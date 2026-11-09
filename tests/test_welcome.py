# -*- coding: utf-8 -*-
"""入群欢迎插件的离线测试。

运行：python -m unittest discover -s tests -v
"""

import asyncio
import importlib.util
import logging
import random
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

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


# --------------------------------------------------------------------------
# 假 astrbot 环境
# --------------------------------------------------------------------------


class FakeAt:
    def __init__(self, qq=None):
        self.qq = qq


class FakePlain:
    def __init__(self, text=""):
        self.text = text


def _ensure_astrbot() -> None:
    if "astrbot" in sys.modules:
        api = sys.modules.get("astrbot.api")
        if api is not None and "astrbot.api.message_components" not in sys.modules:
            mc = types.ModuleType("astrbot.api.message_components")
            mc.At = FakeAt
            mc.Plain = FakePlain
            api.message_components = mc
            sys.modules["astrbot.api.message_components"] = mc
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("fake.astrbot")
    api.AstrBotConfig = dict

    event_mod = types.ModuleType("astrbot.api.event")

    class _EventMessageType:
        ALL = "all"
        GROUP_MESSAGE = "group"
        PRIVATE_MESSAGE = "private"

    class _Filter:
        EventMessageType = _EventMessageType

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class AstrMessageEvent:
        pass

    class MessageEventResult:
        pass

    event_mod.filter = _Filter
    event_mod.EventMessageType = _EventMessageType
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = MessageEventResult

    star_mod = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context=None):
            self.context = context

    class Context:
        pass

    star_mod.Star = Star
    star_mod.Context = Context

    mc = types.ModuleType("astrbot.api.message_components")
    mc.At = FakeAt
    mc.Plain = FakePlain

    api.event = event_mod
    api.star = star_mod
    api.message_components = mc
    astrbot.api = api

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = mc


_ensure_astrbot()

_spec = importlib.util.spec_from_file_location(
    "welcome_plugin_under_test", PLUGIN_DIR / "main.py"
)
plugin_main = importlib.util.module_from_spec(_spec)
sys.modules["welcome_plugin_under_test"] = plugin_main
_spec.loader.exec_module(plugin_main)


# --------------------------------------------------------------------------
# 假事件 / 假 bot
# --------------------------------------------------------------------------


class FakeBot:
    def __init__(self, card="小明", group_name="测试群", fail=False):
        self.card = card
        self.group_name = group_name
        self.fail = fail
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.fail:
            raise RuntimeError("api boom")
        if action == "get_group_member_info":
            return {"card": self.card, "nickname": "xm"}
        if action == "get_group_info":
            return {"group_name": self.group_name}
        return {}


class FakeContext:
    def __init__(self, text="AI 写的欢迎语", fail=False, provider="prov-1"):
        self.text = text
        self.fail = fail
        self.provider = provider
        self.prompts = []

    async def get_current_chat_provider_id(self, umo=None):
        return self.provider

    async def llm_generate(self, chat_provider_id=None, prompt=None):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("llm boom")
        return SimpleNamespace(completion_text=self.text)


class FakeEvent:
    def __init__(self, raw, *, group_id="123", user_id="456", bot=None):
        self.message_obj = SimpleNamespace(raw_message=raw)
        self.unified_msg_origin = f"test:GroupMessage:{group_id}"
        self.bot = bot
        self._group = group_id
        self._user = user_id
        self.stopped = False

    def get_group_id(self):
        return self._group

    def get_sender_id(self):
        return self._user

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return ("plain", text)

    def chain_result(self, chain):
        return ("chain", chain)


async def collect(plugin, event):
    return [item async for item in plugin._handle(event)]


def increase(group_id="123", user_id="456", sub_type="approve"):
    return {
        "post_type": "notice",
        "notice_type": "group_increase",
        "group_id": int(group_id),
        "user_id": int(user_id),
        "sub_type": sub_type,
    }


def make_plugin(**overrides):
    config = {
        "enable": True,
        "templates": ["欢迎 {nickname} 加入本群"],
        "at_member": False,
        "delay_seconds": 0,
        "cooldown_seconds": 0,
        "skip_invite": False,
        "use_llm": False,
        "max_length": 120,
        "whitelist_groups": [],
        "blacklist_groups": [],
    }
    config.update(overrides)
    return plugin_main.GroupWelcomePlugin(context=FakeContext(), config=config)


# --------------------------------------------------------------------------
# 纯逻辑
# --------------------------------------------------------------------------


