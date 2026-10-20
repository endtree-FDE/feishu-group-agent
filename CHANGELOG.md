# 变更记录

用户能看到的变化写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。版本号只以仓库根目录的 `VERSION` 为准。

程序不读取 `VERSION` 或本文件。删掉它们时，机器人的行为与现在相同。

当前 `VERSION` 是 `0.7.0`，发布日期是 2026-10-20。发布说明见 [docs/release-0.7.0.md](docs/release-0.7.0.md)。从 0.6.0 升上来见 [docs/升级到0.7.0.md](docs/升级到0.7.0.md)。说明里不要写密钥、群号、open_id 或客户名。

## [Unreleased]

## [0.7.0] - 2026-10-20

下面是用户能看到的变化。没写进旧配置的键，仍用各项自己的默认值。这一版新加的键默认关着，或不写就保持 0.6.0 的行为。发布说明见 [docs/release-0.7.0.md](docs/release-0.7.0.md)。

### 添加

- 可替换的模型提供方。不写 `model` 时仍是 Cursor（`cursor-agent`，配置里 `agent.mode` 为 `acp` 时仍是常驻进程），行为与现在相同。`model.provider` 可写成 `openai_compatible`（OpenAI Chat Completions 兼容，含 DeepSeek、通义、Kimi、OpenRouter、本机 Ollama）或 `anthropic`（Claude Messages API）。地址、模型名、温度、超时和最大 token 写在 `model` 里。密钥只写环境变量名（`model.api_key_env`），不能写进配置。说明和四份示例见 [docs/换模型.md](docs/换模型.md)。
- 这两个新接口走原来的工具循环：读群历史和知识库、记忆提议、写入确认码。没有自带联网。可选 `search`（Tavily 或 SearXNG）默认关，打开后用 `web_search`。流式进度仍在；有思考才放收起的思考摘要，没有就跳过。多轮上下文仍按话题拼进提示词。接口若返回 token 数，会计入用量周报的「模型用量」，没有则不写这一行。
- 主人可以在飞书私聊里开关功能。发「设置」或「/设置」会看到一张清单：用量周报、知识检索、记忆成长、早间待办、断线提醒、技能。每一项写着现在开没开、有什么用、打开后会不会主动发消息、要不要先准备资料。点「第 N 项」或回复数字，先看改动摘要，再用原来的 6 位确认码。确认码只认发起人、原来的私聊和原来的话题。说明见 [docs/新手开关功能.md](docs/新手开关功能.md)。
- 确认之后先备份配置，再写入，再跑配置检查，然后按停止再启动的方式重新拉起，并且不会同时留下两个接收进程。检查或重启失败会改回备份，并用大白话说明原因和下一步。
- 第一次被主人私聊，或刚启动且已经写了主人时，私聊发一段短介绍。不发到群里。群里有人问怎么开，只会请对方找主人到私聊里发「设置」，不讲配置细节。
- 对话不能改密钥、模型配置、谁可以私聊。这些只提示去改配置文件。改动记在 `logs/feature_switch.jsonl`，不含编号、确认码和密钥。

### 安全

- 密钥不进日志。`python checks/check_config.py`、`--check` 和 `--doctor` 在密钥变量没设置时失败，并说明在当前窗口怎么设。只读回答和白名单写入确认码不变。
- 私聊开关只改用量周报、知识检索、记忆成长、早间待办、断线提醒、技能。不能改模型配置、密钥和谁可以私聊。

旧配置不用改。从 0.6.0 升上来见 [docs/升级到0.7.0.md](docs/升级到0.7.0.md)。

## [0.6.0] - 2026-10-20

下面是用户能看到的变化。没写进旧配置的键，仍用各项自己的默认值。发布说明见 [docs/release-0.6.0.md](docs/release-0.6.0.md)。这一版新加的开关默认都关着。

### 添加

