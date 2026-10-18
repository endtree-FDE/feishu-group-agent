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

if failures:
    print("%d failed" % len(failures))
    sys.exit(1)
print("batch ok")
