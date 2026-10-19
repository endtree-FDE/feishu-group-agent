# v0.4.0 发布说明（草稿）

这是草稿，不是 GitHub Release。仓库根目录的 `VERSION` 现在仍是 `0.4.0-dev`。程序不读取 `VERSION`，也不读取这份说明。真正发布时，把 `VERSION` 改成 `0.4.0`，打同名标签，再把本节整理进 Release。Release 正文不要写密钥、群号、open_id、客户名或个人电脑路径。

下面的例子都是编的：机器人叫「示例助手」，人叫小李，群叫「城市挑战赛」。

## 这一版给使用者的变化

- 只读工具可以连续多轮。`agent.max_tool_rounds` 不写是 4。写操作仍然先发确认码，小李确认之后才执行。
- 可以读取当前会话里的历史附件（`tools.message_attachment`）。下载前核对会话，不能拿别的群的消息编号来读文件。
- 模型可以换成 DeepSeek、通义、智谱、盘古，或比赛提供的 OpenAI 兼容地址。密钥只放环境变量。
- 知识库目录和 `kb_read`。空目录不改变提示词。
- 停机再等 `shutdown_wait_sec`（默认 15 秒）。休眠超过 `link_jump_sec`（默认 20 秒）就重连。补答默认不超过 12 小时（`catchup.max_age_sec`）。
- 多少秒没有新输出算卡住，由 `agent.stall_sec` 决定，默认 300。写成 0 仍按 300，不能关掉卡死检测。
- 本机增加 `logs/events.jsonl`。它和 `logs/journal.jsonl` 都不记问题原文。
- `python agentbot.py agents/demo.json --doctor` 只做诊断，不发消息。Windows 上可以用 `scripts\verify_windows.ps1` 做部署后的只读检查。
- `python setup_agent.py --name demo --provider deepseek --dry-run` 可以先看将要生成的配置，不写文件。
- `python checks/check_config.py` 按 JSON Schema 检查类型，并拒绝把密钥写进配置。

## 兼容

旧的 `agents/*.json` 不必为了升级而补上这些新键。没写的键保持上面的默认。已经写了的值优先，例如 `pdf_scan_pages` 写了 3 就仍是 3，不写才是 10。

长期记忆、进度卡片、常驻模式默认仍然关闭。确认码和写入白名单没有放宽。

## 还没做

还没有 GitHub Release。每晚用模型重写记忆默认不跑。不主动往群里发消息。

## English

This is a draft, not a GitHub Release. `VERSION` remains `0.4.0-dev` until the tag `0.4.0` is published. The program does not read this file. Missing config keys keep the defaults above. Do not put secrets or real Feishu ids in the release notes.