- 回答质量回归 `python evals/run.py`。用例在 `evals/cases/suite.json`，假模型基线在 `evals/baseline/ci.json`。说明见 [docs/回归测试.md](docs/回归测试.md)。不改默认配置。正式运行仍不读这些文件。CI 只跑假模型那一档，不连接飞书，也不拿单实例锁。
- 用量周报 `report`，默认关。不写或 `enabled` 为 `false` 时不发私聊，`/状态` 也不多这一行。打开后，按 `weekday` 和 `time`（不写是周一 09:00、`Asia/Shanghai`）把上一周的合计私聊给主人，每周一次，重启不重发。接收人优先用 `report.owner_open_ids`，空着再用 `alerts.owner_open_ids`。正文只有合计：问题与完成数、响应时间中位数和 P90、工具和文件类型、写入提议与确认、脱敏次数、错误、连接中断和健康提醒、粗问题类别。没有问题原文、人名、open_id 或群编号。
- 主人可以在私聊发 `/report` 再看一份，不占每周名额。群里发这条只回一句已私聊，明细不进群。没打开时，`/report` 仍交给模型。
- `python agentbot.py <配置> --report [--week 2026-W42] [--out 文件.md|文件.csv]` 只打印或导出，不发送。`enabled` 为 false 时也能用。
- 读文件的审计和用量只记扩展名，不记原文件名。脱敏命中只记次数。`--dry-run`、测试替身和停机补答会标成演练或补答，周报不把它们算进真实用量。
- 分群知识库检索，默认关。没写 `knowledge` 或 `enabled` 为 `false` 时，不收录、不检索，提示词、工具和 `/状态` 与现在一样。打开后，每个群或私聊一份本地 SQLite 索引：本地 `kb/`、可选的群文件，以及配置或群里出现的飞书云文档、知识库和多维表格。中文用字双字进 FTS5。配了 OpenAI 兼容的向量地址才做混合检索，否则只用关键词。模型用 `kb_search` 查，也可以打开 `auto_retrieve` 自动附上片段。回答按标题、章节或页码引用，飞书文档带链接。白名单成员发「/kb refresh」更新；「/kb」或「/kb status」查看。`refresh_min` 大于 0 时按间隔补一次，一个周期只更新一个会话。说明见 [docs/知识库.md](docs/知识库.md)。
- 记忆成长 `memory.growth`，默认关。不写或 `enabled` 为 `false` 时，不调用模型，不发提案，也不改「记一下」、`/记忆`、`/忘记`。长期记忆和成长都打开后，才会在配置的时间（不写是 03:00）或一段对话闲下来时，对照本群近期消息提议新事实、更新或作废。提案默认私聊 `approver_open_ids`，也可以改成发在本群。每条用单独的 6 位码，审批人才能 `确认记忆`、`改记忆` 或 `驳回记忆`。别人、别的群、过期和用过的码都不会写入。事实会记下日期、会话、消息编号和把握。客户名、手机号、证件号、邮箱和密钥整条不提议。群与群仍然分开。`/搜记忆` 只在长期记忆开着时可用。成长打开后，`/忘记` 要本人再确认；关着时仍是立刻删除。见 [docs/记忆成长.md](docs/记忆成长.md)。

旧配置不用改。说明见 [docs/用量周报.md](docs/用量周报.md)。从 0.5.0 升上来见 [docs/升级到0.6.0.md](docs/升级到0.6.0.md)。

### 安全

- 知识库按会话分开，检索不能指定别的群。收录前和送出前都打码，并消掉工具标记和伪造确认码。资料里的网址不会被拿去访问，只把飞书文档、知识库、多维表格的编号交给原来的只读命令。压缩包、PDF 页数和单文件大小仍用附件那一套上限。向量地址只来自配置，密钥只读环境变量。需要的飞书权限都是只读，没打开这项时 `--doctor` 不多要权限。

## [0.5.0] - 2026-10-19

下面是用户能看到的变化。没写进旧配置的键，仍用各项自己的默认值。发布说明见 [docs/release-0.5.0.md](docs/release-0.5.0.md)。这一版新加的开关默认都关着。

### 添加

