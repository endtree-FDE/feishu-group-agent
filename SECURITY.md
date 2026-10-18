# 安全策略

## 支持的版本

版本号写在仓库根目录的 `VERSION`。还没有 GitHub Release。安全修复以 `main` 分支为准。以后打了 Release，就以最新的 Release 和 `main` 为准。

## 报告漏洞

请不要在公开的 Issue、Pull Request 或群聊里贴漏洞细节，也不要贴 App Secret、访问令牌、填好的配置、群编号、用户编号或客户名单。

使用 GitHub 的私人安全通报：

[https://github.com/endtree-FDE/feishu-group-agent/security/advisories/new](https://github.com/endtree-FDE/feishu-group-agent/security/advisories/new)

我们会确认收到，并在修好之后再公开说明。若私人通报暂时不可用，请先只发问题的类型和影响范围，等维护者回复后再给复现步骤。

## 请不要提交或公开的内容

- 飞书 App Secret，以及任何模型 API 密钥、访问令牌
- 填好的 `agents/*.json`（`agents/example.json` 里的 `REPLACE_` 占位符可以提交）
- `bot_open_id`、`chat_ids`、用户 `open_id`、文档 / 文件夹 / 多维表格 / 日历编号
- `redact.txt` 里的客户名
- `memory/`、`logs/`、`audit/`、`state/`、`state.db`
- `kb/` 里的真实资料（只保留 `kb/请把资料放在这里.md`）
- 电脑用户目录、内网地址、手机号、邮箱

飞书 App Secret 只在你自己的电脑上输入：

```text
lark-cli config init --app-id 你的应用ID --app-secret-stdin --brand feishu
```

模型密钥只放进环境变量。配置里的 `api_key_env` 只写变量名。

## 这个程序的安全边界

机器人跑在操作者自己的电脑上，使用这台电脑上的 lark-cli 登录态和模型额度。仓库不代管这些凭据。

写飞书之前要同时通过三道锁，再加一次确认码：

1. 发起人在 `write.allowed_users` 里。
2. 目标在配置点名的文件夹、文档、日历或表里。
3. 动作只限于新建文档、在白名单文档末尾追加、建日程、加一条表格记录。

程序不删除消息、文档、日程或表格记录，不覆盖已有正文，不修改权限，不邀请他人，也不主动发消息。

回答、进度、记忆和知识库同步会打码客户名、手机号、证件号、邮箱、密钥样子的字符串和电脑路径。话题红线默认是空的，由部署者自己写。

常驻模式默认关闭。打开之后，改文件、删文件和执行命令的请求会被拒绝。上网只放行公网的搜索和网页读取，不放行本机和内网地址。

更细的规则见 [docs/安全红线.md](docs/安全红线.md)。

## English

The version is the `VERSION` file. There is no GitHub Release yet, so security fixes target `main`. After a release exists, they target the latest release and `main`. Report vulnerabilities through [GitHub private security advisories](https://github.com/endtree-FDE/feishu-group-agent/security/advisories/new). Do not include secrets, Feishu IDs, or customer names in public issues. The bot runs on the operator's machine; this repository must not contain App Secrets or filled-in configuration.
