# v0.9.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.9.0`。发布日期是 2026-10-20。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.8.1](release-0.8.1.md)（`66478cb`）。

## 这一版加了什么

旧的 `agents/*.json` 不改也能接着跑。调用记录默认开，另外三项默认关，不开就和 0.8.1 一样。四项都不新开收消息进程，写入仍走白名单和确认。

- **调用记录**（默认开）。每轮回答在本机 `logs/calls.jsonl` 记一条调用链：收到的消息、用过的技能和工具、每步耗时和 token、结果。写入前脱敏，客户名写成「某客户」，不记密钥。`observability.calls.retain_days` 不写是 30 天。主人私聊发「最近调用」或「这条怎么答的」可以看。用量周报在有记录时多一段合计。要关掉，把 `observability.calls` 写成 `false`。见 [调用记录](调用记录.md)。
- **复盘写回**（默认关，`memory.retrospect.enabled`）。一轮群聊做完后，把「做了什么、哪里出错、下次怎么做」写成一条记忆提案，走现有的「确认记忆」。审批人点头才写入这个群，驳回或不回复不写。需要长期记忆开着，审批人用 `memory.growth.approver_open_ids`。不另调模型。见 [复盘写回](复盘写回.md)。
- **技能自动改进**（默认关，`skills.evolve.enabled`）。技能有版本和基线哈希。主人私聊发「改进技能」时，根据失败的调用和复盘提议下一版。`evals/run.py` 的分数必须更高（持平也不换），并且用原来的确认方式点头，才替换 `SKILL.md`。发「回滚技能 名字」并确认，可以回到上一版。不执行技能里的脚本。见 [技能自动改进](技能自动改进.md)。
- **主理人加专员**（默认关，`agent.crew.enabled`）。长问题或同时要做几件事时，拆成查资料、读文档、起草，再汇总。短句不拆。专员只有只读工具，不能写入。总预算不写时是 16 轮、40 万 token、900 秒；每一步不写时是 6 轮、12 万 token、240 秒。见 [主理人与专员](主理人与专员.md)。

后三项也可以由主人在私聊里发「设置」打开：「复盘写回」「技能改进」「主理人」。从「设置」打开时，会把它们依赖的长期记忆、技能或工具一起打开。

## 从 0.8.1 换上

看门狗大约每 3 分钟看一次心跳。停进程和换代码必须在同一步里。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
if (-not $?) { throw "stop.ps1 没停成，先不要换代码" }
git fetch origin --tags
git reset --keep v0.9.0
if (-not $?) { throw "代码还没换成这一版，先不要启动" }
```

然后再用原来的 `scripts\start.ps1` 启动。`git reset --keep` 留下配置、`state`、`kb`、`memory`、`redact.txt` 和 `logs`。不要用 `git reset --hard`，也不要 `git clean`。

换上之后：

```text
python checks/check_config.py agents\<名字>.json
python agentbot.py agents\<名字>.json --doctor
```

两条都不发消息。

## 退回 0.8.1

退回目标是 `v0.8.1`（`66478cb`）。同样先停，停成了立刻换回。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
if (-not $?) { throw "stop.ps1 没停成，先不要换代码" }
git reset --keep v0.8.1
if (-not $?) { throw "代码还没换回去，先不要启动" }
```

`logs/calls.jsonl` 会留着，0.8.1 不读它。

## 还没做

企业微信不能拿来收消息。

## English

`VERSION` is `0.9.0`, released on 2026-10-20. Old config files keep working without new keys. New: per-turn call traces in `logs/calls.jsonl` (on by default, redacted, `observability.calls`); retrospective memory proposals that need approval (`memory.retrospect.enabled`, off); skill evolution that only replaces `SKILL.md` when the eval score is strictly higher and the owner confirms (`skills.evolve.enabled`, off); a lead agent with read-only specialists and bounded budgets (`agent.crew.enabled`, off). Writes still go through the allowlist and confirmation. Rollback target is `v0.8.1` (`66478cb`).
