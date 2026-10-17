# 配置怎么填

一份配置 = 一个机器人。复制 `example.json`，改名为你的机器人，例如 `agents/xiaojing.json`。用记事本或 VS Code 打开，按下面逐项改。

先记三句：

1. 以 `REPLACE_` 开头的，都必须换成你自己的，不能留着启动。
2. 「小助手」「小李」只是例子，改成你的机器人名字和同事的称呼。
3. **不要**在这个文件里写应用密钥（App Secret）。密钥只在你自己电脑上、运行初始化命令时输入一次，见 `docs/新建智能体.md`。

模型名字写在 `agent.model`。默认是 `grok-4.7-high-fast`。思考用的是**这台电脑上**已经执行过 `cursor-agent login` 的那个 Cursor 账号和额度。配置里不写账号，也不要写别人的账号。

下面按文件里的顺序说明每一项。

## 基本

| 键 | 填什么 |
|---|---|
| `name` | 这个机器人的短名字，只用英文和数字，例如 `xiaojing`。用来写日志。 |
| `base_dir` | 填放着 `agentbot.py` 的那个文件夹的完整路径，斜杠写成 `/`。看门狗只读这个代码文件夹里的 `state\heartbeat`。程序把心跳写在 `base_dir` 下面。两个地方不一致，看门狗会每 5 分钟重启一次。不要填成别的文件夹，也不要填别人的用户目录。 |
| `kb_dir` | 知识库文件夹，默认 `kb`。把资料 md 放在那里。 |
| `profile` | 你在自己电脑上配置 lark-cli 时起的名字。运行 `lark-cli config show` 能看到。不是应用密钥。 |
| `bot_open_id` | 机器人自己的编号。在群成员列表的 bots 里，抄 `member_id`。没填时，正式启动会被拒绝。演练模式可以暂时空着，但正式用之前必须填。 |
| `bot_names` | 机器人在群里显示的名字，可写多个。有人 @ 这个名字时，程序会把 @ 去掉再理解问题。 |
| `chat_ids` | 允许回答的群的编号，一个群一项。不在这里的群，即使被 @ 也不说话。编号只能含字母、数字、下划线和短横线。 |
| `conflict_pidfiles` | 旧程序的 pid 文件路径。那些进程还活着时，新程序拒绝启动，避免同一个应用回复两次。没有旧程序就留空列表 `[]`。 |
| `reply_in_thread` | `true` 表示回复在话题里，不打散群聊。一般保持 `true`。 |
| `max_concurrency` | 同时最多答几题。默认 `2`。 |
| `max_queue` | 排队最多几题，多出来的会回复「提问有点多」。默认 `4`。 |

## 多久能问一次

`rate_limit.min_gap_per_user`：同一个人至少隔多少秒才能再问。默认 `20`。

`rate_limit.max_per_hour`：所有人加起来，一小时最多答多少次。默认 `30`。超过就回复「提问有点多」。

## 开关 features

每一项都是 `true` 或 `false`。

| 键 | 打开之后 |
|---|---|
| `memory` | 记住**当前话题**最近几轮对话（短期）。关掉后，下一句不再接着上一句。 |
| `p2p` | 允许私聊。关掉后，私聊一律不回。 |
| `tools` | 允许查飞书（群历史、文档、日历、表格）以及按确认码写入。 |
| `attachments` | 允许看对方发来的图片和文件。 |
| `vision` | 允许把图片交给模型看。要和 `attachments` 一起开。 |

## 两套「记忆」，不要混

`memory` 这一段里有两类开关：

- `enabled`：长期记忆（「记一下」、`/记忆`、`/忘记`）。**默认 `false`。** 想用之前先看 `docs/记忆与命令.md`，确认没问题再改成 `true`。升级旧机器人时，先不要开，见 `docs/升级已有智能体.md`。
- `max_turns`、`ttl_hours`：短期话题记忆，配合上面的 `features.memory`。默认记住 6 轮，超过 24 小时就忘掉。
- `dir`：长期记忆放在哪个文件夹，默认 `memory`。每个群一个子文件夹，互不混用。
- `profile_budget_chars`：注入给模型的群档案最多多少字，默认 `1500`。超了会截断。
- `max_fact_chars`：一条「记一下」最多多少字，默认 `300`。
- `scope`：现在固定用 `topic`（按话题分开）。
- `per_user`：按人记一份档案。**先保持 `false`。** 个人记忆默认不开启。

