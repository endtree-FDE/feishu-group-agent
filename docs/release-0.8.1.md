# v0.8.1 发布说明

版本号是仓库根目录的 `VERSION`：`0.8.1`。发布日期是 2026-10-20。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.8.0](release-0.8.0.md)（`8604f62`）。本说明不代替打 tag，也不合并。标签 `v0.8.1` 由发布时另打。

## 这一版修什么

0.8.0 把写入确认默认改成点卡片，并多拉起一个 `card.action.trigger` 接收进程。飞书后台没开通卡片回调时，这个进程一启动就退出，报 `failed_precondition`：`EventKey card.action.trigger requires callbacks not subscribed in console`，退出码 2。之后大约每 60 秒重启一次。

两条接收进程共用一个连接状态。卡片进程一退出，就把整条长连接记成断了。收消息的 `im.message.receive_v1` 其实还连着，健康检查却一直判定断线，反复给主人发断线提醒。

旧的 `agents/*.json` 不改也能接着跑。没写 `confirm.card_callback` 时按 `auto`。

- 连接状态按接收进程分开记。只有收消息的长连接断了才算机器人断线。卡片进程异常只记一条警告，不发断线提醒。进群、出群进程同样不拿来判断机器人断没断。
- 启动前先探测卡片回调。探测到后台没订阅（日志里有 `failed_precondition` 或 `not subscribed`，或者退出码是 2），这次运行就不再启动、也不再重启卡片进程。确认自动改回确认码。私聊只提醒主人一次，说明怎么开通。
- 可以配置 `confirm.card_callback`：`auto`（默认）、`on`、`off`。`on` 仍会去启动，但没订阅时退出后不再重启。`off` 不启动卡片进程，确认用确认码。
- `--doctor` 会把这件事说清楚：没开通不算断线，接收进程数量也不把 `card.action.trigger` 算进去。已经连上过才把这个进程算进应有的数量。`WARN` 仍然不让诊断失败。

## 怎么开通

不需要公网地址。到飞书开放平台，进入这个应用：

1. 打开「事件与回调」。
2. 订阅方式选「使用长连接接收事件」。
3. 添加事件「卡片回传交互」（`card.action.trigger`）。
4. 发布版本。

开通并重新启动之后，点按钮会自己恢复。不想用卡片时，在配置里写 `"confirm": {"card_callback": "off"}`，或者把 `confirm.mode` 写成 `code`。

## 从 0.8.0 换上

看门狗大约每 3 分钟看一次心跳。进程停了而代码还是旧的，它会把旧程序再拉起来。所以停进程和换代码必须在同一步里。

标签还没打。不要 `git reset` 到一个还不存在的 `v0.8.1`。先取 `origin/main` 上的这一版，或取这次修复所在的提交。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
if (-not $?) { throw "stop.ps1 没停成，先不要换代码" }
git fetch origin main
git reset --keep origin/main
if (-not $?) { throw "代码还没换成这一版，先不要启动" }
```

然后再用原来的 `scripts\start.ps1` 启动。`git reset --keep` 留下配置、`state`、`kb`、`memory`、`redact.txt` 和 `logs`。不要用 `git reset --hard`，也不要 `git clean`。

换上之后：

```text
python checks/check_config.py agents\<名字>.json
python agentbot.py agents\<名字>.json --doctor
```

两条都不发消息。卡片回调没开通时，诊断是警告，不是失败。收消息的接收进程仍应有一个。

## 退回 0.8.0

退回目标是 `8604f62`。当时 `VERSION` 是 `0.8.0`。同样必须同一步：先停，停成了立刻换回。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
if (-not $?) { throw "stop.ps1 没停成，先不要换代码" }
git reset --keep 8604f62
if (-not $?) { throw "代码还没换回去，先不要启动" }
```

0.8.0 在没开通卡片回调时仍会反复重启卡片进程，并可能误发断线提醒。线上若还不能开通回调，应留在这一版，或把确认改成确认码之后再考虑退回。

## 还没做

企业微信不能拿来收消息。本说明不代替打 tag。

## English

`VERSION` is `0.8.1`, released on 2026-10-20. This note does not create the tag and does not merge. The program does not read this file. Old config files do not need new keys. `confirm.card_callback` defaults to `auto`. If the Feishu console has not subscribed to `card.action.trigger`, that consumer is not restarted, write confirmation falls back to the code, and the owner is notified once. Only the `im.message.receive_v1` consumer marks the bot offline. `--doctor` does not count the card consumer until callbacks are connected, and a missing card consumer is not a disconnect. Rollback target is `8604f62` (`v0.8.0`). Do not put secrets or real Feishu ids in the release notes.
