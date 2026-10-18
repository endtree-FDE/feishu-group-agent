#!/usr/bin/env python3
# 预设、配置校验和安装向导的离线检查。不连模型，不写仓库里的 agents/。
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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

if failures:
    print("%d failed" % len(failures))
    sys.exit(1)
print("batch ok")
