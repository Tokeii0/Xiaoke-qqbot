# 配置指南

首次安装步骤见 [README](../README.md)。环境变量模板为 [`.env.example`](../.env.example)，网关模板为 [`tdai-gateway.example.json`](../tdai-gateway.example.json)。实际配置文件均保留在本机。

## 配置从哪里生效

启动器先保留进程已有环境变量，再读取 `.env`，最后读取 `.env.<ENVIRONMENT>`（默认 `.env.prod`），读取时不会覆盖已经存在的环境变量。因此 `.env.prod` **不会覆盖 `.env` 中已有的同名值**。

机器人在启动环境的基础上加载 `data/bot_config.json` 和 `data/bot_secrets.json`。后台保存的字段优先于对应环境默认值；编辑 `.env` 后发现没有变化时，应检查后台是否已经保存过该字段。

| 修改内容 | 生效方式 |
| --- | --- |
| 对话模型、人设、群白名单、JEV 子开关、日程等可编辑字段 | 后台保存后使用新配置 |
| 后台保存的模型 Key | 写入本地秘密文件，页面只显示配置状态 |
| `HOST`、`PORT`、OneBot / Web 后台令牌、`TYPESAFE_API_KEY` | 修改环境后重启机器人 |
| 网关地址、鉴权、提取模型和网关 JSON | 重启对应服务；已独立运行的网关不会随机器人自动重新配置 |

即使在后台改了主模型，记忆网关也不会自动同步：`TDAI_LLM_*` 留空时只在启动时从环境中的 `BOT_*` 取值。

## 基础聊天与权限

先填写自己的 `BOT_SUPERUSERS`、`BOT_ALLOWED_GROUPS`，并使 `SUPERUSERS` 与超管列表一致。空白名单不允许任何群。群管理可使用管理命令，但不能借此越过群白名单；普通用户私聊不开放。

`BOT_ALLOW_SUPERUSER_PRIVATE_CHAT` 控制超管是否可以私聊聊天。未开启时，超管仍保留私聊管理命令入口。

主模型基础地址填到 API 根路径，例如 `https://api.openai.com/v1`，客户端会追加 `/chat/completions`。模型名和参数需要供应商实际支持。备用模型失败时也会保留可诊断的错误信息，不能保证所有“兼容接口”支持相同扩展参数。

## JEV 与功能开关

安装项目已包含 `typesafe-sdk`。设置 `TYPESAFE_API_KEY` 并重启后，在后台启用 JEV；默认模型配置为 `jev-latest`。

| 功能 | 主要开关 |
| --- | --- |
| 参与判断 | `BOT_JEV_ENABLED` + `BOT_JEV_GATE_ENABLED` |
| 场景、情绪和短答 | `BOT_JEV_SCENE_ENABLED` |
| 连续接话 | `BOT_JEV_CONTINUITY_ENABLED` |
| 本地事实筛选与修正 | `BOT_JEV_MEMORY_ENABLED` |
| 未解决事项与相关追问 | `BOT_JEV_FOLLOWUP_ENABLED` |
| 自然语言提醒与整理 | `BOT_JEV_TOOLS_ENABLED` |
| 群知识 | `BOT_JEV_KNOWLEDGE_ENABLED` |
| 自然语言纠错与回复偏好 | `BOT_JEV_FEEDBACK_ENABLED` |
| 语音适用性 / 风格 | `BOT_VOICE_JEV_GATE_ENABLED` / `BOT_VOICE_JEV_ENABLED` |

这些 JEV 子功能需要总开关。原有关键词、@ 和概率回复规则可以在不开 JEV 时使用；语义功能会按各自逻辑跳过或回退。语音闸门开启但判断不可用时优先文字，手误判断不可用时保留原文。

建议先在测试群开启参与判断与场景判断，观察记录后再增加记忆、跟进及主动发言。无人点名时的主动参与可使用 `BOT_PROACTIVE_*` 的静默时间、冷却和小时 / 每日预算。

## 可选接口

| 功能 | 接口 / 条件 | 配置入口 |
| --- | --- | --- |
| 主对话与备用 | `/chat/completions`，使用供应商实际可用的模型 | `BOT_*`、`BOT_FALLBACK_*` |
| 直接图片上下文 | 主模型支持 `image_url` 多模态消息；备用模型也需兼容 | `BOT_VISION_ENABLED=true`、`BOT_VISION_MODE=direct` |
| 独立识图 | 单独调用视觉模型，描述回填给主模型 | `BOT_VISION_MODE=separate`、`BOT_VISION_MODEL` 等 |
| QQ 语音 | 支持 `/live/sessions` 的 WebSocket 服务 | `BOT_VOICE_*` 与后台语音页 |
| 校园配图 | 支持 `/images/generations`、JPEG 输出及指定尺寸的模型 | `BOT_ROUTINE_PHOTO_*` 与校园日常页 |
| 联网 | Tavily Search API；机器人用 JEV 判定检索时机 | `BOT_SEARCH_*`，独立搜索 Key |

### 语音

当前代码默认模型字符串是 `gpt-live-1`，默认音色是 `marin`。这是客户端配置，使用前要确认服务商向你的账号开放了该模型和 Live 协议。普通 `/audio/speech` TTS 与其他实时音频协议不能只改模型名后直接接入。

可以在后台填写甜美、温柔等基础风格并试听，再启用 JEV 动态风格。`BOT_VOICE_SEND_TEXT` 控制是否同时发送正文，程序不会增加合成语音标签。超长回复、代码等不适合语音的内容会保留文字，失败时也回退文字。

### 校园日常与配图