- 六份创作流程技能，沿用原来的 `skills/<名字>/SKILL.md`、目录和触发词预放。分别是选题打分（`topic-ideation`）、短视频口播稿（`short-video-script`）、分镜表（`storyboard`）、不绑定某一家工具的提示词包（`aigc-prompt-pack`）、项目策划书（`creative-brief`）、创作复盘（`post-mortem`）。用户明确要求保存时，才提议已有的 `docs_create`、`docs_append` 或 `base_create`，确认码和写入白名单不变。
- `agents/example.json` 的 `skills.enabled` 仍是 `false`，`skills.only` 仍是 `[]`。不改配置时，提示词和没有这些文件时一样。只启用创作技能的写法在 [docs/技能.md](docs/技能.md) 和 [agents/CONFIG.md](agents/CONFIG.md)。
- 渠道适配器。收事件、发消息、改卡片、读历史和附件、查成员、认身份都从 `channels/` 出去。飞书（lark-cli）是第一个适配器。没写 `channel` 的旧配置仍走飞书，命令和配置键不变。渠道缺少的能力（例如卡片刷新或日历）不会进提示词，模型硬调用时直接说明，不打到传输层。测试用内存渠道。企业微信只放了实验骨架和设计说明，默认不加载，也不连接。见 [docs/渠道.md](docs/渠道.md) 和 [docs/渠道-企业微信.md](docs/渠道-企业微信.md)。
- 断线提醒 `alerts`，默认关。不写或 `enabled` 为 `false` 时不发消息，也不写 `logs/alerts.log`。打开后，长连接连续中断或心跳超过 `unhealthy_after_sec`（不写是 180 秒）才私聊 `owner_open_ids`，恢复后再发一条。`repeat_sec` 不写是 1800 秒。私聊和早晨摘要都交给当前渠道的 `push_text`。飞书在适配器里调用 `im +messages-send`，不依赖事件长连接，也不启动收消息的进程。企业微信骨架这项直接失败，不会去连接。看门狗仍按 3 分钟重启；只有这项开着时，才在重启前通知，失败也不挡住重启。`windows_toast` 默认 `false`。
- 早晨摘要 `digest`，默认关。这是框架会主动发到群里的唯一一种消息。`enabled` 为 `true` 时，按 `time` 和 `timezone`（不写是 08:30、`Asia/Shanghai`）往 `chat_ids` 里、并且已经在服务范围内的群发一条今天的日历和未完成任务。每个群每个本地日最多一条，重启也不再发。不点名所有人。正文先走原来的脱敏。没配日历或任务表就不读。打开后，提示词稳定前缀里多一句固定说明，不随问题和日期变化。

旧配置不用改。`/状态` 只在对应开关打开时多一行。

## [0.4.1] - 2026-10-19

下面是用户能看到的变化。没写进旧配置的键，仍用各项自己的默认值。发布说明见 [docs/release-0.4.1.md](docs/release-0.4.1.md)。

### 安全

- （#58）写入确认码绑定发起人、会话和话题。主聊天的码不绑单条消息编号。别人、别的群、别的私聊、别的话题都不能确认，错误尝试不会把码用掉。发送人或消息编号为空时不能发起或确认。用过或取消过的码先记入已用名单，重启后也不能再执行。还没使用且未过期的码，重启后仍然有效。新码不会覆盖还没确认的操作。`state/state.db` 会自己建 `used_writes` 表，不用手工迁移。
- （#58）引用、历史、网页说明、飞书文档、PDF、Word、PPT、Excel、压缩包、图片说明、长期记忆和工具结果里的工具标记、伪造确认码会被消掉。当前这条用户问题仍可正常调用工具。
- （#58）上网前的跳转探测只连接已经检查过的地址。域名解析到 `198.18.0.0/15` 的假 IP 代理仍然放行，裸 IP 和跳进内网仍然拒绝。
- （#58）Word、PPT、Excel 和知识库压缩包成员超过体积或压缩比时跳过。PDF 页数异常时整份跳过，不执行里面的脚本。启动时清掉超过一小时的临时文件。
- （#58）客户名多认全角空格、组合附加符号、拆开的短名，以及名单里拼音的声调和连字符。没有写进名单的拼音仍不自动隐藏。带空格或连字符的手机号、`Bearer` 和 `sk-` 样子的密钥会打码。日志里的接口错误和模型报错也会打码。
- （#58）同一个飞书应用只能有一个接收进程。另一个目录再启动会被拒绝，不会结束已经在跑的那个。`stop.ps1` 仍按本目录和 profile 判断，不会误杀别的机器人。
- （#58）单实例锁和按应用的锁先写完整内容，再让别的进程看见。启动的一瞬间，另一个进程不会把还没写完的空文件当成过期锁并接管。
- （#58）确认、状态、帮助和记忆命令不再受单人提问间隔限制，但每分钟有命令次数上限。不写 `rate_limit.max_commands_per_min` 时是 40。写成 `0` 表示不限这条。
- （#58）GitHub Actions 固定到 commit SHA，并校验 gitleaks 压缩包的 SHA256。
- （#58）`.github/dependabot.yml` 跟踪 github-actions 和 pip。`requirements.txt` 只列出可选的 `pypdf`。
- （#58）`CODEOWNERS`。安全上报写明使用 GitHub 私密漏洞上报。
- （#58）文档里的 `lark-cli config init` 都带 `--name <profile>`，并说明不写会覆盖这台电脑上其他机器人的 profile。

### 添加

- （#59）私聊可以按群成员放行。`p2p.allow_group_members` 默认 `false`，不写时不查群成员，和 0.4.0 一样。设为 `true` 后，当前在所服务群里的成员可以私聊；`allow_open_ids` 或写入白名单里的人仍然可以。`open_to_all` 优先。成员名单默认缓存 600 秒（`p2p.member_cache_ttl_sec`）。查成员失败、超时、缺权限或名单不完整时拒绝，不回复群外的人。需要 `im:chat.members:read`。

