# 参与贡献

感谢你愿意改这个模板。行为和安全边界以 [docs/安全红线.md](docs/安全红线.md) 为准，不要为了方便把确认码、白名单或脱敏绕过去。

## 环境

- Python 3.12
- 离线测试不需要安装第三方包，也不需要飞书账号或网络
- 真的连上飞书时才需要 Windows、Node.js、`@larksuite/cli` 和你自己的飞书应用

## 测试

在仓库根目录运行：

```text
python tests/run_tests.py
```

当前是 178 项。提交之前请让它们全部通过。测试用假的 lark-cli 和假的模型进程，不会访问飞书。

改了启动脚本、配置键或工具名时，把对应的检查补进 `tests/run_tests.py`。

## 不要提交的文件

`.gitignore` 已经排除这些路径。请确认 `git status` 里没有它们：

- `agents/*.json`，除了占位用的 `agents/example.json`
- `redact.txt`、`.env` 和任何密钥
- `memory/`、`logs/`、`audit/`、`state/`、`state.db`
- `kb/` 里除了 `kb/请把资料放在这里.md` 以外的资料

配置示例继续用 `REPLACE_` 占位符。文档里的人名、群名、客户名用「小助手」「小李」「某客户」这一类例子，不要写真实的。

## 改动方式

- 提交说明写清楚改了什么、为什么改。中文或英文都可以。
- 用户能看到的说明放在 `docs/` 或 `README.md`，用完整的句子。
- 安全相关的改动请在 Pull Request 里写明影响：谁能写、写到哪里、失败时用户看到什么。
- 不要把现有行为放宽成默认开启记忆、进度卡片或常驻模式。这些开关默认是关的。

## 许可证

本项目使用 Apache-2.0。提交贡献即表示你同意按该许可证授权你的改动。见 [LICENSE](LICENSE)。

## English

Run `python tests/run_tests.py` and keep all 178 checks passing. Do not commit secrets, filled-in `agents/*.json`, `redact.txt`, `memory/`, `logs/`, or real knowledge-base files. Contributions are licensed under Apache-2.0.