class TestRender(unittest.TestCase):
    def test_known_placeholder(self):
        self.assertEqual(
            render_template("欢迎 {nickname}", {"nickname": "小明"}), "欢迎 小明"
        )

    def test_multiple_placeholders(self):
        text = render_template(
            "{nickname}({qq}) 来到 {group_name}",
            {"nickname": "小明", "qq": "456", "group_name": "测试群"},
        )
        self.assertEqual(text, "小明(456) 来到 测试群")

    def test_unknown_placeholder_kept(self):
        self.assertEqual(render_template("欢迎 {unknown}", {}), "欢迎 {unknown}")

    def test_none_value_kept(self):
        self.assertEqual(
            render_template("{group_name}", {"group_name": None}), "{group_name}"
        )

    def test_empty_template(self):
        self.assertEqual(render_template("", {"a": 1}), "")


class TestTemplates(unittest.TestCase):
    def test_pick_from_list(self):
        self.assertIn(pick_template(["a", "b"], random.Random(3)), ("a", "b"))

    def test_empty_falls_back(self):
        self.assertIn(pick_template([]), DEFAULT_TEMPLATES)

    def test_blank_filtered(self):
        self.assertEqual(pick_template(["", "  ", "only"]), "only")

    def test_none_falls_back(self):
        self.assertIn(pick_template(None), DEFAULT_TEMPLATES)


class TestGroupList(unittest.TestCase):
    def test_parse_int_list(self):
        self.assertEqual(parse_id_list([123, "456"]), {"123", "456"})

    def test_parse_comma_string(self):
        self.assertEqual(parse_id_list("123, 456 789"), {"123", "456", "789"})

    def test_parse_none(self):
        self.assertEqual(parse_id_list(None), set())

    def test_whitelist_only(self):
        self.assertTrue(is_group_allowed("1", {"1"}, set()))
        self.assertFalse(is_group_allowed("2", {"1"}, set()))

    def test_blacklist_priority(self):
        self.assertFalse(is_group_allowed("1", {"1"}, {"1"}))

    def test_empty_whitelist_means_all(self):
        self.assertTrue(is_group_allowed("999", set(), set()))

    def test_empty_group_id(self):
        self.assertFalse(is_group_allowed("", set(), set()))


class TestCooldown(unittest.TestCase):
    def test_second_blocked(self):
        cache = {}
        self.assertTrue(allow_welcome("k", cache, 100.0, 300))
        self.assertFalse(allow_welcome("k", cache, 200.0, 300))
        self.assertTrue(allow_welcome("k", cache, 500.0, 300))

    def test_zero_cooldown(self):
        cache = {}
        self.assertTrue(allow_welcome("k", cache, 1.0, 0))
        self.assertTrue(allow_welcome("k", cache, 1.0, 0))

    def test_different_keys(self):
        cache = {}
        self.assertTrue(allow_welcome("a", cache, 1.0, 300))
        self.assertTrue(allow_welcome("b", cache, 1.0, 300))

    def test_prune(self):
        cache = {str(i): 0.0 for i in range(100)}
        prune_cache(cache, 10_000.0, 600)
        self.assertEqual(cache, {})


