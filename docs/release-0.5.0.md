# v0.5.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.5.0`。发布日期是 2026-10-19。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.4.1](release-0.4.1.md)。从 0.4.1 升上来要做的事见 [升级到 0.5.0](升级到0.5.0.md)。

## 这一版给使用者的变化

四项都是默认关。旧的 `agents/*.json` 不改也能接着跑，行为和 0.4.1 加上后来并进主线的创作技能、渠道层一样：技能不进提示词，渠道仍是飞书，不发断线私聊，也不往群里发定时消息。

- 六份创作流程技能，仍放在 `skills/<名字>/SKILL.md`。分别是选题打分（`topic-ideation`）、短视频口播稿（`short-video-script`）、分镜表（`storyboard`）、不绑定某一家工具的提示词包（`aigc-prompt-pack`）、项目策划书（`creative-brief`）、创作复盘（`post-mortem`）。`agents/example.json` 里 `skills.enabled` 仍是 `false`，`skills.only` 仍是 `[]`。不改配置时，提示词和没有这些文件时一样。用户明确要求保存时，才提议已有的 `docs_create`、`docs_append` 或 `base_create`。确认码和写入白名单不变。写法见 [docs/技能.md](技能.md)。
- 渠道适配器。收事件、发消息、改卡片、读历史和附件、查成员、认身份都从 `channels/` 出去。没写 `channel` 时仍是飞书，命令和配置键不变。企业微信只是实验骨架：不写 `channels.experimental`，也不设 `AGENTBOT_EXPERIMENTAL_CHANNELS=1` 时不会加载；就算加载了，`ready()` 也是假的，不会连接。见 [docs/渠道.md](渠道.md) 和 [docs/渠道-企业微信.md](渠道-企业微信.md)。
- 断线提醒。长连接连续中断，或心跳超过阈值，才私聊配置里的主人；恢复后再发一条。短时间闪断不发。发送走当前渠道的主动发送，不走事件长连接。`--alert-check` 给看门狗用，不拿单实例锁，不启动收消息的进程。细节见 [docs/断线提醒与晨报.md](断线提醒与晨报.md)。
- 早晨摘要。只有打开后，才在设定的本地时间往指定的、并且已经在服务范围内的群发一条今天的日历和未完成任务。每个群每个本地日最多一条。不点名所有人。这是框架会主动发到群里的唯一一种消息。

`/状态` 只在对应开关打开时多一行。

## 断线提醒的配置样子

例子里的开关是关的。`owner_open_ids` 留空，不要把真实 open_id 写进仓库。

```json
"alerts": {
  "enabled": false,
  "owner_open_ids": [],
  "unhealthy_after_sec": 180,
  "repeat_sec": 1800,
  "local_log": true,
  "windows_toast": false
}
```

`unhealthy_after_sec` 和 `repeat_sec` 不写时分别是 180 和 1800。写成 `0`、负数或无法转成数字时，仍用这两个默认值。看门狗重启进程的条件仍是心跳超过 3 分钟。想让进程死掉时也能通知，这项留着 180。`windows_toast` 默认关。

早晨摘要的键在 `agents/example.json` 的 `digest` 里，同样是 `enabled: false`。时间不写是 `08:30`，时区不写是 `Asia/Shanghai`。

## 飞书权限

断线私聊不新加权限。本来就要有 `im:message:send_as_bot`。主人要在应用可用范围里。如果飞书返回不在会话中，让主人先给机器人发一条私聊。不要求打开 `features.p2p`，也不要求 `im:message.p2p_msg:readonly`。飞书没发出去时，本机 `logs/alerts.log` 仍会记一条，日志里不写 open_id。

早晨摘要要求机器人已经在目标群里，并且仍用 `im:message:send_as_bot`。日历编号是真实的（不是空的，也不是 `REPLACE_` 开头）时，还要 `calendar:calendar.event:read`。任务表的 `base_token` 和 `table_id` 都是真实的时，还要 `base:record:read`。占位符不会去读接口。改完权限后要发布应用版本。摘要正文不会点名所有人。

## 还没做

企业微信不能拿来收消息或发告警。每晚用模型重写记忆默认不跑。本说明不代替打 tag。

## English

`VERSION` is `0.5.0`, released on 2026-10-19. This text is the release note for the upcoming tag `v0.5.0`. The program does not read this file. Creative-workflow skills, the channel adapter, disconnect alerts, and the morning digest are all off unless you turn them on. Omitting `channel` stays on Feishu. The WeCom adapter is an experimental skeleton: it does not load unless `channels.experimental` is true, and even then it does not connect. Alert private messages and digest posts go through the channel `push_text` method. On Feishu that calls `im +messages-send` and does not start the event consumer. Alerts need the existing `im:message:send_as_bot` scope. A real calendar id also needs `calendar:calendar.event:read`. A real task table also needs `base:record:read`. The digest never mentions everyone and posts at most once per local day per chat. Upgrade and rollback steps are in `docs/升级到0.5.0.md`. The rollback target is v0.4.1 (`deb498f`). Do not put secrets or real Feishu ids in the release notes.
