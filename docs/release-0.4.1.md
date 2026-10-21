# v0.4.1 发布说明

版本号是仓库根目录的 `VERSION`：`0.4.1`。发布日期是 2026-10-19。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.4.0](release-0.4.0.md)。从 0.4.0 升上来要做的事见 [升级到 0.4.1](升级到0.4.1.md)。

## 这一版给使用者的变化

- （#58）写入确认码绑定发起人、会话和话题。主聊天的码不绑单条消息编号。别人、别的群、别的私聊、别的话题都不能确认。发错不会把码用掉。发送人或消息编号为空时不能发起或确认。用过或取消过的码先记入已用名单，重启后也不能再执行。还没使用且未过期的码，重启后仍然有效。新码不会覆盖还没确认的操作。
- （#58）引用、历史、网页说明、飞书文档、PDF、Word、PPT、Excel、压缩包、图片说明、长期记忆和工具结果里的工具标记、伪造确认码会被消掉。当前这条用户问题仍可正常调用工具。
- （#58）上网前的跳转探测只连接已经检查过的地址。域名解析到 `198.18.0.0/15` 的假 IP 代理仍然放行，裸 IP 和跳进内网仍然拒绝。
- （#58）Word、PPT、Excel 和知识库压缩包成员超过体积或压缩比时跳过。PDF 页数异常时整份跳过，不执行里面的脚本。启动时清掉超过一小时的临时文件。
- （#58）客户名多认全角空格、组合附加符号、拆开的短名，以及名单里拼音的声调和连字符。没有写进名单的拼音仍不自动隐藏。带空格或连字符的手机号、`Bearer` 和 `sk-` 样子的密钥会打码。日志里的接口错误和模型报错也会打码。
- （#58）同一个飞书应用只能有一个接收进程。另一个目录再启动会被拒绝，不会结束已经在跑的那个。`stop.ps1` 仍按本目录和 profile 判断，不会误杀别的机器人。
- （#58）单实例锁和按应用的锁先写完整内容，再让别的进程看见。启动的一瞬间，另一个进程不会把还没写完的空文件当成过期锁并接管。
- （#58）确认、状态、帮助和记忆命令不再受单人提问间隔限制，但每分钟有命令次数上限。不写 `rate_limit.max_commands_per_min` 时是 40。写成 `0` 表示不限这条。
- （#59）私聊可以按群成员放行。`p2p.allow_group_members` 默认 `false`，不写时不查群成员，和 0.4.0 一样。设为 `true` 后，当前在所服务群里的成员可以私聊。`allow_open_ids` 或写入白名单里的人仍然可以。`open_to_all` 优先。成员名单默认缓存 600 秒（`p2p.member_cache_ttl_sec`）。查成员失败、超时、缺权限或名单不完整时拒绝，不回复群外的人。需要应用权限 `im:chat.members:read`。

## 兼容

旧的 `agents/*.json` 不必为了升级而补键。没写 `p2p.allow_group_members` 时不按群成员放行。

有一处比 0.4.0 宽：`allow_open_ids` 非空时，写入白名单里的人即使不在这份名单里也可以私聊。以前私聊名单非空时，不在名单里的人一律不回。

确认码和写入白名单没有放宽。不在写入白名单里的人仍然不能执行写入。`state/state.db` 会在用到确认码时自己建 `used_writes` 表，不用手工迁移。

## 还没做

每晚用模型重写记忆默认不跑。不主动往群里发消息。本说明不代替打 tag。

## English

`VERSION` is `0.4.1`, released on 2026-10-19. This text is the release note for the upcoming tag `v0.4.1`. The program does not read this file. (#58) Confirmation codes are bound to the initiator, chat, and topic. Used or cancelled codes are recorded before they run and cannot be replayed after a restart. Untrusted text cannot become a tool call or a confirmation code. Redirect checks connect only to addresses that were already validated. One Feishu app has one receiver. Lock files become visible only after they are fully written. Confirm, status, help, and memory commands skip the per-user gap and use a per-minute cap. (#59) `p2p.allow_group_members` defaults to false. When enabled, current members of served groups may use private chat, which needs `im:chat.members:read`. `open_to_all` still wins, and a failed or truncated member list fails closed. Upgrade steps are in `docs/升级到0.4.1.md`. Do not put secrets or real Feishu ids in the release notes.