class TestText(unittest.TestCase):
    def test_truncate_short(self):
        self.assertEqual(truncate("abc", 10), "abc")

    def test_truncate_long(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")

    def test_truncate_zero_means_unlimited(self):
        self.assertEqual(truncate("abcdef", 0), "abcdef")

    def test_nickname_card(self):
        self.assertEqual(clean_nickname("小明", "456"), "小明")

    def test_nickname_is_id(self):
        self.assertEqual(clean_nickname("456", "456"), "新朋友")

    def test_nickname_empty(self):
        self.assertEqual(clean_nickname("", "456"), "新朋友")


# --------------------------------------------------------------------------
# 事件流
# --------------------------------------------------------------------------


class TestGroupIncrease(unittest.TestCase):
    def test_welcome_sent(self):
        plugin = make_plugin()
        event = FakeEvent(increase(), bot=FakeBot())

        out = asyncio.run(collect(plugin, event))

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "plain")
        self.assertIn("小明", out[0][1])
        self.assertTrue(event.stopped, "命中后应停止事件传播")

    def test_normal_message_ignored(self):
        plugin = make_plugin()
        event = FakeEvent({"post_type": "message", "message": "hello"}, bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])
        self.assertFalse(event.stopped)

    def test_other_notice_ignored(self):
        plugin = make_plugin()
        raw = {"post_type": "notice", "notice_type": "group_decrease", "group_id": 123}
        event = FakeEvent(raw, bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])

    def test_disabled(self):
        plugin = make_plugin(enable=False)
        event = FakeEvent(increase(), bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])

    def test_blacklist(self):
        plugin = make_plugin(blacklist_groups=["123"])
        event = FakeEvent(increase(), bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])

    def test_whitelist_miss(self):
        plugin = make_plugin(whitelist_groups=["999"])
        event = FakeEvent(increase(), bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])

    def test_whitelist_hit(self):
        plugin = make_plugin(whitelist_groups=["123"])
        event = FakeEvent(increase(), bot=FakeBot())

        self.assertEqual(len(asyncio.run(collect(plugin, event))), 1)

    def test_cooldown_blocks_duplicate(self):
        plugin = make_plugin(cooldown_seconds=300)
        first = FakeEvent(increase(), bot=FakeBot())
        second = FakeEvent(increase(), bot=FakeBot())

        self.assertEqual(len(asyncio.run(collect(plugin, first))), 1)
        self.assertEqual(asyncio.run(collect(plugin, second)), [])

    def test_skip_invite(self):
        plugin = make_plugin(skip_invite=True)
        event = FakeEvent(increase(sub_type="invite"), bot=FakeBot())

        self.assertEqual(asyncio.run(collect(plugin, event)), [])

    def test_at_member(self):
        plugin = make_plugin(at_member=True)
        event = FakeEvent(increase(), bot=FakeBot())

        out = asyncio.run(collect(plugin, event))

        self.assertEqual(out[0][0], "chain")
        chain = out[0][1]
        self.assertEqual(chain[0].qq, "456")
        self.assertTrue(chain[1].text.startswith(" "))

    def test_group_name_placeholder(self):
        plugin = make_plugin(templates=["欢迎 {nickname} 来到 {group_name}"])
        bot = FakeBot(group_name="摸鱼群")
        event = FakeEvent(increase(), bot=bot)

        out = asyncio.run(collect(plugin, event))

        self.assertIn("摸鱼群", out[0][1])
        self.assertIn("get_group_info", [call[0] for call in bot.calls])

    def test_bot_api_failure_falls_back(self):
        """拿不到昵称时用「新朋友」，不能报错。"""
        plugin = make_plugin()
        event = FakeEvent(increase(), bot=FakeBot(fail=True))

        out = asyncio.run(collect(plugin, event))

        self.assertEqual(len(out), 1)
        self.assertIn("新朋友", out[0][1])

    def test_no_bot(self):
        plugin = make_plugin()
        event = FakeEvent(increase(), bot=None)

        out = asyncio.run(collect(plugin, event))

        self.assertEqual(len(out), 1)
        self.assertIn("新朋友", out[0][1])

    def test_empty_templates_use_default(self):
        plugin = make_plugin(templates=[])
        event = FakeEvent(increase(), bot=FakeBot())

        out = asyncio.run(collect(plugin, event))

        self.assertEqual(len(out), 1)
        self.assertTrue(len(out[0][1]) > 0)

    def test_max_length(self):
        plugin = make_plugin(templates=["很长" * 100], max_length=20)
        event = FakeEvent(increase(), bot=FakeBot())

        out = asyncio.run(collect(plugin, event))

        self.assertLessEqual(len(out[0][1]), 20)


class TestLLMWelcome(unittest.TestCase):
    def test_llm_used(self):
        plugin = make_plugin(use_llm=True)
        context = FakeContext(text="小明来啦，欢迎欢迎")
        plugin.context = context

        out = asyncio.run(collect(plugin, FakeEvent(increase(), bot=FakeBot())))

        self.assertEqual(out[0][1], "小明来啦，欢迎欢迎")
        self.assertEqual(len(context.prompts), 1)
        self.assertIn("小明", context.prompts[0])

    def test_llm_failure_falls_back(self):
        plugin = make_plugin(use_llm=True, templates=["模板欢迎 {nickname}"])
        plugin.context = FakeContext(fail=True)

        out = asyncio.run(collect(plugin, FakeEvent(increase(), bot=FakeBot())))

        self.assertIn("模板欢迎", out[0][1])

    def test_no_provider_falls_back(self):
        plugin = make_plugin(use_llm=True, templates=["模板欢迎 {nickname}"])
        plugin.context = FakeContext(provider="")

        out = asyncio.run(collect(plugin, FakeEvent(increase(), bot=FakeBot())))

        self.assertIn("模板欢迎", out[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
