# 飞书群机器人模板

这是一个飞书群机器人的模板。有人在群里 @ 它，或者私聊它，它就用你电脑上的 Cursor 来回答。

它能看本群最近的聊天，读分享给它的文档、日历和多维表格，也能看图片和文件。

要新建文档、建日程或往表格加记录时，必须由指定的人在 5 分钟内回复一个 6 位数，它才会动手。

每个群可以记自己的事：说「记一下」就记下，说「/记忆」就查看，说「/忘记」就删掉。

默认用这台电脑上的 Cursor（模型名默认 `grok-4.7-high-fast`，额度算 `cursor-agent login` 的那个账号）。也可以换成你自己的模型账号（DeepSeek、通义千问、Kimi、智谱、xAI、OpenAI，或本机 Ollama）。密钥放在环境变量里，不写进配置。见 [docs/模型接口.md](docs/模型接口.md)。

## 它能做什么

- 只在被 @ 或被允许的私聊里回答，不主动发消息。
- 同一个话题接着聊；发「新话题」就清空这几轮上下文。
- 长期记忆按群分开，白名单的人才能记和删，删除是真删。
- 进度卡片：先告诉你正在处理（默认关着，配置里打开）。
- 客户名按你的名单打成「某客户」；手机号、证件号、邮箱、密钥和电脑路径自动隐藏。
- 话题红线默认不设。要限制什么，写在自己的配置里。
- 同一个应用只跑一个进程；看门狗发现卡住会在本机重启，不往群里说话。
- 知识库可以定时收进来，先放待审区，你点头才换上。
- 技能（默认关着）：提示词里只放一行目录。模型要用时再读那份步骤。打开之后，问题里的词如果对上技能的触发词，会把那份步骤直接放进这一题。见 [docs/技能.md](docs/技能.md)。
- 常驻模式（默认关着）：`agent.mode` 写成 `acp` 后，Cursor 只启动一次。不改的话仍是每题一个进程。见 [docs/常驻模式.md](docs/常驻模式.md)。只对 Cursor 这一路有用。
- 模型可以换成你自己的账号：配置里写 `model_provider` 为 `openai_compat`，密钥放在环境变量。没写这一项时，仍是 Cursor。见 [docs/模型接口.md](docs/模型接口.md)。

## 在自己电脑上先检查

需要 Windows 和 Python 3.12。不需要先装别的 Python 包，也不需要联网：

```text
python tests/run_tests.py
```

一共 139 项，全部通过就可以。看 PDF 附件时才需要另外安装 `pypdf`。

从零开始：看 [docs/新建智能体.md](docs/新建智能体.md)。

已经有一个机器人、想换成这套（例如「小景」）：看 [docs/升级已有智能体.md](docs/升级已有智能体.md)。

配置每一项的意思：[agents/CONFIG.md](agents/CONFIG.md)。

## 文件

| 文件 | 作用 |
|---|---|
| `agentbot.py` | 主程序：收消息、决定回不回、叫模型、回复 |
| `providers/` | 模型接口。默认 `cursor`；`openai_compat` 接你自己的账号；`fake` 只给测试用 |
| `acp_client.py` | 可选的常驻进程。默认不用，Cursor 且 `agent.mode` 为 `acp` 才启动 |
| `tools.py` | 动飞书的手：只读，以及要确认码的写入 |
| `guard.py` | 打码和话题红线 |
| `memory.py` | 每个群一份长期记忆 |
| `skills.py` | 技能目录、触发词预放和按需读取；默认关闭 |
| `skills/` | 每个技能一个文件夹，里面一份 `SKILL.md` |
| `kb_sync.py` | 知识库先待审、再替换 |
| `make_bundle.py` | 在资料电脑上把笔记打成压缩包 |
| `agents/example.json` | 配置样子，里面都是占位符 |
| `agents/CONFIG.md` | 每一项怎么填 |
| `redact.example.txt` | 空的客户名名单样子，复制成 `redact.txt` 再用 |
| `scripts/` | Windows 上的启动、停止、看门狗、计划任务 |
| `tests/run_tests.py` | 离线测试，139 项 |
| `tests/fake_acp_agent.py` | 测试用的假常驻服务，不是真的 cursor-agent |
| `docs/` | 给人看的说明和架构图。技能怎么写见 `docs/技能.md` |
| `SKILL.md` | 给帮忙操作的 AI 助手看的步骤 |

设计说明和两张流程图在 `docs/架构.md`、`docs/arch-full.mmd`、`docs/memory-flow.mmd`。

## 许可证

本项目以 Apache-2.0 开源。你可以商用、修改、再分发，需要保留许可证和版权声明。全文见 [LICENSE](LICENSE)，版权信息见 [NOTICE](NOTICE)。

本项目调用的第三方模型和工具（例如 Cursor CLI、各家模型 API、飞书开放平台）各有自己的服务条款。使用者需要自己遵守这些条款，并使用自己的账号。
