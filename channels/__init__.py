# 按配置选出渠道。没写 channel 时是飞书，和旧配置一样。
from channels.base import (
    Cap, ChannelError, FEISHU_CAPS, TOOL_CAPS, Identity,
    can_update, channel_name, experimental_enabled, status_modes, unavailable,
)
from channels.feishu import FeishuChannel
from channels.memory import MemoryChannel


def open_channel(cfg, exe=None, api=None, tmp="", dry=False, log=None, run_capture=None, names=()):
    name = channel_name(cfg)
    if name in ("feishu", "lark"):
        return FeishuChannel(cfg, exe, api, tmp, dry, log, run_capture, names)
    if name in ("fake", "memory"):
        return MemoryChannel(cfg)
    if name == "wecom":
        # 没打开实验开关时连类都不加载，避免以后骨架里的导入在飞书启动时生效。
        if not experimental_enabled(cfg):
            raise ChannelError("企业微信适配器仍是实验骨架，默认不启用。没写 channel 的配置继续走飞书。要在本机看骨架，把 channels.experimental 设为 true。那样也不会连接企业微信。")
        from channels.wecom import WeComChannel
        return WeComChannel(cfg)
    raise ChannelError("不认识的 channel。不写 channel 时使用飞书。")
