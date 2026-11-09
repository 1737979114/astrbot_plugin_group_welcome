# 入群欢迎 · 安装说明

## 1. 安装（二选一）

- **方式 A（推荐）**：AstrBot 后台 →「插件」→「从本地上传 / 安装插件」→ 选择本 zip → 安装完成后重载插件。
- **方式 B**：把 zip 解压到 `<AstrBot>/data/plugins/astrbot_plugin_group_welcome/`，确认 `metadata.yaml` 在该目录根下，然后回后台重载插件。

装完在插件列表里能看到「入群欢迎」，日志出现 `[welcome] 已加载 | 启用=True ...` 即为成功。

## 2. 环境要求

- AstrBot `>=4.5.0,<5`
- **无第三方依赖**，不需要 `pip install`，也不用装数据库
- 平台：OneBot（NapCat / Lagrange 等）、QQ 官方机器人、Telegram、Discord、企业微信、飞书、钉钉、Slack、KOOK、Satori、Misskey、LINE

## 3. 平台差异（重要）

| 平台类型 | 触发方式 |
|---|---|
| OneBot（aiocqhttp） | 真正的进群通知 `group_increase`，进群即欢迎 |
| QQ 官方机器人、Telegram、Discord 等 | 拿不到统一的入群事件，改为**本群第一次发言**时欢迎一次 |

兜底模式由 `first_message_welcome` 控制（默认开）。每个群第一个发言的新面孔只登记、不欢迎，从第二个开始正常触发，避免一装插件就把群里老成员全欢迎一遍。

## 4. 安装后建议改的配置

到 AstrBot 后台 →「插件」→「入群欢迎」→「设置」：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `templates` | 3 条内置模板 | 欢迎语模板，随机挑一条；支持 `{nickname}` `{qq}` `{group_id}` `{group_name}` |
| `at_member` | 开 | 欢迎时 @ 新成员（部分平台不支持 @，会自动退回纯文本） |
| `delay_seconds` | 2 | 延迟几秒再发，更像真人；别超过 10 秒 |
| `cooldown_seconds` | 300 | 同一人重复欢迎冷却，防止协议端重复推送连发 |
| `use_llm` | 关 | 用 AI 生成欢迎语。**开启后要填 `llm_provider_id`**，填你自己 AstrBot 里的模型服务商 ID；留空则用当前会话默认模型，再没有就回退模板 |
| `max_length` | 120 | 欢迎语最大长度 |
| `skip_invite` | 关 | 管理员邀请入群时不欢迎，避免批量拉人刷屏 |
| `whitelist_groups` / `blacklist_groups` | 空 | 群号名单，黑名单优先；白名单为空表示所有群生效 |

**不要直接复制别人发来的 `data/config/astrbot_plugin_group_welcome_config.json`**：那里面是对方本机的模型服务商 ID，搬过来会指向不存在的服务。让 AstrBot 自己生成默认配置最省事。

## 5. 数据文件

- `data/plugin_data/astrbot_plugin_group_welcome/seen.json`：记录每个群已出现过的成员，用于「首次发言」兜底时判断谁是新面孔。
- 删掉它 = 重新建立基线（每个群的第一个发言人不会被欢迎）。

## 6. 不触发怎么排查

1. 确认插件已启用，当前群不在黑名单里（白名单非空时必须在白名单内）。
2. 看日志有没有 `[welcome] 已加载`；没有就是没加载，先重载插件。
3. OneBot 下没有任何进群日志，说明协议端没推 `group_increase`，检查 NapCat/Lagrange 的连接与群通知设置。
4. 同一个人刚被欢迎过会进冷却（默认 300 秒），换号或等几分钟再测。
5. 改完配置要**重载插件**才生效（配置在初始化时读取）。

## 7. 许可

MIT，见 `LICENSE`。
