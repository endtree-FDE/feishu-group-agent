# 路线图

写于 2026-10-18。目标是达到可以公开的成熟开源项目水平：回答准、联网和读文件可靠、记忆好用、写入安全、崩溃能自己恢复、日志查得到、新人大约半小时能部署。许可证用仓库里已有的 Apache-2.0。公开之后，同一套门卫、确认码和脱敏要能换模型和换运行平台，方便拿去参赛（例如 2026 年 CCF BDCI 上由 openJiuwen / JiuwenSwarm 支撑的赛题）。比赛用的密钥、股票池和客户数据不进这个仓库。

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

### 3. 长期记忆按本群问题召回被截掉的事实（本轮草稿）

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

## 开源就绪

公开仓库要让陌生人能看懂、能跑测试、能报告漏洞。下面每一项都还没做完；已经有的文件写明了，避免推倒重来。做的时候仍遵守上面的红线，不把真实账号、群号、密钥、客户名写进示例或截图。

### 9. 中英双语 README（本轮草稿）

- **目标：** 首页同时给中文和英文读者：这是什么、架构图、半小时能走完的快速开始、安全边界、许可证。
- **现状：** [README.md](../README.md) 有中文说明、英文导读、mermaid 架构图，以及中英两份快速开始。测试条数改为看运行结果，不再写 208。[LICENSE](../LICENSE) 已是 Apache-2.0。
- **参考：** [NoneBot2 README](https://github.com/nonebot/nonebot2)、[OpenClaw](https://github.com/openclaw/openclaw)。
- **验收：** 中文和英文都覆盖用途、架构图、复制 `agents/example.json` 的快速开始、测试命令、不要提交的文件。测试条数与当时的 `tests/run_tests.py` 一致，或改成「全部通过」而不写会死的数字。不出现真实群号和客户名。

### 10. CONTRIBUTING 和代码规范（本轮草稿）

- **目标：** 贡献者知道怎么跑测试、什么不能提交、代码按什么风格改。
- **现状：** [CONTRIBUTING.md](../CONTRIBUTING.md) 写了测试命令、4 空格紧凑风格，以及 `checks/lint.py`。测试条数改为看运行结果，不再写死。
- **参考：** [GitHub 开源指南里的贡献文档](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions)。
- **验收：** 文档写明：Python 3.12、`python tests/run_tests.py` 必须全过、不提交填好的配置和密钥、新行为要补测试。代码规范写成仓库里已经在用的写法（紧凑的 Python，注释说明为什么），并有一个不强迫全库重排的检查能在 CI 里通过。不把现有默认开关改成开。

### 11. SECURITY.md（本轮草稿）

- **目标：** 别人知道往哪私下报告漏洞，以及哪些文件不能公开。
- **现状：** [SECURITY.md](../SECURITY.md) 已有私人通报链接和禁止提交的清单。版本政策已改成：还没有 Release 时以 `main` 为准，有了 Release 之后以最新 Release 和 `main` 为准。
- **参考：** [GitHub 安全政策](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository)。
- **验收：** 中英都说明：不要在公开 Issue 里贴密钥和飞书编号；给出私人通报入口；列出不提交的路径。版本政策与当时是否已发布一致。

### 12. CHANGELOG、版本号和 Release（本轮草稿）

- **目标：** 每次对用户可见的改动有一条记录，打标签就能下载。
- **现状：** 根目录有 `VERSION`（`0.4.0-dev`）和 `CHANGELOG.md` 的未发布节。还没有 GitHub Release。程序不读这两个文件。`acp_client.py` 里给协议用的 `clientInfo` 版本字符串仍不是对外发布号。
- **参考：** [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)、[语义化版本](https://semver.org/lang/zh-CN/)、[GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。
- **验收：** 仓库根有 `CHANGELOG.md`，至少有一个未发布或已发布的版本节。有一处单一版本号（例如 `VERSION` 文件），和 changelog、Release 标签一致。缺这个文件时，程序行为与现在相同。Release 说明不包含密钥和真实编号。

### 13. Issue 和 Pull Request 模板（本轮草稿）

- **目标：** 报问题和提改动时，先写清复现步骤、影响和有没有碰到写入或脱敏。
- **现状：** `.github/ISSUE_TEMPLATE/` 有缺陷和功能两个模板，漏洞改走私人通报。`.github/PULL_REQUEST_TEMPLATE.md` 要求写配置兼容和对抗自检。
- **参考：** [Issue 和 PR 模板](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates)。
- **验收：** 至少有一个 bug 模板、一个功能模板、一个 PR 模板。模板提醒不要粘贴 App Secret、群号、open_id、客户名。不因为加了模板而改变程序行为。

### 14. GitHub Actions：测试和 lint，Windows 与 Linux（本轮草稿）

- **目标：** 每次推送和拉取请求都在 Windows 和 Linux 上跑测试，并跑第 10 项的 lint。
- **现状：** `.github/workflows/ci.yml` 在 Ubuntu 和 Windows 上跑 `checks/lint.py` 和 `tests/run_tests.py`。`.gitattributes` 把文本固定为 LF。本地测试不需要第三方包，也不需要飞书。
- **参考：** [GitHub Actions](https://docs.github.com/en/actions)。
- **验收：** 两个系统都执行 `python tests/run_tests.py`，失败则工作流失败。不在 CI 里访问飞书，不把密钥写进工作流。Windows 上没有 PowerShell 的检查仍只核对脚本源码，和现在的测试一样。

### 15. 示例配置

- **目标：** 新人复制一份就能看出每一项该换成自己的值。
- **现状：** [agents/example.json](../agents/example.json) 已用 `REPLACE_` 占位，并被当作唯一允许提交的智能体配置。
- **参考：** [agents/CONFIG.md](../agents/CONFIG.md)。
- **验收：** 示例里没有真实 App Secret、群号、open_id、客户名。新增的开关在示例里写出默认值，并在 CONFIG 里说明「不写这一项时怎样」。`python agentbot.py agents/example.json --check` 对占位符的提示与第 5 项一致，不要求在这一项里先做完第 5 项。

### 16. 演示截图和动图占位（本轮草稿）

- **目标：** 首页能放一张界面说明，但公开前不放带真实聊天记录的图。
- **现状：** [docs/demo/README.md](demo/README.md) 说明以后放图时要遮住什么。目录里还没有真实截图。
- **参考：** 常见做法是仓库里放一张不含账号的示意图，或在 README 里写明「截图待补」。
- **验收：** 有一处占位（图或说明文件），写明替换时要遮住群名、头像、open_id 和客户名。占位文件本身不含这些内容。不把真实会话截图提交进来。

### 17. 模型和平台可替换

- **目标：** 参赛或换供应商时，只换模型或换运行平台，不复制一份飞书门卫、确认码和 SSRF 检查。
- **现状：** `model_provider` 已能在 `cursor`、`openai_compat`、`fake` 之间换，密钥只留在环境变量里。消息入口和工具仍绑在飞书 / lark-cli 上。
- **参考：** [docs/模型接口.md](模型接口.md)、[openJiuwen 赛题报道](https://caijing.chinadaily.com.cn/a/202607/20/WS6a5dd2a3a310d709c2fbe862.html)、[职场赛题](https://competitions.atomgit.com/competition/2079523455267733506)、[量化赛题](https://competitions.atomgit.com/competition/2079794808848314369)、[论文赛题](https://competitions.atomgit.com/competition/2079803553561747457)。赛题要接的是 openJiuwen / JiuwenSwarm 这一类运行时，模型名由参赛者自己配。
- **验收：** 不写新的平台配置时，飞书入口和现有两种模型与现在相同。文档说明怎样接另一种 OpenAI 兼容模型，以及平台接口要守住的边界：白名单、确认码、脱敏、不主动发消息。仓库不收录比赛密钥、账号或赛题数据。默认不自动切换到第二家模型（那是第 8 项，默认关）。

### 18. 自动化密钥扫描（本轮草稿）

- **目标：** 每次推送都扫一遍工作区和提交，发现像密钥的字符串就让检查失败。
- **现状：** CI 用 gitleaks 8.28.0 扫描提交，配置在 `.gitleaks.toml`。`python checks/history_scan.py` 再按账号、群号、open_id、密钥和客户名查全部历史，只打印打码结果。`.gitignore` 已排除填好的配置、`redact.txt`、`.env`、记忆和日志。
- **参考：** [gitleaks](https://github.com/gitleaks/gitleaks)。
- **验收：** CI 里有一次密钥扫描。`agents/example.json` 的 `REPLACE_` 占位和测试里的虚构客户名不会被判失败。扫描告警里的原文不写进仓库。这一项不代替下面的公开前人工复查。

## 公开前清理

这一节只规定怎么查、怎么报告。查到了也不改写 Git 历史：不用 `git filter-repo`，不 rebase，不 amend 旧提交。要删历史里的真实数据，必须由维护者另作决定。

公开之前再运行一次 `python checks/history_scan.py`（CI 的 secret scan 作业每次推送也会跑）。它覆盖 `git rev-list --all` 里的每一个提交，至少包括：

- 飞书 `ou_` open_id、`on_` union_id、较长的 `oc_` 群号、`cli_` 应用编号、`om_` 消息编号
- App Secret、`sk-` 密钥、私钥、云厂商访问密钥、带真实口令的赋值
- 手机号、邮箱、用户目录路径
- 不带「某某」的公司名或客户名
- 提交说明和作者字段里的真实账号

报告只写种类、文件路径和提交号，值打码。占位符（`REPLACE_`）、文档和测试里的「小李」「小助手」「某某」公司、以及测试用的短 `oc_` 群号，要和真实数据分开写。

2026-10-18 对当时本地能看到的全部 44 个提交、1924 个文件版本查过一次，没有改写历史。文件内容里没有上述真实编号、密钥、手机号、邮箱或客户名。公司名都带「某某」，或是 `guard.py` 里用来打码的后缀规则。测试里的手机号是虚构的。短的 `oc_` 是测试群号。提交作者是维护者的 GitHub noreply 地址，以及云代理的维护邮箱，这两处在提交元数据里，不是密钥，也没有从历史里删掉。下一次公开前要按上面的范围再查，因为这之后还会有新提交。
