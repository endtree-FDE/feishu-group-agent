# v0.6.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.6.0`。发布日期是 2026-10-20。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.5.0](release-0.5.0.md)。从 0.5.0 升上来要做的事见 [升级到 0.6.0](升级到0.6.0.md)。本说明不代替打 tag。

## 这一版给使用者的变化

四项都是默认关。旧的 `agents/*.json` 不改也能接着跑，行为和 0.5.0 一样：不到点发用量周报，不建分群索引，不提议新的长期记忆，正式运行也不读回归用例。

- 回答质量回归。`python evals/run.py` 把编好的问题送进正式回答流程，渠道换成内存适配器。不连接飞书，不发消息，不拿单实例锁。用例在 `evals/cases/suite.json`，假模型基线在 `evals/baseline/ci.json`。CI 只跑假模型那一档。正式运行的机器人不读这些文件。见 [docs/回归测试.md](回归测试.md)。
- 用量周报 `report`。打开后，按星期和时间（不写是周一 09:00、`Asia/Shanghai`）把上一周的合计私聊给主人，每周一次，重启不重发。正文只有合计，没有问题原文、人名、open_id 或群编号。见 [docs/用量周报.md](用量周报.md)。
- 分群知识库检索 `knowledge`。打开后，每个群或私聊一份本地 SQLite 索引。回答按标题、章节或页码引用。没打开时，提示词、工具和 `/状态` 与 0.5.0 一样。见 [docs/知识库.md](知识库.md)。
- 记忆成长 `memory.growth`。长期记忆和这项都打开后，才会在配置的时间（不写是 03:00）或一段对话闲下来时提议新事实、更新或作废。审批人确认之前不写账本。见 [docs/记忆成长.md](记忆成长.md)。

`/状态` 只在对应开关打开时多一行。

## 怎么单独打开

四项互不牵连。只开一项时，另外三项保持关闭。

回答质量回归没有运行时开关。升级前在仓库根目录跑假模型那一档即可，不改 `agents/*.json`：

```text
python evals/run.py --config agents/example.json --provider fake --tag ci --baseline evals/baseline/ci.json --out evals/out
```

只开用量周报时，在你自己的 json 里只改这一处。不要同时把 `knowledge.enabled` 或 `memory.growth.enabled` 设成 `true`。

```json
"report": {
  "enabled": true
}
```

`weekday`、`time`、`timezone`、`window_min` 不写，就是周一 `09:00`、`Asia/Shanghai`、窗口 180 分钟。`report.owner_open_ids` 不写时，用已经写在 `alerts.owner_open_ids` 里的主人；不要求 `alerts.enabled` 已经打开。两边都空着就不会发。编号只放在你自己的配置里，不要提交。主人可以在私聊发 `/report` 再看一份，不占每周名额。群里发这条只回一句已私聊。没打开时，`/report` 仍交给模型。不发送、只在本机看：`python agentbot.py <配置> --report`。`enabled` 为 false 时这条命令仍可用。

分群知识库的最小打开方式见 [docs/知识库.md](知识库.md)。`enabled` 设成 `true` 才收录、才检索。不配向量地址时只用关键词。

记忆成长写在 `memory.growth` 里。`memory.enabled` 和 `growth.enabled` 都要是 `true`，并且 `approver_open_ids` 里有审批人。缺一个就不跑，也不改「记一下」、`/记忆`、`/忘记`。成长关着时，`/忘记` 仍是立刻删除。

改完 JSON 后重启进程。

## 飞书权限

用量周报不新加权限。本来就要有 `im:message:send_as_bot`。主人要在应用可用范围里。如果飞书返回不在会话中，让主人先给机器人发一条私聊。不要求打开 `features.p2p`。日志和周报正文都不写 open_id。

分群知识库没打开时，`--doctor` 不会因为这项多要权限。打开之后，只收本地 `kb/` 不加权限；群文件、云文档、知识库节点、多维表格各用原来的只读权限。资料里的网址不会被拿去访问。

记忆成长没打开时不多要权限。私聊送达沿用 `im:message:send_as_bot`。要看本群近期消息时，仍用已经打开的群历史权限。提案确认码和写入确认码是两套，都不能由别人或别的群使用。

## 还没做

企业微信不能拿来收消息或发周报。本说明不代替打 tag。标签 `v0.6.0` 由发布时另打。

## English

`VERSION` is `0.6.0`, released on 2026-10-20. This text is the release note for the upcoming tag `v0.6.0`. The program does not read this file. Answer-quality evals, the weekly usage report, per-chat knowledge search, and memory growth are all off unless you turn them on. Omitting the keys keeps v0.5.0 behavior. Evals do not connect to Feishu and do not take the single-instance lock. A report posts at most once per week to the owners and never includes question text or ids. Knowledge search indexes one chat at a time and cites title and section or page. Memory growth writes nothing until an approver confirms. Upgrade and rollback steps are in `docs/升级到0.6.0.md`. Stop and `git reset --keep v0.6.0` are one step. The rollback target is v0.5.0 (`e842684`). Do not put secrets or real Feishu ids in the release notes.
