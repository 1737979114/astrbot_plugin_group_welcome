# -*- coding: utf-8 -*-
"""astrbot_plugin_group_welcome

有人进群时自动发欢迎语。

链路::

    OneBot notice(group_increase)
        -> AstrBot 包装成 GROUP_MESSAGE 事件（raw_message 保留原始 payload）
        -> 过滤 post_type / notice_type
        -> 名单检查、重复欢迎冷却
        -> 取昵称（get_group_member_info）
        -> 模板渲染 或 AI 生成
        -> 可选 @ 新成员 + 延迟发送

只处理进群通知，不拦截普通群消息。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:  # pragma: no cover
    get_astrbot_plugin_data_path = None  # type: ignore

try:  # 旧版本可能没有导出 AstrBotConfig
    from astrbot.api import AstrBotConfig
except Exception:  # pragma: no cover
    AstrBotConfig = dict  # type: ignore

# @ 新成员需要消息组件，不同版本路径略有差异
try:
    from astrbot.api.message_components import At, Plain

    HAS_COMPONENTS = True
except Exception:  # pragma: no cover
    try:
        from astrbot.core.message.components import At, Plain

        HAS_COMPONENTS = True
    except Exception:
        At = Plain = None  # type: ignore
        HAS_COMPONENTS = False

# 允许同目录的 welcome_core 被导入
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from welcome_core import (  # noqa: E402
    DEFAULT_TEMPLATES,
    allow_welcome,
    clean_nickname,
    is_group_allowed,
    parse_id_list,
    pick_template,
    prune_cache,
    render_template,
    truncate,
)

PLUGIN_NAME = "astrbot_plugin_group_welcome"

DEFAULT_LLM_PROMPT = (
    "你在群聊里欢迎一位刚进群的新成员。用一句自然、热情的中文欢迎他，"
    "20 到 40 字，不要 emoji，不要罗列群规，不要反问。"
)

#: 群名缓存有效期（秒）
GROUP_NAME_TTL = 3600

#: 常见裸命令：首次发言就是这些的话，只记录不欢迎
BARE_COMMANDS = {
    "签到", "打卡", "上班", "下班", "出题", "我的积分", "积分", "积分中心",
    "积分明细", "积分排行", "兑换列表", "兑换记录", "成员序号", "团队列表",
    "友谊签到", "恋爱签到", "工时", "工时榜", "补签", "身份码",
}


class GroupWelcomePlugin(Star):
    """进群自动欢迎。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)

        self.config = config if config is not None else {}

        self.enable = self._cfg_bool("enable", True)
        self.templates = self._cfg_list("templates") or list(DEFAULT_TEMPLATES)
        self.at_member = self._cfg_bool("at_member", True)
        self.delay_seconds = self._cfg_int("delay_seconds", 2, minimum=0, maximum=30)
        self.cooldown_seconds = self._cfg_int(
            "cooldown_seconds", 300, minimum=0, maximum=86400
        )
        self.skip_invite = self._cfg_bool("skip_invite", False)

        self.use_llm = self._cfg_bool("use_llm", False)
        self.llm_provider_id = str(self._cfg("llm_provider_id", "") or "").strip()
        self.llm_prompt = str(self._cfg("llm_prompt", "") or "").strip()
        self.max_length = self._cfg_int("max_length", 120, minimum=10, maximum=500)

        self.whitelist = parse_id_list(self._cfg("whitelist_groups", []))
        self.blacklist = parse_id_list(self._cfg("blacklist_groups", []))

        # QQ 官方机器人没有「成员入群」事件，用「首次发言」兜底欢迎
        self.first_message_welcome = self._cfg_bool("first_message_welcome", True)

        # 运行时状态
        self._last_welcome: dict[str, float] = {}
        self._seen: dict[str, set[str]] = {}
        self._seen_path = self._resolve_seen_path()
        self._seen_dirty = False
        self._load_seen()
        self._group_names: dict[str, tuple[str, float]] = {}
        self._rng = random.Random()

        logger.info(
            "[welcome] 已加载 | 启用=%s | 模板数=%s | @新成员=%s | AI=%s | 白名单=%s | 黑名单=%s",
            self.enable,
            len(self.templates),
            self.at_member,
            self.use_llm,
            len(self.whitelist),
            len(self.blacklist),
        )

    # ------------------------------------------------------------------ 已见成员

    def _resolve_seen_path(self) -> Path | None:
        try:
            if get_astrbot_plugin_data_path is None:
                return None
            base = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
            base.mkdir(parents=True, exist_ok=True)
            return base / "seen.json"
        except Exception:
            return None

    def _load_seen(self) -> None:
        """加载「已见过」名单。

        没有记录时按空基线启动：某个群第一次出现新人只登记、不欢迎，
        从第二个新人开始正常欢迎，避免刚装插件就把群里的老成员全欢迎一遍。
        """
        if self._seen_path is None:
            return
        try:
            if self._seen_path.is_file():
                data = json.loads(self._seen_path.read_text(encoding="utf-8"))
                for gid, ids in (data.get("groups") or {}).items():
                    self._seen[str(gid)] = {str(x) for x in ids}
                logger.info(f"[welcome] 已加载 {len(self._seen)} 个群的成员基线")
        except Exception as exc:
            logger.warning(f"[welcome] 读取 seen.json 失败：{exc}")

    def _save_seen(self) -> None:
        if self._seen_path is None or not self._seen_dirty:
            return
        try:
            data = {"groups": {g: sorted(ids) for g, ids in self._seen.items()}}
            self._seen_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            self._seen_dirty = False
        except Exception as exc:
            logger.warning(f"[welcome] 保存 seen.json 失败：{exc}")

    def _mark_seen(self, group_id: str, user_id: str) -> bool:
        """记下这个人已出现；返回 True 表示此前没见过（= 新人）。"""
        bucket = self._seen.get(group_id)
        if bucket is None:
            # 这个群没有基线，先记录不欢迎，避免误伤
            self._seen[group_id] = {user_id}
            self._seen_dirty = True
            self._save_seen()
            return False
        if user_id in bucket:
            return False
        bucket.add(user_id)
        self._seen_dirty = True
        self._save_seen()
        return True

    # ------------------------------------------------------------------ 配置

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "开")
        return bool(value)

    def _cfg_int(
        self, key: str, default: int, minimum: int = 0, maximum: int = 10**6
    ) -> int:
        try:
            value = int(self._cfg(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _cfg_list(self, key: str) -> list[str]:
        value = self._cfg(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    # ------------------------------------------------------------------ 事件

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_event(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """监听群事件，只接管「成员进群」这一类通知。"""
        try:
            async for result in self._handle(event):
                yield result
        except Exception as exc:  # pragma: no cover
            logger.exception(f"[welcome] 处理进群事件时出现未捕获异常：{exc}")

    async def _handle(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not self.enable:
            return

        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)

        # 只认 OneBot 的 notice / group_increase
        if raw is None or self._raw_get(raw, "post_type") != "notice":
            # QQ 官方机器人拿不到入群事件，用「首次发言」兜底
            async for result in self._handle_first_message(event):
                yield result
            return
        if self._raw_get(raw, "notice_type") != "group_increase":
            return

        group_id = str(
            self._raw_get(raw, "group_id", "") or event.get_group_id() or ""
        ).strip()
        user_id = str(
            self._raw_get(raw, "user_id", "") or event.get_sender_id() or ""
        ).strip()
        sub_type = str(self._raw_get(raw, "sub_type", "") or "").strip()

        if not group_id or not user_id:
            return

        if not is_group_allowed(group_id, self.whitelist, self.blacklist):
            return
        if self.skip_invite and sub_type == "invite":
            logger.debug(f"[welcome] 群 {group_id} 为邀请入群，跳过：{user_id}")
            return

        now = time.monotonic()
        if not allow_welcome(
            f"{group_id}:{user_id}", self._last_welcome, now, self.cooldown_seconds
        ):
            logger.debug(f"[welcome] 冷却中，跳过重复欢迎：{group_id}/{user_id}")
            return
        prune_cache(self._last_welcome, now, max(self.cooldown_seconds, 600))

        # 先占住这个事件，避免其他插件重复欢迎
        event.stop_event()

        nickname = await self._member_name(event, group_id, user_id)
        async for result in self._send_welcome(event, group_id, user_id, nickname):
            yield result

    async def _handle_first_message(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """官方机器人兜底：某人第一次在本群发言时，当作「新面孔」欢迎一次。"""
        if not self.first_message_welcome:
            return

        group_id = str(event.get_group_id() or "").strip()
        user_id = str(event.get_sender_id() or "").strip()
        if not group_id or not user_id:
            return

        # 机器人自己、命令消息不欢迎
        self_id = str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")
        if self_id and user_id == self_id:
            return
        text = str(getattr(event, "message_str", "") or "").lstrip()
        if text[:1] in "/!！":
            return
        # 裸命令（不带 /）也不欢迎，避免和玩法回复叠在一起
        if text.strip() in BARE_COMMANDS:
            self._mark_seen(group_id, user_id)
            return

        if not is_group_allowed(group_id, self.whitelist, self.blacklist):
            return

        if not self._mark_seen(group_id, user_id):
            return

        nickname = str(event.get_sender_name() or "").strip() or "新朋友"
        logger.info(f"[welcome] 首次发言欢迎 {nickname}({user_id}) @ {group_id}")
        async for result in self._send_welcome(event, group_id, user_id, nickname):
            yield result

    async def _send_welcome(
        self, event: AstrMessageEvent, group_id: str, user_id: str, nickname: str
    ) -> AsyncGenerator[MessageEventResult, None]:
        """渲染模板（或 AI 生成）并发送欢迎语。"""
        template = pick_template(self.templates, self._rng)
        group_name = ""
        if "{group_name}" in template:
            group_name = await self._group_name(event, group_id)

        values = {
            "nickname": nickname,
            "qq": user_id,
            "user_id": user_id,
            "group_id": group_id,
            "group_name": group_name,
        }

        text = ""
        if self.use_llm:
            text = await self._llm_welcome(event, values)
        if not text:
            text = render_template(template, values)

        text = truncate(text, self.max_length)
        if not text:
            return

        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        logger.info(f"[welcome] 欢迎 {nickname}({user_id}) 进入群 {group_id}")
        yield self._build_result(event, text, user_id)

    # ------------------------------------------------------------------ 数据获取

    @staticmethod
    def _raw_get(raw: Any, key: str, default: Any = None) -> Any:
        """兼容 aiocqhttp 的 Event 对象和普通 dict。"""
        try:
            return raw.get(key, default)
        except Exception:
            return getattr(raw, key, default)

    async def _member_name(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> str:
        """取群名片/昵称，失败时返回「新朋友」。"""
        bot = getattr(event, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            return "新朋友"

        try:
            info = await bot.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=True,
            )
        except Exception as exc:
            logger.debug(f"[welcome] 获取成员信息失败：{exc}")
            return "新朋友"

        if not isinstance(info, dict):
            return "新朋友"

        name = info.get("card") or info.get("nickname")
        return clean_nickname(name, user_id)

    async def _group_name(self, event: AstrMessageEvent, group_id: str) -> str:
        """取群名，带一小时缓存。模板里没写 {group_name} 时不会调用。"""
        now = time.monotonic()
        cached = self._group_names.get(group_id)
        if cached and now - cached[1] < GROUP_NAME_TTL:
            return cached[0]

        bot = getattr(event, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            return ""

        try:
            info = await bot.call_action("get_group_info", group_id=int(group_id))
        except Exception as exc:
            logger.debug(f"[welcome] 获取群信息失败：{exc}")
            return ""

        name = ""
        if isinstance(info, dict):
            name = str(info.get("group_name") or "").strip()
        if name:
            self._group_names[group_id] = (name, now)
        return name

    async def _llm_welcome(
        self, event: AstrMessageEvent, values: dict[str, Any]
    ) -> str:
        """让模型写一句欢迎语，失败返回空串由模板兜底。"""
        try:
            provider_id = self.llm_provider_id
            if not provider_id:
                provider_id = await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            if not provider_id:
                logger.info("[welcome] 没有可用的模型，改用模板")
                return ""

            prompt = self._build_llm_prompt(values)
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            text = str(getattr(response, "completion_text", "") or "").strip()
            return truncate(text, self.max_length)
        except asyncio.TimeoutError:
            logger.warning("[welcome] AI 生成超时，改用模板")
            return ""
        except Exception as exc:
            logger.warning(f"[welcome] AI 生成失败，改用模板：{exc}")
            return ""

    def _build_llm_prompt(self, values: dict[str, Any]) -> str:
        base = self.llm_prompt or DEFAULT_LLM_PROMPT
        return (
            f"{base}\n"
            f"新成员昵称：{values['nickname']}\n"
            f"群号：{values['group_id']}\n"
            f"群名：{values.get('group_name') or '未知'}\n"
            "只输出欢迎语本身，不要加引号、前缀或解释。"
        )

    # ------------------------------------------------------------------ 发送

    def _build_result(
        self, event: AstrMessageEvent, text: str, user_id: str
    ) -> MessageEventResult:
        """需要 @ 新成员时发消息链，否则发纯文本。"""
        if self.at_member and user_id and HAS_COMPONENTS:
            try:
                return event.chain_result([At(qq=user_id), Plain(" " + text)])
            except Exception as exc:  # pragma: no cover
                logger.debug(f"[welcome] 构造 @ 消息失败，退回纯文本：{exc}")
        return event.plain_result(text)

    # ------------------------------------------------------------------ 生命周期

    async def terminate(self) -> None:
        self._last_welcome.clear()
        self._group_names.clear()
        logger.info("[welcome] 插件已卸载")
