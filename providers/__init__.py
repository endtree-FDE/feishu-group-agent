# 按配置选出模型实现。没写 model_provider 时就是 cursor，和旧配置一样。
# 环境变量 AGENTBOT_FAKE_AGENT 仍优先（离线测试），不看配置里写的是哪一家。
import os

from providers.base import PartialText
from providers.presets import PRESETS, uses_openai_compat


def create(cfg, log, kb):
    if os.environ.get("AGENTBOT_FAKE_AGENT"):
        from providers.fake import FakeProvider
        p = FakeProvider(cfg, log)
    else:
        name = str(cfg.get("model_provider") or "cursor").strip().lower()
        if name in ("", "cursor"):
            from providers.cursor import CursorProvider
            p = CursorProvider(cfg, log, kb)
        elif uses_openai_compat(name):
            from providers.openai_compat import OpenAICompatProvider
            p = OpenAICompatProvider(cfg, log)
            if name in PRESETS:
                p.name = name
        elif name == "fake":
            from providers.fake import FakeProvider
            p = FakeProvider(cfg, log)
        else:
            from providers.fake import UnknownProvider
            p = UnknownProvider(name, log)
    log.info("model provider %s", p.name)
    return p