## [0.4.0] - 2026-10-19

下面是用户能看到的变化。没写进旧配置的键，仍用各项自己的默认值。

### 修复

- （#57）`--doctor` 只在 `lark-cli auth scopes` 给出应用或 tenant 权限时才核对，缺了才失败。`lark-cli` 1.0.98 不接受 `--as bot`；已经登录时这条命令返回 `tokenType` 为 `user` 的 `userScopes`（用户 token，可能只有 `offline_access`）。这不再被当成应用权限，改为警告，并请到开放平台核对。
- （#57）文本文件读写写明 UTF-8，子进程按 UTF-8 解码。标准流被管道或重定向接走、且当前不是 UTF-8 时会改成 UTF-8。中文 Windows 不设置 `PYTHONUTF8` 和 `PYTHONIOENCODING` 也能跑测试。直接看控制台时仍用原来的代码页。

### 添加

- 话题回复同时带了不同的 `root_id` 和 `thread_id` 时，仍算同一段对话。引用里的文件在追问时仍看得到正文，别的会话读不到。停机补答的文件消息不再把 `file_key` 当成提问。
- 语音、表情、群名片和联系人名片直接说明，不把内部编号交给模型。系统消息和撤回事件不回答。飞书限流码 `99991400` 说成稍后再试。
- 长期记忆写入、注入和 `/记忆` 会消掉工具标记。人工档案在磁盘上仍保留原文。常驻模式里，联网搜索已经放行但没有正文时，回退到一次性回答。
- 同一轮可以按顺序执行多条只读工具，每条仍做原来的校验。`message_attachment` 可以传 `message_ids`，逐条核对当前会话后再读，结果分开标注。每轮提示词写明还剩几轮只读工具。
- 扫描 PDF 交给模型的图片说明使用配置里的 `pdf_max_images`（不写是 3）。`docs_read` 只有正文真的被截断时才标「节选」。
- 这些上限不写时和以前一样：`tools.history.page_size` 50（停机补答拉消息也用它）、`tools.history.max_lines` 150（超出注明还剩几行，`0` 不截）、`tools.docs_search.max_chars` 4000、`tools.calendar_read.max_chars` 4000、`tools.base_read.max_chars` 5000、`memory.max_turn_chars` 2000（只留开头，`0` 留整段）、`agent.tool_context_budget_chars` 120000（多轮工具结果超预算时先压缩较早的结果，仍超就截断并注明，不报错；`0` 不设预算）。
- `skills.only`：不写、`[]` 或 `false` 时，文件夹里合法的技能仍然全部可用。写成名字列表后，目录、触发词预放和 `skill_read` 只认这些名字。写错的名字在 `--check` 里提示，不因此失败。
- `commands.status`：不写或为 `true` 时，`/状态`、`/status` 或单独一个「状态」直接回复当前开关，不调用模型。写成 `false` 时，这几个字仍交给模型。「状态怎么样」不是这条命令。
- `memory.trim_note`：不写或为 `true` 时，短期对话超过字数预算会在提示词里注明省略了几条，本地记录还在。写成 `false` 时仍丢掉旧对话，但不再写这句话。没超过预算时不加。
- 只读工具连续多轮（`agent.max_tool_rounds`，不写是 4）、当前会话历史附件（`tools.message_attachment`）、`stall_sec`、`shutdown_wait_sec`、`link_jump_sec`、`catchup.max_age_sec`、`logs/events.jsonl`、`--doctor`、`scripts/verify_windows.ps1`。旧配置没写这些键时行为与现在的默认相同。
- 模型预设 `deepseek`、`dashscope`、`zhipu`、`pangu`。只补空着的地址、模型名和变量名。密钥仍只读环境变量。没写 `model_provider` 时仍是 Cursor。
- 新人 15 分钟说明、排障、行为准则，以及 v0.4.0 发布说明。例子都是编的。
- GitHub Actions 增加 `windows-latest` 作业：用 Windows PowerShell 5.1（`powershell.exe`）解析 `.ps1`、干跑不改系统的函数，并检查含中文的脚本带 UTF-8 BOM，然后跑测试。
- `python checks/check_config.py` 按 JSON Schema 检查配置类型。密钥写进 `api_key_env` 或 `base_url` 会失败。缺了的键和不认识的键不失败。
- `python setup_agent.py` 可以干跑生成 `agents/<名字>.json`，并打印计划任务命令。不加 `--write` 不写文件，也不注册任务。
- 架构图补上现在实际走的路，以及 Cursor 与 OpenAI 兼容接口二选一。每晚整理记忆仍标为设计，默认不跑。
- 增加「用本框架参赛」说明：把 openJiuwen / JiuwenSwarm 的地址和模型名映射到现有配置，密钥仍只读环境变量。
- 知识库目录和 `kb_read`。旧配置没写时，空目录不改变提示词。
- 本机 `logs/journal.jsonl`，只记工具名、字数和耗时，不记问题原文。`observability.journal` 为 `false` 时不写。
- 长期记忆开着且档案被字数预算截断时，按本群问题召回命中的事实。`memory.recall` 为 `false` 时不召回。
- GitHub Actions：Windows 和 Linux 上跑测试与编译检查；另有 Windows PowerShell 5.1 作业检查脚本；Linux 上再跑 gitleaks 和 `checks/history_scan.py`。
- Issue 模板、Pull Request 模板、贡献说明里的代码风格。
- 可选的宽松读数见 [docs/宽松配置示例.md](docs/宽松配置示例.md)。只读轮数、一次回答里的附件数、PDF 页数、文档字数，以及 `history.max_lines`、`docs_search` / `calendar_read` / `base_read` 的 `max_chars`、`memory.max_turn_chars`、`agent.tool_context_budget_chars` 都可以调大。`agents/example.json` 和程序默认值不变。确认码和写入白名单也不放宽。

