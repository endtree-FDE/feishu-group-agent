# 按配置选出模型实现。没写 model.provider / model_provider 时就是 cursor，和旧配置一样。
# 环境变量 AGENTBOT_FAKE_AGENT 仍优先（离线测试），不看配置里写的是哪一家。
import os

from providers.base import PartialText
from providers.presets import PRESETS, provider_token, public_name, uses_openai_compat


def create(cfg, log, kb):
    if os.environ.get("AGENTBOT_FAKE_AGENT"):
        from providers.fake import FakeProvider
        p = FakeProvider(cfg, log)
    else:
        name = provider_token(cfg)
        if name in ("", "cursor"):
            from providers.cursor import CursorProvider
            p = CursorProvider(cfg, log, kb)
        elif name == "anthropic":
            from providers.anthropic import AnthropicProvider
            p = AnthropicProvider(cfg, log)
            p.name = public_name(cfg)
        elif uses_openai_compat(name):
            from providers.openai_compat import OpenAICompatProvider
            p = OpenAICompatProvider(cfg, log)
            label = public_name(cfg)
            if name in PRESETS or label != "openai_compat":
                p.name = label
        elif name == "fake":
            from providers.fake import FakeProvider
            p = FakeProvider(cfg, log)
        else:
            from providers.fake import UnknownProvider
            p = UnknownProvider(public_name(cfg), log)
    log.info("model provider %s", p.name)
    return p
