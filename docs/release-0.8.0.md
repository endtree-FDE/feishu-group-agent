# v0.8.0 发布说明

版本号是仓库根目录的 `VERSION`：`0.8.0`。发布日期是 2026-10-20。程序不读取 `VERSION`，也不读取这份说明。正文不要写密钥、群号、open_id、客户名或个人电脑路径。

这一版相对 [v0.7.0](release-0.7.0.md)。从 0.7.0 升上来要做的事见 [升级到 0.8.0](升级到0.8.0.md)。本说明不代替打 tag。

## 这一版给使用者的变化

旧的 `agents/*.json` 不改也能接着跑。没写 `confirm` 时，写入默认在卡片上点按钮。飞书应用还没开通卡片回调时，自动退回 6 位确认码，和以前一样要发起人本人在原会话里回复。确认不能关掉。白名单不变。

- Windows 一键安装向导。入口是 `scripts/setup.ps1`，提问在 `setup_wizard.py`。Mac 和 Linux 在仓库根目录跑 `python setup_wizard.py` 也是同一套问题。一次只问一个问题，并说明为什么、去飞书开放平台的哪一页、直接回车用什么。App Secret 只从标准输入交给飞书命令行，不写进配置、草稿和屏幕。说明见 [docs/一键安装.md](一键安装.md)。已经装好的人不用再跑向导。
- 写入确认默认改成飞书交互卡片。卡片用大白话写要做什么、改哪里、影响谁，带「确认」和「取消」。只有发起人本人点了才算。服务端核对点击人、会话、动作有没有被改过，以及有没有过期（默认 10 分钟，可改）。一次有效，点过就不能再点。点完卡片变成已确认或已取消，再回报结果。
- 可以配置 `confirm.mode`：`button`（默认，点按钮）、`code`（仍用确认码）、`button_or_code`（两种都行）、`button_and_code`（两样都要）。批量写入、改功能开关、改确认方式、重启可以用 `confirm.high_risk_mode` 单独指定。确认码位数和两段有效期也可以改。主人在私聊里回复「确认方式」就能换，不能写成关掉。说明见 [docs/确认方式.md](确认方式.md)。
- 应用没开通卡片回调，或卡片发不出去时，自动退回确认码。`--doctor` 给出警告和开通步骤，不因此失败。

## 怎么单独打开

不写 `confirm` 就是点按钮。想继续只用确认码，在自己的 json 里写 `"confirm": {"mode": "code"}`。不要把密钥写进文件。

安装向导和确认方式互不牵连。不跑向导时，已有配置保持原样。向导也不会把确认关掉。

改完 JSON 后重启进程。在私聊里改确认方式，确认之后会自己备份并重启。

## 飞书权限

要用卡片上的按钮，到飞书开放平台，进入这个企业自建应用：

1. 打开「事件与回调」。
2. 选择「使用长连接接收事件」。不需要公网地址，也不用另开一台服务器。
3. 添加事件 `card.action.trigger`。
4. 发布版本。

没做这几步时，机器人自动改用确认码。安装向导不新加必须开通的权限。

## 还没做

企业微信不能拿来收消息。本说明不代替打 tag。标签 `v0.8.0` 由发布时另打。

## English

`VERSION` is `0.8.0`, released on 2026-10-20. This text is the release note for the upcoming tag `v0.8.0`. The program does not read this file. Old config files do not need new keys. The install wizard asks one question at a time and does not write secrets into files. Write confirmation defaults to a card button. `confirm.mode` may be `button`, `code`, `button_or_code`, or `button_and_code`. Confirmation cannot be turned off. Without a long-connection subscription to `card.action.trigger`, writes fall back to the confirmation code and `--doctor` prints a warning with the enable steps. Upgrade and rollback steps are in `docs/升级到0.8.0.md`. This note does not create the tag. Do not put secrets or real Feishu ids in the release notes.
