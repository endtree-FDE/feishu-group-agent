---
name: 飞书群机器人模板
description: >-
  用本仓库帮不会写程序的人从零搭一个飞书群机器人，或把他已有的简单飞书机器人升级成这套
  （长期记忆、进度卡片、确认码写入、看门狗）。模型从配置读取。没写
  model_provider 时用对方电脑上的 Cursor；只有这条路在 agent.model 留空时才用
  grok-4.7-high-fast。换成别的模型接口时以配置里的模型名为准。登录和额度用对方自己
  电脑上的 cursor-agent login，或对方自己的环境变量。不要把应用密钥或真实编号写进仓库。
---

# 飞书群机器人模板

给人看的步骤在 `docs/`。你按那些步骤执行命令，对方只做必须本人做的事。配置每一项以 `agents/CONFIG.md` 为准。安全规则以 `docs/安全红线.md` 为准，不要放宽。

## 你必须遵守

- 应用密钥（App Secret）不进 json、不进仓库、不进你的回复、不进群。让对方本人在自己电脑上运行：`lark-cli config init --app-id 对方的应用ID --app-secret-stdin --brand feishu`，然后自己粘贴密钥并回车。你不要代贴。
- 模型看 `model_provider` 和 `agent.model`。没写 `model_provider` 时用这台电脑上的 Cursor。只有这条路在 `agent.model` 留空时才用 `grok-4.7-high-fast`。DeepSeek、通义、智谱、盘古或别的 OpenAI 兼容地址以配置里的模型名为准，不会套这个名字。额度是对方自己电脑上 `cursor-agent login` 的账号，或对方自己的环境变量。不要写死任何账号，不要改用你自己的登录去顶替对方。
- 不往群里发测试消息。验收请一位真人私聊一次、在群里 @ 一次。
- 不删除、不覆盖正文、不改权限、不邀请他人。写操作只有白名单的人、白名单的资源、白名单的命令，加上本人 6 位确认码。
- 不主动发消息。看门狗只写本地日志。
- 对方机器上的配置会有真实群编号和文档编号，这是正常的。不要把填好的配置、`redact.txt`、`logs/`、`memory/` 提交进这个模板仓库。
- 话题红线默认空。不要擅自加屏蔽词。对方要限制话题时，按 `agents/CONFIG.md` 写 `policy.redlines`。

## 从零新建

按 `docs/新建智能体.md` 的编号做完再进入下一步。

1. 对方在开放平台创建企业自建应用，把 App ID 给你。Secret 不给你。
2. 用文档里的一键链接申请权限（把 `clientID` 换成 App ID）。事件订阅用长连接，事件名 `im.message.receive_v1`。然后由对方发布版本并设置可用范围。
3. 在对方的 Windows 上安装 Python 3.12（勾选加入 PATH）、Node.js、`npm install -g @larksuite/cli`、Cursor。由对方运行 `cursor-agent login`。
4. 由对方本人完成 `lark-cli config init`。你用 `lark-cli config show` 确认 profile 名字，不要把输出贴到公开处。
5. 机器人拉进群后：`lark-cli im +chat-list --as bot --profile 对方的profile --page-all --format json` 抄群编号；`lark-cli im +chat-members-list --chat-id 群编号 --as bot --profile 对方的profile --page-all --format json` 抄机器人的 `member_id` 和白名单成员的 `member_id`。对方如果要用自动认群，按 `docs/升级已有智能体.md` 的「14 附」把 `chat_ids` 留空，不要代他们在群里发 `/认群`。
6. 复制 `agents/example.json` 为 `agents/<短名>.json`。按 `CONFIG.md` 填。`REPLACE_` 全部换掉。`memory.enabled` 和 `progress.enabled` 先保持 `false`。`base_dir` 用 `.`。人设改成这个机器人的名字，不写客户真名。
7. 复制 `redact.example.txt` 为 `redact.txt`。没有确认过的客户名就先空着。
8. 在仓库根目录运行 `python tests/run_tests.py`。必须全部通过。这一步不联网。
9. 需要演练时用 `--dry-run 演练.jsonl`。这会调用对方电脑上的 cursor-agent，花的是对方的额度，但不会发到飞书。不要用管道喂中文。
10. `scripts\start.ps1 -Config agents\<短名>.json`。`logs\bot.log` 出现 `ready` 或 `feishu-websocket: connected` 才算成功。
11. `scripts\register-tasks.ps1 -Mode logon -Config agents\<短名>.json -Name <短名>`。开机也要跑才用 `-Mode boot`，密码由对方本人输入，不落盘。知识库任务用 `scripts\kb-sync-task.ps1`，只进待审，对方点头后才 `--apply`。
12. 按新建文档第 14 步做上线检查。通过后，若对方需要，再单独打开记忆，验证，再单独打开进度。一次一个。

缺权限时程序回复「这项权限未开」，错误码常见 99991672。补权限后要重新发布。

## 升级已有机器人

按 `docs/升级已有智能体.md`。读者时间有限，不要合并步骤。

1. 整份复制旧目录做备份。不在旧目录上覆盖。
2. 只迁移配置里的名字、profile、机器人编号、群编号、白名单、资源编号、人设。不迁移 Secret。工具名改成带 s 的 `docs_search`、`docs_read`、`docs_create`、`docs_append`。
3. 新目录先跑通测试，再停旧进程（只停不删），启动新的。
4. 停用会拉起旧目录的计划任务，不要删除。确认只有一个 python 在跑。
5. 先不要开记忆和进度。真人验收问答之后，再打开 `memory.enabled`，验证「记一下 / /记忆 / /忘记」，再打开 `progress.enabled`。
6. 失败：`scripts\stop.ps1`，启动备份。或用 `scripts\switch-from-old.ps1 -Old 旧目录 -Config agents\<短名>.json`，约 90 秒内日志没有 ready 或 connected 会自动退回。脚本不能代替备份。

## 命令（对方在群里用）

- `记一下：主题：内容`、`记住：`、`备忘：`：白名单才能写入群记忆。同主题新的取代旧的。
- `/记忆`：查看。`/忘记 编号`：真删。`/忘记全部` 然后 `确认清空 六位数`：清空，码只对本人、一次有效。
- `新话题` 或 `/new`：只清当前话题的短期上下文，不清长期记忆。
- `确认 六位数` / `取消 六位数`：写操作。非本人无效。
- 私聊不写群记忆。详见 `docs/记忆与命令.md`。

## 排错顺序

看 `logs/bot.log`。没有 Q 行：事件、发布、可用范围、旧进程抢消息。有 Q 但 agent 失败：模型名、`cursor-agent login`、版本目录是否完整（要同时有 node.exe 和 index.js）。tool degraded：开权限或把资源分享给应用。reply 失败：网络和发送权限。重复回复：单实例锁和旧的自启项。

Windows 上 ps1 必须是 UTF-8 带 BOM。不要用管道调用 `start.ps1`。判断进程是否还在，不要解析 tasklist 的中文输出。

## 架构

一条消息怎么走、记忆怎么落盘：`docs/架构.md`，图是 `docs/arch-full.mmd` 和 `docs/memory-flow.mmd`。例子里的人名和活动名是编的，不要换成真实客户。
