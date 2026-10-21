# 参与贡献

感谢你愿意改这个模板。行为和安全边界以 [docs/安全红线.md](docs/安全红线.md) 为准，不要为了方便把确认码、白名单或脱敏绕过去。讨论方式见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。第一次跑起来看 [docs/快速开始.md](docs/快速开始.md)。

## 环境

- Python 3.12
- 离线测试不需要安装第三方包，也不需要飞书账号或网络
- 真的连上飞书时才需要 Windows、Node.js、`@larksuite/cli` 和你自己的飞书应用

## 测试

在仓库根目录运行：

```text
python tests/run_tests.py
```

提交之前请让它们全部通过。通过时最后一行会打印条数，不要在文档里再写死一个会过时的数字。测试用假的 lark-cli 和假的模型进程，不会访问飞书。

改了启动脚本、配置键或工具名时，把对应的检查补进 `tests/run_tests.py`。

回答质量回归是另一套，不替代上面的单元测试。假模型这一档在 CI 里跑，不访问飞书：

```text
python evals/run.py --config agents/example.json --provider fake --tag ci --baseline evals/baseline/ci.json --out evals/out
```

加用例和升级前怎么跑真模型，见 [docs/回归测试.md](docs/回归测试.md)。不要把 `evals/out/` 或真实聊天写进仓库。

另外运行：

```text
python checks/lint.py
```

它只确认 Python 文件是 UTF-8 并且能编译，不重排代码。

## 代码风格

沿用所在文件已经在用的写法：4 个空格缩进，短语句可以写在同一行。注释说明为什么这样，而不是把代码再翻译一遍。不要为了风格把整个文件重新排版。用户能看到的说明用完整的句子。

GitHub Actions 会在 Windows 和 Linux 上跑上面的 lint 和测试。另有一个 `windows-latest` 作业用 Windows PowerShell 5.1（`powershell.exe`，不是 `pwsh`）解析 `scripts/*.ps1`，干跑其中不改系统的函数，并检查含中文的脚本带 UTF-8 BOM。启动、停止和注册计划任务的脚本只解析，不在 CI 里执行。同一工作流里还会跑 gitleaks，以及 `python checks/history_scan.py`。这个扫描只报告打码后的种类，不改写 Git 历史。工作流不访问飞书，也不读取密钥。

含中文的 `.ps1` 请保存为 UTF-8 带 BOM。不要把真实的群号、用户编号、密钥、客户名或 `C:\Users\某人` 这种个人路径写进提交。

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

Run `python checks/lint.py`, `python checks/history_scan.py`, and `python tests/run_tests.py`. Keep every check passing. The history scan only prints redacted categories and does not rewrite git history. The test count is printed at the end; do not hard-code it in docs. Match the compact 4-space style of the file you edit, and do not reformat unrelated lines. Do not commit secrets, filled-in `agents/*.json`, `redact.txt`, `memory/`, `logs/`, or real knowledge-base files. Contributions are licensed under Apache-2.0.