## 谁可以私聊

`p2p.allow_open_ids`：允许私聊的人的编号列表。空列表 `[]` 时，看下一项。

`p2p.open_to_all`：`true` 表示谁私聊都回。`false` 且上面的列表是空的时，只有写入白名单里的人能私聊。

建议先保持 `false`，把要用的人写进 `allow_open_ids`。

## 模型 agent

| 键 | 填什么 |
|---|---|
| `mode` | 保持 `ask`。这是只读模式，模型不能改你电脑上的文件。 |
| `model` | 模型名字。默认 `grok-4.7-high-fast`。改成你的 Cursor 账号里能用的名字即可。不写这一项时，程序也用这个默认值。如果这个模型暂时不能用，程序会自动去掉模型名字再试一次。 |
| `network_retries` | 失败后再试几次。默认 `2`。 |
| `max_runtime_sec` | 一道题最长多久（秒）。默认 `1800`（30 分钟）。到点就停，并尽量把已经写出的部分发出去。 |
| `stall_sec` | 多少秒完全没有新输出，就当成卡住。默认 `300`。 |
| `max_chars` | 回答最多保留多少字。默认 `3500`。 |

这里没有账号、没有密码、没有密钥。额度算在运行 `cursor-agent login` 的那个人头上。

## 脱敏和红线

`redact_file`：客户名名单的文件名，默认 `redact.txt`，放在 `base_dir` 里。一行一个名字。复制 `redact.example.txt` 再填。名单为空时，只打码手机号、证件号、邮箱、密钥样子的字符串和电脑路径。

`policy.redlines`：话题红线。**默认是空列表，不拦截任何话题。** 框架里不写死「不许聊什么」。想加规则时，照下面的例子写（这只是例子，关键词请换成你自己要限制的）：

```json
"policy": {
  "redlines": [
    {
      "name": "薪资与报价",
      "keywords": ["薪资", "工资", "报价单"],
      "action": "refuse",
      "apply_to": ["input", "output", "memory", "progress"],
      "reply": "这个话题请直接找负责人沟通。"
    }
  ]
}
```

- `action` 为 `refuse`：入口直接拒答。`drop`：只在出口删掉命中的段落。
- `apply_to` 决定在哪生效：`input` 入口、`output` 出口、`memory` 不记、`progress` 进度卡片里不显示、`kb` 知识库同步时删段落。不写 `apply_to` 则每一处都生效。
- 旧键 `taboo_keywords` 还能认，新配置不要再用。

`help_pattern`：对方问「你能干什么」时，用这段规则判断，然后直接回复 `texts.help`，不去叫模型。看不懂就保持例子里的原文。

## 工具 tools

每一项的 `enabled` 为 `true` 才允许模型使用。没配好编号时，调用会告诉对方「还没配置」，不会乱写。

| 键 | 作用 |
|---|---|
| `history` | 读本群最近几天的消息。`days` 默认 7，`max_pages` 默认 2。私聊里不提供。 |
| `docs_search` | 搜索已分享给机器人的飞书文档。 |
| `docs_read` | 读一份文档。`allow_tokens` 为空表示：分享给机器人的都能读。想限制就填文档编号。`max_chars` 是最多读多少字。 |
| `calendar_read` | 读日历。`calendar_id` 可空，空则用下面 `write.resources.calendar_id`。 |
| `base_read` | 读多维表格。`base_token`、`table_id` 换成你的表。`search_fields` 是可搜索的列名，没有就留 `[]`。 |
| `attachment` | 下载图片和文件。`max_mb` 是单文件上限，默认 20。 |
| `docs_create` | 在专用文件夹新建文档。要确认码。 |
| `docs_append` | 在白名单文档末尾追加。要确认码。 |
| `calendar_create` | 在团队日历建日程，不邀请任何人。要确认码。 |
| `base_create` | 在表格里加一条记录。要确认码。 |

工具名字是 `docs_search`、`docs_read`、`docs_create`、`docs_append`（docs 带 s）。如果你从旧机器人抄配置，旧名字少一个 s，要改成现在这样。