### 安全

- 公开前扫描只报告打码结果，不改写 Git 历史。

## English

The version is the `VERSION` file (`0.7.0`), released on 2026-10-20. Do not put secrets or Feishu IDs in release notes. The program does not read this file. Omitting `model` keeps the Cursor path from 0.6.0. `search` stays off when omitted. Owners can toggle the existing usage report, knowledge search, memory growth, morning digest, disconnect alerts, and skills in a private chat. That switch cannot change model settings, secrets, or who may use private chat. Upgrade and rollback steps are in `docs/升级到0.7.0.md`. Rolling back to v0.6.0 (`4da9e42`) drops the model providers and the private-chat switch. This note does not create the tag.

The previous version is the `VERSION` file (`0.6.0`), released on 2026-10-20. Do not put secrets or Feishu IDs in release notes. The program does not read this file. 0.6.0 adds answer-quality evals, an optional weekly usage report, optional per-chat knowledge search, and optional memory growth. All four stay off when omitted. Evals do not connect to Feishu. The report is a weekly owner DM with totals only. Knowledge search indexes one chat and cites sources. Memory growth writes nothing until an approver confirms. Upgrade and rollback steps are in `docs/升级到0.6.0.md`. Rolling back to v0.5.0 (`e842684`) drops these four. This note does not create the tag.

The previous version is the `VERSION` file (`0.5.0`), released on 2026-10-19. Do not put secrets or Feishu IDs in release notes. The program does not read this file. Missing keys keep the previous behavior. 0.5.0 adds six creative-workflow skills, a channel adapter (Feishu is the first implementation; WeCom is an experimental skeleton and does not connect), optional disconnect alerts, and an optional morning digest. `skills.enabled`, `alerts.enabled`, and `digest.enabled` stay false when omitted. An empty `skills.only` still means every valid skill once skills are turned on. Writes still need the allow-list and a confirmation code. Alert DMs and digest posts go through the channel `push_text` method. On Feishu that is `im +messages-send` and does not start the event consumer. The digest posts at most once per local day per configured chat and never mentions everyone. Upgrade and rollback steps are in `docs/升级到0.5.0.md`. Rolling back to v0.4.1 (`deb498f`) also drops the channel adapter and the creative skills.

0.4.1 remains the previous release. (#58) Confirmation codes are bound to the initiator, chat, and topic. Used or cancelled codes are stored in `used_writes` before they run and cannot be replayed after a restart. Untrusted text cannot become a tool call or a confirmation code. Redirect checks connect only to addresses that were already validated. One Feishu app has one receiver. Lock files become visible only after they are fully written. Confirm, status, help, and memory commands skip the per-user gap and use `rate_limit.max_commands_per_min` (default 40). (#59) `p2p.allow_group_members` defaults to false. When enabled, current members of served groups may use private chat, which needs `im:chat.members:read`. `open_to_all` still wins, and a failed or truncated member list fails closed. Those upgrade steps are in `docs/升级到0.4.1.md`. 0.4.0 still includes several read-only tool calls per round, configurable read caps, and the (#57) doctor and UTF-8 fixes.
