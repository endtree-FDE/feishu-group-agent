# v0.4.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.4.0`。发布日期是 2026-10-19。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

下面的例子都是编的：机器人叫「示例助手」，人叫小李，群叫「城市挑战赛」。

## 这一版给使用者的变化

- 只读工具可以连续多轮。`agent.max_tool_rounds` 不写是 4。同一轮里可以按顺序执行多条只读工具，每条仍做原来的校验。写操作仍然先发确认码，小李确认之后才执行。
- 可以读取当前会话里的历史附件（`tools.message_attachment`）。下载前核对会话，不能拿别的群的消息编号来读文件。一次回答默认最多 4 个附件。可以传 `message_ids`，逐条核对后再读，结果分开标注。
- 这些读取上限不写时和以前一样：群历史 `page_size` 50、`max_lines` 150；搜索文档、读日历、读表格分别最多 4000、4000、5000 字；短期记忆一条最多 2000 字；多轮工具结果加起来最多 `agent.tool_context_budget_chars`（120000）字，超了先压缩较早的结果，仍超就截断并注明，不报错。写成 `0` 的项表示该项不截。
- 模型可以换成 DeepSeek、通义、智谱、盘古，或比赛提供的 OpenAI 兼容地址。密钥只放环境变量。
- 知识库目录和 `kb_read`。空目录不改变提示词。
- 停机再等 `shutdown_wait_sec`（默认 15 秒）。休眠超过 `link_jump_sec`（默认 20 秒）就重连。补答默认不超过 12 小时（`catchup.max_age_sec`）。
- 多少秒没有新输出算卡住，由 `agent.stall_sec` 决定，默认 300。写成 0 仍按 300，不能关掉卡死检测。
- 本机增加 `logs/events.jsonl`。它和 `logs/journal.jsonl` 都不记问题原文。
- `python agentbot.py agents/demo.json --doctor` 只做诊断，不发消息。Windows 上可以用 `scripts\verify_windows.ps1` 做部署后的只读检查。
- （#57）`--doctor` 不再把用户 token 当成应用权限。`lark-cli` 1.0.98 的 `auth scopes` 在已经登录时返回 `tokenType` 为 `user` 的 `userScopes`，这只给警告，并请到开放平台核对。只有应用或 tenant 权限列表缺项时才失败。
- （#57）测试和程序不再依赖 `PYTHONUTF8` 或 `PYTHONIOENCODING`。文本按 UTF-8 读写；标准流被管道或重定向接走、且当前不是 UTF-8 时才改成 UTF-8。直接看控制台时仍用原来的代码页。
- `python setup_agent.py --name demo --provider deepseek --dry-run` 可以先看将要生成的配置，不写文件。
- `python checks/check_config.py` 按 JSON Schema 检查类型，并拒绝把密钥写进配置。
- `skills.only`：不写、`[]` 或 `false` 时，文件夹里合法的技能仍然全部可用。写成名字列表后，目录、触发词预放和 `skill_read` 只认这些名字。写错的名字在 `--check` 里提示，不因此失败。
- `commands.status`：不写或为 `true` 时，`/状态`、`/status` 或单独一个「状态」直接回复当前开关，不调用模型。写成 `false` 时，这几个字仍交给模型。「状态怎么样」不是这条命令。
- `memory.trim_note`：不写或为 `true` 时，短期对话超过字数预算会在提示词里注明省略了几条，本地记录还在。写成 `false` 时仍丢掉旧对话，但不再写这句话。没超过预算时不加。
- 话题回复同时带了不同的 `root_id` 和 `thread_id` 时，仍算同一段对话。引用里的文件在追问时仍看得到正文，别的会话读不到。停机补答的文件消息只用文件名，不把 `file_key` 当成提问。
- 语音、表情、群名片和联系人名片直接说明，不把内部编号交给模型。系统消息和撤回事件不回答。飞书限流码 `99991400` 说成稍后再试。
- 长期记忆写入、注入和「/记忆」会消掉工具标记。人工档案在磁盘上仍保留原文。常驻模式里，联网搜索已经放行但没有正文时，回退到一次性回答。
- 日程可以改，也可以删。必须是白名单里的人，并且回复确认码。删日程不通知参会人。消息、文档和多维表格记录不删。
- 额度充足、不想被默认上限卡住时，看 [宽松配置示例](宽松配置示例.md)。只读轮数、一次回答里的附件数、PDF 页数、文档字数、群历史行数、搜索和日历和表格的字数、一条短期记忆的字数、多轮工具结果的总预算，都可以调大。不抄那段时，默认仍是上面这些数。

## 兼容

旧的 `agents/*.json` 不必为了升级而补上这些新键。没写的键保持上面的默认。已经写了的值优先，例如 `pdf_scan_pages` 写了 3 就仍是 3，不写才是 10。

长期记忆、进度卡片、常驻模式默认仍然关闭。确认码和写入白名单没有放宽。`agents/example.json` 里的数字仍是默认值，不是宽松示例。

## 还没做

每晚用模型重写记忆默认不跑。不主动往群里发消息。

## English

`VERSION` is `0.4.0`, released on 2026-10-19. This text is the release note for tag `v0.4.0`. The program does not read this file. One round can run several read-only tools. `message_attachment` accepts `message_ids`. A reply whose `thread_id` differs from `root_id` stays in the same topic. Quoted files remain visible on follow-up and cannot be read from another chat. Catch-up does not treat a `file_key` as the question. Voice, stickers, and contact or chat cards are not sent to the model. System messages and recalls get no answer. Long-term memory strips tool markers on write and inject. An empty web-search turn in resident mode falls back to a one-shot answer. Missing read caps keep the previous defaults, including `history.max_lines` 150, search/calendar/base character caps, `memory.max_turn_chars` 2000, and `agent.tool_context_budget_chars` 120000. A separate loose-limits example raises those numbers; the defaults and `agents/example.json` stay as they are. Confirmation codes and write allow-lists are unchanged. (#57) `--doctor` no longer treats a user token as an app permission: a `tokenType` of `user` is a warning, and only a real app or tenant scope list can fail the check. Tests and the program no longer depend on `PYTHONUTF8` or `PYTHONIOENCODING`. Nightly memory rewrite stays off, and the bot does not message groups on its own. Do not put secrets or real Feishu ids in the release notes.
