# 路线图

写于 2026-10-18。目标是把这个飞书群智能体框架做到接近成熟开源项目：回答准、联网和读文件可靠、记忆好用、写入安全、崩溃能自己恢复、日志查得到、新人大约半小时能部署。

排序按「收益高、风险低」在前。每一项都要保持这些红线：不主动往群里发消息，不加话题屏蔽，写入仍要白名单和 6 位确认码，不把真实账号、群号、密钥、客户名写进仓库。已有 `agents/*.json` 缺了新键时，要么保持原行为，要么只在本来就安全的方向上补默认值（下面每项写明是哪一种）。

星数和链接以 2026-10-18 能打开的公开页面为准，星数以后会变。

## 已经做成草稿、还没进 main

这三项各自独立，不叠在一起。线上是否部署由人决定。

| 项 | 草稿 | 配置兼容 |
|---|---|---|
| 技能选用名单 | [PR #19](https://github.com/endtree-FDE/feishu-group-agent/pull/19) | `skills.only` 不写、`[]` 或 `false` 时，合法技能仍全部可用 |
| 超预算时注明省略了几条 | [PR #21](https://github.com/endtree-FDE/feishu-group-agent/pull/21) | `memory.trim_note` 不写或 `true` 才在真的裁掉旧对话时加一句；`false` 恢复静默丢掉。没超预算的提示词不变 |
| `/状态` 不调用模型 | [PR #20](https://github.com/endtree-FDE/feishu-group-agent/pull/20) | `commands.status` 不写或 `true` 时生效；`false` 时「状态」仍交给模型 |

## 接下来按这个顺序做

### 1. 知识库目录，并按文件名读取（本轮草稿）

- **目标：** 模型知道知识库里有哪些文件，并能按相对路径读出正文。没有依据时仍说不知道。`openai_compat` 读不了工作区文件，所以必须有程序代读，不能只靠 Cursor 自己翻文件夹。
- **参考：** [OpenClaw 工作区](https://docs.openclaw.ai/concepts/agent-workspace)、[Dify 知识库](https://github.com/langgenius/dify)、[CowAgent](https://github.com/zhayujie/CowAgent)、本仓库 `docs/架构.md` 里还没落地的知识库目录。
- **验收：** 知识库为空时，提示词不多出目录块。有 `.md` / `.txt` 时，只注入文件名和一行标题，不注入正文。标题和正文里的客户名要打码，`<<TOOL>>` 要换成看不出的样子。`../`、绝对路径、跳出目录的符号链接读不到。未列入私聊名单的人不注入目录，也不能读。`kb_index.enabled` 为 `false` 时不注入目录。`tools.kb_read.enabled` 为 `false` 时不能读。旧配置没写这两项时：目录默认开（空目录无影响），`kb_read` 默认开。
- **不做：** 不把整库塞进每一题，不上向量库。

### 2. 本机日志能按工具查，但不记原文（本轮草稿）

- **目标：** 除了现有的 `metric` 行，再写一份本机 `logs/journal.jsonl`，能按工具名、是否成功、耗时、字数检索。问题、工具参数、回复正文、路径、群号都不进这文件。
- **参考：** [OpenHands](https://github.com/OpenHands/OpenHands) 的事件记录、[LangBot](https://github.com/langbot-app/LangBot) 的监控。只借鉴「查得到」，不把提示词落盘。
- **验收：** 正常回答会追加一行，里面没有问题原文。`observability.journal` 为 `false` 时不写。不写这一项时默认写。已有的 `metric` 行还在。
- **不做：** 不把日志送到外部服务。

### 3. 长期记忆按本群问题召回被截掉的事实

- **目标：** 群档案超过字数预算时，本题若命中被截掉的事实，把那几条（已脱敏、仍在预算内）补进会变的部分。别的群的账本不读。
- **参考：** [mem0](https://github.com/mem0ai/mem0) 的检索、[OpenClaw 记忆](https://docs.openclaw.ai/concepts/memory)、[Letta memory blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks)。
- **验收：** `memory.enabled` 为 false 时提示词与现在相同。开着时，A 群的问题看不到 B 群的事实。召回的文字经过脱敏。不让模型直接改档案；写入仍走「记一下」或确认码。
- **不做：** 不上托管记忆，不夜间自动调模型重写档案。

### 4. 技能声明了工具范围才限制

- **目标：** `SKILL.md` 里如果写了 `x-tools`，这一题按该技能继续调用的飞书工具必须在名单里。没写这个字段的技能和现在一样。
- **参考：** [agentskills.io](https://agentskills.io/specification)、[Claude 技能仓库](https://github.com/anthropics/skills)、[lark-cli 技能](https://github.com/larksuite/cli/tree/main/skills)。
- **验收：** 没有 `x-tools` 的技能仍能调用已启用的只读工具。写了 `x-tools: [base_read]` 时，同一次技能后续调用 `docs_create` 被拒绝，确认码不会发出。写操作即使在名单里，仍要白名单和确认码。
- **不做：** 不执行技能目录里的脚本，不安装第三方技能市场。

### 5. 部署检查说清下一步

- **目标：** `python agentbot.py agents/<名字>.json --check` 在占位符还没换、可选依赖没装时，用 HINT 告诉下一步，而不是让新人对着日志猜。正式缺 `bot_open_id` 仍应失败。
- **参考：** [NoneBot2](https://github.com/nonebot/nonebot2) 的脚手架、[OpenClaw 技能与配置检查](https://docs.openclaw.ai/skills)、[飞书官方插件](https://github.com/larksuite/openclaw-lark) 的安装说明。部署步骤本身仍以 [docs/新建智能体.md](新建智能体.md) 为准。
- **验收：** 配置里还留着 `REPLACE_` 时，`--check` 打印 HINT，并指出要换成自己的值。不因为没装 `pypdf` / `openpyxl` 而失败，只提示可选。已有测试里的占位配置不会因为这条 HINT 被判失败。
- **不做：** 不加网页安装器，不在检查时访问飞书以外的新地址。

### 6. 读附件失败时说原因，而不是笼统失败

- **目标：** 下载失败、类型不支持、文件损坏时，回复和日志里的原因已经部分分开。再补上「可以再发一次」和「不要把失败当成空内容去编」。
- **参考：** [lark-cli](https://github.com/larksuite/cli) 的结构化错误、本仓库 `tools.py` 里已有的权限和 3380002 提示。
- **验收：** 损坏的 PDF、旧版 `.doc`、超限 zip 的现有测试仍通过。新用例覆盖「下载失败」时不调用模型编造正文，回复里没有本地路径。
- **不做：** 不放宽附件大小上限，不读取知识库目录以外的路径。

### 7. 崩溃后的回退原因写进日志

- **目标：** 常驻进程崩溃或超时后改走一次性进程，这件事已有。日志里补一行原因（崩溃、超时、协议错误），不含半截回答。看门狗重启仍不往群里发消息。
- **参考：** [LangBot](https://github.com/langbot-app/LangBot) 的异常处理、本仓库 `docs/常驻模式.md`。
- **验收：** 现有「崩溃后这一题回退到 -p」测试仍通过，并且日志里有固定的 `fallback reason=`。`scripts/stop.ps1` 仍只结束本机器人。
- **不做：** 不扩大进程树结束范围。

### 8. 可选的备用模型，默认关

- **目标：** 主模型整次失败时，可以再试另一家，但只有配置写了才发生，避免一次提问扣两次额度。
- **参考：** [AstrBot](https://github.com/AstrBotDevs/AstrBot)、[Dify](https://github.com/langgenius/dify) 的多模型。
- **验收：** 不写备用配置时，失败路径与现在相同。写了之后，主模型失败才调用备用，密钥仍只在环境变量里。日志不打印密钥。
- **不做：** 不把备用模型设成默认开。

## 明确不做

- 插件市场，或让模型自己改技能、改人设。
- 到点往群里发消息，包括晨报、提醒、进群招呼。
- 框架内置话题屏蔽。话题限制继续只由每个机器人自己的 `policy.redlines` 决定，默认为空。
- 放宽写入：不取消确认码，不取消白名单，不邀请人，不代发 `@全员`（除非对方原话要求，且是已确认的日程）。
- 把问题原文、工具结果或提示词写成可外传的轨迹。
- 为了检索去接托管记忆或向量服务。