## 写入 write

三道锁都要过，才会真正改飞书。详见 `docs/安全红线.md`。

| 键 | 填什么 |
|---|---|
| `enabled` | `true` 才允许提议写入。升级时可以先改成 `false`，等只读稳定了再开。 |
| `confirm_ttl_sec` | 确认码有效秒数，默认 `300`（5 分钟）。 |
| `allowed_users` | 谁可以发起写入、谁可以改群记忆。左边是这个人的编号，右边是称呼，例如 `"REPLACE_USER_OPEN_ID": "小李"`。右边的称呼只用于回复和审计，改成真人的称呼。 |
| `resources.folder_token` | 专用文档文件夹的编号。新建文档只进这个文件夹。 |
| `resources.docs_append_tokens` | 允许在末尾追加的文档编号列表。空列表表示还不能追加。 |
| `resources.calendar_id` | 专用日历的编号。 |
| `resources.base_token` / `table_id` | 允许新增记录的那张表。读和写可以用同一张，也可以分开。 |
| `base_time_field` | 加记录时，如果没有这一列，就自动填上当前时间。不需要就改成 `""`。 |
| `base_select_fields` | 飞书里的单选列。程序会把纯文字包成列表再写入，否则飞书会报格式错误。例子里是「状态」。你的表没有单选列就改成 `[]`。 |

这些编号都不是密钥，但它们指向你的真实文档和表。不要把填好的配置发到公开的地方。

## 进度卡片 progress

`enabled` 默认 `false`：不发表情、不发「处理中」卡片，直接等答案。升级时先保持关闭，只读跑稳了再改成 `true`。

打开后需要权限：表情 `im:message.reactions:write_only`，更新消息 `im:message:update`，卡片 `cardkit:card:write`。缺了也不会崩，只是没有进度提示。

| 键 | 含义 |
|---|---|
| `mode` | `card` 是一张卡片（思考过程默认收起）。`text` 是一条文字，答完后这条文字会被换成答案。 |
| `reaction` | 收到问题时贴的表情，默认 `OnIt`。 |
| `done_reaction` | 答完换成的表情，默认 `DONE`。 |
| `status_message` | 要不要发进度消息。 |
| `thinking_panel` | 卡片里要不要「思考过程」。会先去掉路径和客户名。 |
| `thinking_chars` | 思考过程最多保留多少字。 |
| `card_interval_sec` / `card_max_updates` | 卡片多久刷新一次、最多刷新多少次。 |
| `min_gap_sec` | 两次刷新至少隔几秒。 |
| `fast_interval_sec` / `fast_phase_sec` / `slow_interval_sec` / `max_edits` | 文字模式用：前两分钟刷新密一些，之后变疏。文字消息大约只能改 20 次，所以 `max_edits` 留了余量。 |

## 固定回复 texts

对方看到的几句现成话。可以改成你的机器人的口气。

| 键 | 什么时候用 |
|---|---|
| `busy` | 问得太密，或排队满了。 |
| `empty` | @ 了但没有写问题。 |
| `fail` | 模型没有给出回答。 |
| `help` | 对方问「你能干什么」。 |
| `partial_note` | 时间到了，但已经写出一部分，贴在答案后面。 |
| `stall` | 时间到了，一点文字都没有。 |
| `policy_refuse` | 命中红线、又没给那条规则单独写回复时用。 |

## 人设 system_prompt

模型每次都会先读这段。写成你希望它怎么说话、什么不能做。例子是一个通用助手。请改成你的机器人的名字和边界。不要写客户真名，不要写密钥，不要写电脑路径。

## 知识库同步 kb_sync

`docs_tokens`：要从飞书拉进知识库的文档编号列表。空列表表示不拉。同步只进待审区，你确认后才替换正式知识库。见 `docs/新建智能体.md` 最后一节。

## 演练开关

`dry_run_live_reads`：演练时要不要真的去读飞书。默认 `false`，演练只在屏幕上打印命令，不发消息。不要为了省事改成 `true`，除非你知道自己在演练、并且只读。

写操作在演练里默认也不真的执行。只有你本人在环境变量里打开 `AGENTBOT_DRY_LIVE_WRITES=1` 时才会真的写。验收时才这么做，并且用单独的测试表。
