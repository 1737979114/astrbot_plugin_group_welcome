# -*- coding: utf-8 -*-
"""入群欢迎的纯逻辑层。

只依赖标准库，不导入 AstrBot，方便单独跑单元测试。
包含：模板渲染、模板随机、群名单判断、欢迎冷却、文案截断。
"""

from __future__ import annotations

import random
import re
from typing import Any, Iterable

#: 占位符形如 {nickname}，只认字母数字下划线，避免把正文里的花括号误替换
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

#: 用户没有配置模板时的兜底文案
DEFAULT_TEMPLATES: tuple[str, ...] = (
    "欢迎 {nickname} 加入本群！有问题随时问，大家都很乐意帮忙。",
    "{nickname} 来啦，欢迎欢迎，先看下群公告哈。",
    "欢迎新朋友 {nickname}，祝你在这里聊得开心。",
)


def normalize_id(value: Any) -> str:
    """统一 ID 格式，兼容 int / str。"""
    return str(value if value is not None else "").strip()


def parse_id_list(values: Any) -> set[str]:
    """把配置里的群号名单统一成字符串集合。

    兼容三种写法：``[123, "456"]``、``"123,456"``、``"123 456"``。
    """
    if values is None:
        return set()

    if isinstance(values, str):
        parts: list[Any] = re.split(r"[,，\s]+", values)
    elif isinstance(values, (list, tuple, set)):
        parts = []
        for item in values:
            if isinstance(item, str):
                parts.extend(re.split(r"[,，\s]+", item))
            else:
                parts.append(item)
    else:
        parts = [values]

    return {normalize_id(part) for part in parts if normalize_id(part)}


def is_group_allowed(
    group_id: str,
    whitelist: set[str],
    blacklist: set[str],
) -> bool:
    """黑名单优先于白名单；白名单为空表示全部群生效。"""
    gid = normalize_id(group_id)
    if not gid:
        return False
    if gid in blacklist:
        return False
    if whitelist and gid not in whitelist:
        return False
    return True


def pick_template(
    templates: Iterable[str] | None,
    rng: random.Random | None = None,
) -> str:
    """随机挑一条模板；配置为空时用内置兜底模板。"""
    items = [str(item).strip() for item in (templates or []) if str(item).strip()]
    if not items:
        items = list(DEFAULT_TEMPLATES)
    return (rng or random).choice(items)


def render_template(template: str, values: dict[str, Any]) -> str:
    """替换 ``{key}`` 占位符。

    未知占位符原样保留，避免用户写错变量名时整句被清空。
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values and values[key] is not None:
            return str(values[key])
        return match.group(0)

    return PLACEHOLDER_RE.sub(replace, template or "").strip()


def truncate(text: str, max_length: int) -> str:
    """按字符数截断，超出时补省略号。"""
    value = (text or "").strip()
    if max_length <= 0 or len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "…"


def clean_nickname(name: Any, fallback_id: str) -> str:
    """清洗昵称。

    很多适配器在 notice 事件里把 nickname 直接填成 QQ 号，
    这种情况按"没拿到昵称"处理，统一叫"新朋友"，免得欢迎语里出现一串数字。
    """
    text = str(name or "").strip()
    if not text or text == normalize_id(fallback_id):
        return "新朋友"
    return text


def allow_welcome(
    key: str,
    last_seen: dict[str, float],
    now: float,
    cooldown: float,
) -> bool:
    """重复欢迎冷却：同一人短时间内多次触发只欢迎一次。"""
    if cooldown <= 0:
        return True

    last = last_seen.get(key)
    if last is not None and now - last < cooldown:
        return False

    last_seen[key] = now
    return True


def prune_cache(cache: dict[str, float], now: float, ttl: float) -> None:
    """清理过期记录，避免长期运行后字典无限增长。"""
    if len(cache) <= 64:
        return
    for key, timestamp in list(cache.items()):
        if now - timestamp > ttl:
            cache.pop(key, None)