开启 `BOT_ROUTINE_ENABLED` 后，工作日 / 周末安排在后台编辑。`BOT_CONTEXT_TIMEZONE` 影响日程；更改时区时也应检查网关模板中的 `memory.timezone`。

配图另需 `BOT_ROUTINE_PHOTO_ENABLED`。当前默认模型字符串是 `gpt-image-2.5-flare`，同样需要供应商实际支持，项目不保证这个默认值对所有账号可用。

质量、比例、校园设定和冷却在后台设置：默认 `medium`；横向 4:3 请求 `1024x768`，竖向 3:4 请求 `768x1024`，自动模式结合事件选择。默认 JEV 配图阈值为 `0.75`，可使用 `BOT_ROUTINE_PHOTO_THRESHOLD` 或后台调整。图片模型必须接受这些原生尺寸，不能仅凭“支持生图”判断兼容性。

校园配图 Key 留空时，仅在基础地址相同时尝试复用语音或主对话 Key；独立服务建议单独配置。每个事件的标准图片保存在日常数据库中，删除相应数据会失去原图复用能力。

## 记忆与数据范围

`npm ci` 安装官方 `@tencentdb-agent-memory/memory-tencentdb`。默认使用本地 SQLite 与 BM25，无需购买 VectorDB；开启记忆提取仍会调用配置的语言模型。

复制网关模板后，SQLite 使用 `TDAI_STORE_BACKEND=sqlite`；接入腾讯云 VectorDB 时改为 `tcvdb`，填写 `TDAI_TCVDB_*`，并按上游要求配置匹配的 embedding。`TDAI_EMBEDDING_PROVIDER=none` 表示不使用 embedding 服务。具体数据库能力以安装版本的上游文档为准。

本地历史、知识和偏好通过群或私聊会话区分，个人记录还带成员归属。网关调用传入会话键与 `qq-<用户号>`；其内部召回范围由上游实现和部署决定。

`/小可 记忆` 使用的 `/search/memories` 管理检索没有群范围，因此**只允许超管在私聊中使用**；即使超管在群里调用，也不会返回全局结果。共享网关的常规召回范围仍取决于上游，不应被当作互不信任群之间的严格隔离边界。需要隔离时使用独立机器人实例、独立网关和独立数据目录，或先扩展并验证服务端范围过滤。

自然语言“别记住”修改的是本地确认事实；它不是对所有数据库、上游副本和备份的全量删除接口。

| 私有路径 | 内容 |
| --- | --- |
| `data/bot_config.json`、`data/bot_secrets.json` | 后台设置、私人提示词和密钥；配置文件也可能包含 Webhook 令牌 |
| `data/chat_history.db` | 最近对话及图片信息 |
| `data/intelligence.db` | 本地事实、知识、偏好、事项和提醒 |
| `data/tencentdb-memory/` | 网关采集和长期记忆 |
| `data/member_profiles.db`、`data/bot_state.db` | 成员互动画像、情绪与关系状态 |
| `data/campus_photo_state.json`、`data/campus_photo_state.diary.db` | 配图状态、日常细节、起床理由和标准图片 |
| `data/request_log.db`、`data/judge_log.db`、`data/logs/` | 模型请求、JEV 记录和运行日志 |
| `data/summaries.db`、`data/summaries/` | 总结用聊天资料、生成网页与截图 |

路径可由相关环境变量改写；如改到 `data/` 以外，需自行将新路径加入忽略规则。备份时应同时保存配置与所需数据库；SQLite 正在写入时使用备份 API 或先停服务，避免只复制主文件而遗漏 WAL。

## 其他集成

- **聊天总结**：启用后收集允许群的消息，可定时生成并发送图片。截图需要系统中可用的 Edge / Chrome；`BOT_SUMMARY_BROWSER` 可指定可执行文件。Linux / macOS 不应假定能自动找到浏览器。
- **入群审核**：默认关闭，接口地址与目标群为空。按产品分组返回 `users`，每条含 `qq`、`lifetimeLicenseCount`、`totalLicenseCount`；两项数量都满足阈值才放行。它是可选适配器，不依赖某个私人授权网站。
- **Webhook**：启用后向 `/webhook/<令牌>` POST，填写目标群及模板。令牌属于秘密；反向代理访问日志也可能记录 URL 中的令牌。
- **远程后台**：默认仅本机登录。远程部署可用 SSH 本地端口转发，例如 `ssh -L 8080:127.0.0.1:8080 user@server` 后在本机访问后台；替换为自己的服务器信息。

## 常见问题

| 现象 | 先检查 |
| --- | --- |
| 找不到记忆组件或网关启动失败 | 是否执行 `npm ci`、Node 是否达到 22.16.0、网关配置是否复制；查看本地网关日志 |
| 后台 404 | `WEB_ADMIN_ENABLED`、`WEB_ADMIN_TOKEN` 是否已设置，服务是否重启 |
| 后台登录 403 | 是否通过本机或 SSH 隧道访问；仅修改监听地址不会解除登录来源限制 |
| 机器人不回消息 | NapCat 连接与 Token、群白名单、群聊天开关、睡眠状态、触发规则、模型配置 |
| JEV 不参与 | 总开关、子开关、`TYPESAFE_API_KEY`、文本长度与主动预算 |
| 语音 / 图片失败 | 协议、账号权限、模型名、尺寸参数和超时；在本机查看脱敏后的请求详情 |
| 修改 `.env` 无效 | 后台保存的覆盖值、环境加载优先级，以及是否重启了对应进程 |

不要把完整错误日志或请求详情直接粘贴到公开 Issue。
