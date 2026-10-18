# 变更记录

用户能看到的变化写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。版本号只以仓库根目录的 `VERSION` 为准。

程序不读取 `VERSION` 或本文件。删掉它们时，机器人的行为与现在相同。

当前 `VERSION` 是 `0.4.0-dev`。还没有对应的 GitHub Release。发布时请把 `VERSION` 改成不带 `-dev` 的版本号，打同名标签（例如 `0.4.0`），并让本文件里的版本节和标签一致。Release 说明不要写密钥、群号、open_id 或客户名。

`main` 上最近一次合并还没有版本标签。下面只记这条线相对于该快照新加的、用户能感知的部分。

## [Unreleased]

### 添加

- 模型预设 `deepseek`、`dashscope`、`zhipu`、`pangu`。只补空着的地址、模型名和变量名。密钥仍只读环境变量。没写 `model_provider` 时仍是 Cursor。
- `python checks/check_config.py` 按 JSON Schema 检查配置类型。密钥写进 `api_key_env` 或 `base_url` 会失败。缺了的键和不认识的键不失败。
- 知识库目录和 `kb_read`。旧配置没写时，空目录不改变提示词。
- 本机 `logs/journal.jsonl`，只记工具名、字数和耗时，不记问题原文。`observability.journal` 为 `false` 时不写。
- 长期记忆开着且档案被字数预算截断时，按本群问题召回命中的事实。`memory.recall` 为 `false` 时不召回。
- GitHub Actions：Windows 和 Linux 上跑测试与编译检查；Linux 上再跑 gitleaks 和 `checks/history_scan.py`。
- Issue 模板、Pull Request 模板、贡献说明里的代码风格。

### 安全

- 公开前扫描只报告打码结果，不改写 Git 历史。

## English

The version is the `VERSION` file (`0.4.0-dev`). There is no GitHub Release yet. When publishing, drop the `-dev` suffix, tag that version, and keep this file in sync. Do not put secrets or Feishu IDs in release notes. The program does not read this file.
