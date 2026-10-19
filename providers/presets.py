# 国产模型和比赛常用的 OpenAI 兼容地址。只填空着的 base_url、model、api_key_env。
# 配置里已经写了的值优先。密钥不在这里，只记录环境变量的名字。
# 地址对照 2026-10-18 的公开文档。模型名以账号里能用的为准，写了 agent.model 就用写的。

PRESETS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "title": "DeepSeek",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "title": "通义千问",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-5.3",
        "api_key_env": "ZAI_API_KEY",
        "title": "智谱",
    },
    "pangu": {
        "base_url": "https://api.modelarts-maas.com/openai/v1",
        "model": "openpangu-2.0-flash",
        "api_key_env": "MAAS_API_KEY",
        "title": "华为盘古（MaaS OpenAI 兼容）",
    },
}

_BLANK_PROVIDER = {"", "cursor", "fake"}


def preset_id(cfg):
    """选出要套用的预设。cursor 和 fake 不套。没写、写错的 preset 当没有。"""
    if not isinstance(cfg, dict):
        return ""
    provider = str(cfg.get("model_provider") or "cursor").strip().lower()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    chosen = str(agent.get("preset") or "").strip().lower()
    if provider in _BLANK_PROVIDER:
        return ""
    if provider == "openai_compat":
        return chosen if chosen in PRESETS else ""
    if provider in PRESETS:
        return provider
    return ""


def uses_openai_compat(name):
    return name == "openai_compat" or name in PRESETS


def resolve_agent(cfg):
    """返回补全后的 agent 副本。不改调用方的配置，也不读取密钥。"""
    if not isinstance(cfg, dict):
        return {}
    agent = dict(cfg.get("agent") or {}) if isinstance(cfg.get("agent"), dict) else {}
    spec = PRESETS.get(preset_id(cfg))
    if not spec:
        return agent
    for key in ("base_url", "model", "api_key_env"):
        if not str(agent.get(key) or "").strip():
            agent[key] = spec[key]
    return agent
