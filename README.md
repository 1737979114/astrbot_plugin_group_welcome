# astrbot_plugin_group_welcome

有人进群时自动发欢迎语。支持多套模板随机、@ 新成员、延迟发送、AI 个性化文案、群黑白名单和重复欢迎冷却。

## 1. 结构

```
astrbot_plugin_group_welcome/
├── main.py              # 插件入口：事件识别、昵称获取、发送
├── welcome_core.py      # 纯标准库：模板渲染、名单判断、冷却
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # WebUI 可视化配置
├── README.md
└── tests/
    └── test_welcome.py  # 44 项离线测试
```

## 2. 安装

把整个目录放进 `AstrBot/data/plugins/`，重启 AstrBot，或在 WebUI 插件页上传 zip。

## 3. 它怎么判断「有人进群」

OneBot 协议的进群通知是一个 `notice` 事件，AstrBot 的 aiocqhttp 适配器会把它包装成普通事件（类型 `GROUP_MESSAGE`），原始 payload 保留在 `event.message_obj.raw_message` 里。所以判断逻辑是：

```python
raw.get("post_type") == "notice"
raw.get("notice_type") == "group_increase"
```

这两个条件同时满足才处理，普通群消息、退群通知、戳一戳都不会误触发。命中后会调用 `event.stop_event()`，避免和别的欢迎插件重复发。

进群者信息通过 OneBot 的 `get_group_member_info` 拉取，优先用群名片，其次昵称；拿不到就统一叫「新朋友」，不会把 QQ 号当名字发出去。

微信、Telegram、Discord、QQ 官方机器人等平台没有统一的「入群」事件，这时插件会退一步：**某人在本群第一次发言**时欢迎一次（`first_message_welcome`，默认开）。为了避免刚装插件就把全群老成员欢迎一遍，每个群的第一个「新面孔」只登记不欢迎，从第二个开始正常触发。

## 4. 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `enable` | 开 | 总开关 |
| `first_message_welcome` | 开 | 拿不到入群事件的平台（QQ 官方机器人等）用「首次发言」兜底欢迎；关掉则只认真正的进群通知 |
| `templates` | 3 条内置模板 | 随机选一条发送 |
| `at_member` | 开 | 欢迎时 @ 新成员 |
| `delay_seconds` | 2 | 延迟几秒再发，更像真人 |
| `cooldown_seconds` | 300 | 同一人重复欢迎冷却，防止协议端重复推送导致连发 |
| `use_llm` | 关 | 用 AI 生成欢迎语 |
| `llm_provider_id` | 空 | AI 使用的模型，留空用会话默认 |
| `llm_prompt` | 空 | 自定义提示词 |
| `max_length` | 120 | 欢迎语最大长度，超出截断 |
| `skip_invite` | 关 | 管理员邀请入群时不欢迎，避免批量拉人刷屏 |
| `whitelist_groups` | 空 | 只在这些群生效，留空表示全部 |
| `blacklist_groups` | 空 | 这些群不欢迎，优先级高于白名单 |

## 5. 模板占位符

| 占位符 | 含义 |
|---|---|
| `{nickname}` | 新成员昵称或群名片 |
| `{qq}` / `{user_id}` | 新成员 QQ 号 |
| `{group_id}` | 群号 |
| `{group_name}` | 群名（只有模板里写了才会去查，带一小时缓存） |

示例：

```
欢迎 {nickname} 加入本群！有问题随时问，大家都很乐意帮忙。
{nickname} 来啦，欢迎欢迎，先看下群公告哈。
欢迎 {nickname} 来到 {group_name}，祝你在这里聊得开心。
```

写错的占位符（比如 `{nickename}`）会原样保留，不会被清空，方便自己发现拼写问题。

## 6. AI 欢迎语

开启 `use_llm` 后，插件会带着昵称和群名请求模型写一句 20~40 字的欢迎语。以下情况会自动回退到模板，不会导致新人没人理：

- 没有可用模型（`llm_provider_id` 为空且会话没有默认模型）
- 模型调用超时或报错
- 模型返回空字符串

模型选择建议用响应快的（如 GLM-4-Flash 系列），欢迎语不需要强推理。

## 7. 不触发时怎么排查

1. 确认 `enable` 是开的，且当前群不在黑名单里（白名单非空时必须在白名单内）。
2. 看日志里有没有 `[welcome] 已加载 | 启用=True ...`，确认插件确实加载了。
3. 如果日志里连进群事件都没有，说明协议端没推送 `group_increase`，检查 NapCat 的连接和群通知设置。
4. 同一人刚被欢迎过会进冷却（默认 300 秒），换个号或等几分钟测试。
5. 改了配置后需要**重载插件**才生效，因为配置是在初始化时读取的。

## 8. 测试

```bash
cd astrbot_plugin_group_welcome
python -m unittest discover -s tests -v
```

覆盖：模板渲染（含未知占位符、空值）、模板随机与兜底、群黑白名单组合、重复欢迎冷却、文案截断、昵称兜底、进群/退群/普通消息的区分、@ 消息链、AI 成功与失败回退。

## 9. 许可

MIT
