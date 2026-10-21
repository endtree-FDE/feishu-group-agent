# v0.7.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.7.0`。发布日期是 2026-10-20。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.6.0](release-0.6.0.md)。从 0.6.0 升上来要做的事见 [升级到 0.7.0](升级到0.7.0.md)。本说明不代替打 tag。

## 这一版给使用者的变化

旧的 `agents/*.json` 不改也能接着跑，行为和 0.6.0 一样：仍用 Cursor 回答，不额外联网搜索，已有功能开关保持原样。新键不写就是关，或保持原来的默认。

- 可替换的模型提供方。不写 `model` 时仍是 Cursor。`model.provider` 可写成 `openai_compatible` 或 `anthropic`。地址、模型名、温度、超时和最大 token 写在 `model` 里。密钥只写环境变量名，不能写进配置。没设置这个变量时，配置检查和 `--doctor` 会失败，并说明在当前窗口怎么设。见 [docs/换模型.md](换模型.md)。
- 可选的联网搜索 `search`，默认关。不写时不多出搜索工具。打开后，模型用 `web_search`，后端是 Tavily 或 SearXNG。密钥同样只写环境变量名。
- 主人可以在飞书私聊里开关功能。发「设置」会看到用量周报、知识检索、记忆成长、早间待办、断线提醒、技能。点「第 N 项」或回复数字后，先看改动摘要，再用原来的 6 位确认码。确认后备份、检查，再平滑重启，并且不会留下两个接收进程。失败会改回备份。见 [docs/新手开关功能.md](新手开关功能.md)。

私聊开关不能改模型配置、密钥和谁可以私聊。这些仍要改配置文件。群里不能改开关。

## 怎么单独打开

换模型和私聊开关互不牵连。不换模型时，不要写 `model`。不搜索时，不要把 `search.enabled` 设成 `true`。

只换接口、不改其他开关时，在你自己的 json 里写 `model`。四份示例和变量名见 [docs/换模型.md](换模型.md)。不要把密钥写进文件，也不要提交你填好的配置。

只在私聊里开关功能时，不用加新键。主人私聊机器人，发「设置」，按卡片一次改一项。群里的人只会被告知去找主人。

改完 JSON 后重启进程。用私聊开关确认的那一次，会自己备份并重启。

## 飞书权限

换模型不新加飞书权限。搜索没打开时也不多要权限。

私聊开关不新加权限。介绍和改完后的结果只发私聊，沿用原来的 `im:message:send_as_bot`。不要求打开对所有人的私聊。

## 还没做

企业微信不能拿来收消息。本说明不代替打 tag。标签 `v0.7.0` 由发布时另打。

## English

`VERSION` is `0.7.0`, released on 2026-10-20. This text is the release note for the upcoming tag `v0.7.0`. The program does not read this file. Omitting `model` keeps the Cursor answer path from v0.6.0. `search` stays off when omitted. The private-chat feature switch only toggles the existing usage report, knowledge search, memory growth, morning digest, disconnect alerts, and skills. It cannot change model settings, secrets, or who may use private chat. Old config files do not need new keys. Upgrade and rollback steps are in `docs/升级到0.7.0.md`. Stop and `git reset --keep v0.7.0` are one step. The rollback target is v0.6.0 (`4da9e42`). Do not put secrets or real Feishu ids in the release notes.
