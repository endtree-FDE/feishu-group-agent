#!/usr/bin/env python3
# 预设、配置校验和安装向导的离线检查。不连模型，不写仓库里的 agents/。
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import utf8io  # noqa: F401
os.chdir(ROOT)

failures = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)

from providers.presets import resolve_agent, preset_id

cursor = {"model_provider": "cursor", "agent": {"model": "grok-4.7-high-fast"}}
got = resolve_agent(cursor)
check("cursor 不套预设", preset_id(cursor) == "" and "base_url" not in got and got["model"] == "grok-4.7-high-fast")

bare = {"model_provider": "deepseek", "agent": {}}
filled = resolve_agent(bare)
check("deepseek 补地址和变量名", filled["base_url"] == "https://api.deepseek.com" and filled["api_key_env"] == "DEEPSEEK_API_KEY" and filled["model"] == "deepseek-flash")
check("补全不把密钥写进结果", "sk-" not in str(filled))

explicit = {"model_provider": "deepseek", "agent": {"model": "my-model", "base_url": "https://example.com/v1"}}
over = resolve_agent(explicit)
check("已经写了的模型名和地址优先", over["model"] == "my-model" and over["base_url"] == "https://example.com/v1" and over["api_key_env"] == "DEEPSEEK_API_KEY")

compat = {"model_provider": "openai_compat", "agent": {"preset": "zhipu"}}
z = resolve_agent(compat)
check("openai_compat 加 preset 才补智谱", z["base_url"].startswith("https://open.bigmodel.cn/") and z["api_key_env"] == "ZAI_API_KEY")

plain = {"model_provider": "openai_compat", "agent": {"model": "m", "base_url": "https://example.com", "api_key_env": "UNIT_OPENAI_KEY"}}
check("没写 preset 的 openai_compat 不改原值", resolve_agent(plain) == plain["agent"])

pangu = resolve_agent({"model_provider": "pangu", "agent": {}})
check("盘古用 MaaS 的 OpenAI 兼容地址", pangu["base_url"] == "https://api.modelarts-maas.com/openai/v1" and pangu["api_key_env"] == "MAAS_API_KEY")

missing = {}
check("没写 model_provider 时当 cursor", preset_id(missing) == "" and resolve_agent(missing) == {})

import json
from checks.check_config import check_data, check_file

ok_errors, ok_hints = check_file(os.path.join(ROOT, "agents", "example.json"))
check("example.json 通过校验", ok_errors == [] and ok_hints == [])

bare_cfg = {"future_flag": {"any": 1}, "model_provider": "cursor"}
check("不认识的键不失败", check_data(bare_cfg, "bare.json")[0] == [])

bad_type = {"memory": {"enabled": "yes"}}
type_errors, _ = check_data(bad_type, "bad.json")
check("布尔写成文字会报错", any(p == "memory.enabled" for p, _ in type_errors))

planted = "sk-" + "planted"
secret_cfg = {"agent": {"api_key_env": planted, "base_url": "https://example.com/v1?api_key=abc"}}
sec_errors, _ = check_data(secret_cfg, "sec.json")
sec_text = " ".join(m for _, m in sec_errors)
check("密钥样式的变量名和地址被拒绝", any(p == "agent.api_key_env" for p, _ in sec_errors) and any(p == "agent.base_url" for p, _ in sec_errors))
check("报错不回显密钥", planted not in sec_text and "api_key=abc" not in sec_text)

space_env = {"agent": {"api_key_env": "NOT A NAME"}}
check("变量名里有空格会被拒绝", any(p == "agent.api_key_env" for p, _ in check_data(space_env, "sp.json")[0]))

hint_cfg = {"bot_open_id": "REPLACE_BOT_OPEN_ID"}
hint_errors, hint_lines = check_data(hint_cfg, "mine.json")
check("非示例里的 REPLACE_ 只是提示", hint_errors == [] and hint_lines and "REPLACE_" in hint_lines[0])

import subprocess
WIZ = os.path.join(ROOT, "setup_agent.py")

def wizard(args, outdir=None):
    cmd = [sys.executable, WIZ] + args
    if outdir:
        cmd += ["--outdir", outdir]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr

code, out = wizard(["--name", "drydeepseek", "--provider", "deepseek", "--dry-run"])
check("向导干跑不写文件", code == 0 and not os.path.exists(os.path.join(ROOT, "agents", "drydeepseek.json")))
check("向导干跑写出预设模型名和计划任务", "deepseek-flash" in out and "grok-4.7-high-fast" not in out and "register-tasks.ps1" in out and "start.ps1" in out and "没有写入" in out)

code, out = wizard(["--name", "drycursor"])
check("不加 --write 也是干跑", code == 0 and "grok-4.7-high-fast" in out and "没有写入" in out and not os.path.exists(os.path.join(ROOT, "agents", "drycursor.json")))

with tempfile.TemporaryDirectory() as td:
    code, out = wizard(["--name", "box", "--provider", "deepseek", "--write"], td)
    dest = os.path.join(td, "box.json")
    body = open(dest, encoding="utf-8").read() if code == 0 else ""
    check("向导 --write 只写新文件", code == 0 and os.path.isfile(dest) and '"model_provider": "deepseek"' in body and "deepseek-flash" in body and "sk-" not in body)
    first = body
    code2, out2 = wizard(["--name", "box", "--provider", "cursor", "--write"], td)
    check("已有文件不覆盖", code2 == 1 and open(dest, encoding="utf-8").read() == first and "没有覆盖" in out2)
    code3, out3 = wizard(["--name", "../evil", "--provider", "cursor", "--write"], td)
    check("名字不能跳出目录", code3 == 2 and not os.path.exists(os.path.join(td, "..", "evil.json")))
    code4, _ = wizard(["--api-key", "nope", "--name", "box2", "--write"], td)
    check("拒绝 --api-key", code4 == 2 and not os.path.exists(os.path.join(td, "box2.json")))
    code5, out5 = wizard(["--name", "custom", "--provider", "openai_compat", "--base-url", "https://example.com/v1", "--model", "m", "--api-key-env", "UNIT_OPENAI_KEY", "--write"], td)
    custom = open(os.path.join(td, "custom.json"), encoding="utf-8").read()
    check("自填兼容接口写入变量名", code5 == 0 and "UNIT_OPENAI_KEY" in custom and "grok-4.7-high-fast" not in custom)
    planted = "sk-" + "planted"
    code6, out6 = wizard(["--name", "leak", "--provider", "openai_compat", "--base-url", "https://example.com/v1?api_key=abc", "--model", "m", "--api-key-env", planted, "--write"], td)
    check("向导拒绝夹带密钥且不回显", code6 == 2 and not os.path.exists(os.path.join(td, "leak.json")) and planted not in out6 and "api_key=abc" not in out6)

code, out = wizard(["--name", "both", "--dry-run", "--write"])
check("干跑和写入不能一起用", code == 2 and "不能一起用" in out and not os.path.exists(os.path.join(ROOT, "agents", "both.json")))

import checks.history_scan as _hs
_sha = _hs.git("rev-parse", "HEAD")[1].strip()
_lines = _hs.grep_commit(_sha, "ou_" + "placeholder")
check("历史扫描搜的是提交树，不是同名路径", bool(_lines) and any("placeholder" in x for x in _lines))
check("占位 open_id 不算泄漏", _hs.fixture("open_id", "ou_" + "placeholder", "tests/test_observe.py"))
check("当前历史没有需要复查的真编号", _hs.main() == 0)

if failures:
    print("%d failed" % len(failures))
    sys.exit(1)
print("batch ok")
