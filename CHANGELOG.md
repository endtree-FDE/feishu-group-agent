# 变更记录

用户能看到的变化写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。版本号只以仓库根目录的 `VERSION` 为准。

程序不读取 `VERSION` 或本文件。删掉它们时，机器人的行为与现在相同。

当前 `VERSION` 是 `0.4.0-dev`。还没有对应的 GitHub Release。发布时请把 `VERSION` 改成不带 `-dev` 的版本号，打同名标签（例如 `0.4.0`），并让本文件里的版本节和标签一致。Release 说明不要写密钥、群号、open_id 或客户名。

`main` 上最近一次合并还没有版本标签。下面只记这条线相对于该快照新加的、用户能感知的部分。

## [Unreleased]

还没有收成 `0.4.0` 标签，也还没有 GitHub Release。发布草稿见 [docs/release-0.4.0.md](docs/release-0.4.0.md)。`VERSION` 仍是 `0.4.0-dev`。下面先写相对当前 `main` 新加的，再保留已经在 `main` 上、尚未打标签的内容。

### 修复

- `--doctor` 只在 `lark-cli auth scopes` 给出应用或 tenant 权限时才核对，缺了才失败。`lark-cli` 1.0.98 不接受 `--as bot`；已经登录时这条命令返回 `tokenType` 为 `user` 的 `userScopes`（用户 token，可能只有 `offline_access`）。这不再被当成应用权限，改为警告，并请到开放平台核对。
- 文本文件读写写明 UTF-8，子进程按 UTF-8 解码。标准流被管道或重定向接走、且当前不是 UTF-8 时会改成 UTF-8。中文 Windows 不设置 `PYTHONUTF8` 和 `PYTHONIOENCODING` 也能跑测试。直接看控制台时仍用原来的代码页。

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
- 集成保留已在 `main` 上的能力：只读工具连续多轮（`agent.max_tool_rounds`，不写是 4）、当前会话历史附件（`tools.message_attachment`）、`stall_sec`、`shutdown_wait_sec`、`link_jump_sec`、`catchup.max_age_sec`、`logs/events.jsonl`、`--doctor`、`scripts/verify_windows.ps1`。旧配置没写这些键时行为与现在的默认相同。
- 模型预设 `deepseek`、`dashscope`、`zhipu`、`pangu`。只补空着的地址、模型名和变量名。密钥仍只读环境变量。没写 `model_provider` 时仍是 Cursor。
- 新人 15 分钟说明、排障、行为准则，以及 v0.4.0 发布说明草稿。例子都是编的。
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

### 安全

- 公开前扫描只报告打码结果，不改写 Git 历史。

## English

The version is the `VERSION` file (`0.4.0-dev`). There is no GitHub Release yet. When publishing, drop the `-dev` suffix, tag that version, and keep this file in sync. Do not put secrets or Feishu IDs in release notes. The program does not read this file. New on this branch: several read-only tool calls per round, `message_ids`, remaining-round hints, configurable read caps, and `agent.tool_context_budget_chars`. Missing keys keep the previous behavior. `--doctor` warns instead of failing when `lark-cli` only returns a user token. Tests and the program read and write text as UTF-8 without `PYTHONUTF8` or `PYTHONIOENCODING`.
