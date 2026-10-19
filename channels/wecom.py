# 企业微信适配器骨架。实验，默认不加载，也不连接。
# 设计见 docs/渠道-企业微信.md。这里不实现收发，避免半成品被当成已经能上线。
from channels.base import Cap, Channel, ChannelError, Identity

# 智能机器人长连接实际有的能力。没有群历史、群成员、云文档、日历、表格、表情和撤回。
# 卡片和流式刷新官方有，但骨架还没发，所以不放进集合，核心会退回文字回复。
WECOM_CAPS = frozenset({
    Cap.RECEIVE, Cap.SEND_TEXT, Cap.IDENTITY,
})

_SECRET_KEYS = ("secret", "token", "encoding_aes_key", "encoding_aeskey", "webhook", "webhook_url")


class WeComChannel(Channel):
    """只做事件形状转换和能力声明。ready() 为假，主程序不会去连 wss。"""

    name = "wecom"
    experimental = True

    def __init__(self, cfg=None):
        super().__init__(cfg)
        block = cfg.get("wecom") if isinstance(cfg, dict) else None
        block = block if isinstance(block, dict) else {}
        for key in _SECRET_KEYS:
            if block.get(key) or (isinstance(cfg, dict) and key == "webhook" and cfg.get(key)):
                raise ChannelError("企业微信的密钥和 webhook 不能写进配置，只写环境变量的名字")
        if isinstance(cfg, dict) and (cfg.get("webhook") or cfg.get("webhook_url")):
            raise ChannelError("不要把消息推送的 webhook 写进配置")
        self.bot_id = str(block.get("bot_id") or (cfg or {}).get("bot_open_id") or "")
        self.secret_env = str(block.get("secret_env") or "")

    def capabilities(self):
        return WECOM_CAPS

    def ready(self):
        return False

    def blocked_reason(self):
        return "实验骨架，尚未连接企业微信，也不会主动发消息"

    def identity(self):
        return Identity(self.bot_id or "wecom-bot", self.cfg.get("bot_names") or [], "")

    def classify(self, obj):
        """长连接的 aibot_msg_callback，或回调明文 body。事件和欢迎语不当成提问。"""
        body = _body_of(obj)
        ev = to_inbound(body, self.bot_id)
        if not ev:
            return "drop", None
        return "message", ev

    def send_text(self, mid, text, key):
        raise ChannelError(self.blocked_reason())

    def consume_argv(self, event_key):
        raise ChannelError(self.blocked_reason())


def _body_of(obj):
    if not isinstance(obj, dict):
        return None
    if obj.get("cmd") in ("aibot_msg_callback", "aibot_event_callback"):
        body = obj.get("body")
        return body if isinstance(body, dict) else None
    if obj.get("msgtype") or obj.get("msgid"):
        return obj
    return None


def to_inbound(body, bot_id=""):
    """收成核心已经在用的事件字段。不保留 secret，也不把加密材料带进提示词。"""
    if not isinstance(body, dict):
        return None
    if body.get("msgtype") == "event" or body.get("event"):
        return None
    msgtype = str(body.get("msgtype") or "text")
    text = ""
    if msgtype == "text":
        block = body.get("text") if isinstance(body.get("text"), dict) else {}
        text = str(block.get("content") or "")
    elif msgtype == "mixed":
        text = _mixed_text(body.get("mixed"))
    else:
        # 图片、文件、语音、视频官方只在单聊回调。骨架只留一句，不下载。
        text = "（企业微信发来一条%s，骨架还不能读取附件）" % msgtype
    sender = body.get("from") if isinstance(body.get("from"), dict) else {}
    userid = str(sender.get("userid") or "?")
    chattype = str(body.get("chattype") or "single")
    group = chattype == "group"
    chat_id = str(body.get("chatid") or "")
    if not group:
        chat_id = chat_id or ("p2p:" + userid)
    mentions = [{"id": bot_id, "name": ""}] if group and bot_id else []
    mid = str(body.get("msgid") or "")
    if not mid:
        return None
    return {
        "message_id": mid,
        "chat_id": chat_id,
        "chat_type": "group" if group else "p2p",
        "sender_id": userid,
        "sender_type": "user",
        "message_type": "text" if msgtype in ("text", "mixed") else msgtype,
        "content": text,
        "mentions": mentions,
        "root_id": "",
        "thread_id": "",
        "parent_id": "",
        "reply_to": "",
    }


def _mixed_text(mixed):
    items = mixed.get("msg_item") if isinstance(mixed, dict) else None
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("msgtype") == "text" and isinstance(item.get("text"), dict):
            parts.append(str(item["text"].get("content") or ""))
    return "\n".join(p for p in parts if p)
