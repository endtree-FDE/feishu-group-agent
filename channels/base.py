# 渠道适配器。核心只认识这里的能力和归一化事件，不认识某一家的命令行。
# 没写 channel 时由调用方选飞书。缺能力时核心降级，不假装做成了。
import os

class Cap:
    RECEIVE = "receive"
    SEND_TEXT = "send_text"
    EDIT_TEXT = "edit_text"
    CARDS = "cards"
    CARD_UPDATE = "card_update"  # 进度卡片的原地更新，或企业微信的流式刷新
    REACTIONS = "reactions"
    RECALL = "recall"
    HISTORY = "history"
    ATTACHMENTS = "attachments"
    MEMBERS = "members"
    CHAT_LIST = "chat_list"
    IDENTITY = "identity"
    DOCS = "docs"
    CALENDAR = "calendar"
    BITABLE = "bitable"
    THREADS = "threads"


# 飞书（lark-cli）现在这几项都有。新渠道缺哪项就不要放进自己的集合。
FEISHU_CAPS = frozenset({
    Cap.RECEIVE, Cap.SEND_TEXT, Cap.EDIT_TEXT, Cap.CARDS, Cap.CARD_UPDATE,
    Cap.REACTIONS, Cap.RECALL, Cap.HISTORY, Cap.ATTACHMENTS, Cap.MEMBERS,
    Cap.CHAT_LIST, Cap.IDENTITY, Cap.DOCS, Cap.CALENDAR, Cap.BITABLE, Cap.THREADS,
})

# 模型工具名 → 需要的渠道能力。知识库和技能不在这张表里，任何渠道都能用。
TOOL_CAPS = {
    "history": Cap.HISTORY,
    "message_attachment": Cap.ATTACHMENTS,
    "docs_search": Cap.DOCS,
    "docs_read": Cap.DOCS,
    "docs_create": Cap.DOCS,
    "docs_append": Cap.DOCS,
    "calendar_read": Cap.CALENDAR,
    "calendar_create": Cap.CALENDAR,
    "calendar_create_batch": Cap.CALENDAR,
    "calendar_update": Cap.CALENDAR,
    "calendar_delete": Cap.CALENDAR,
    "base_read": Cap.BITABLE,
    "base_create": Cap.BITABLE,
}

_UNAVAILABLE = {
    Cap.RECEIVE: "这个渠道还收不了消息",
    Cap.SEND_TEXT: "这个渠道发不了文字",
    Cap.EDIT_TEXT: "这个渠道改不了已经发出的文字",
    Cap.CARDS: "这个渠道发不了卡片",
    Cap.CARD_UPDATE: "这个渠道更新不了卡片",
    Cap.REACTIONS: "这个渠道没有表情回应",
    Cap.RECALL: "这个渠道撤回不了消息",
    Cap.HISTORY: "这个渠道读不了群历史",
    Cap.ATTACHMENTS: "这个渠道读不了消息里的附件",
    Cap.MEMBERS: "这个渠道查不了群成员",
    Cap.CHAT_LIST: "这个渠道列不出所在的群",
    Cap.IDENTITY: "这个渠道没有机器人身份",
    Cap.DOCS: "这个渠道没有云文档",
    Cap.CALENDAR: "这个渠道没有日历",
    Cap.BITABLE: "这个渠道没有多维表格",
    Cap.THREADS: "这个渠道没有话题",
}


class ChannelError(Exception):
    def __init__(self, user_msg, detail=""):
        super().__init__(user_msg)
        self.user_msg = user_msg
        self.detail = detail


class Identity:
    """机器人自己是谁。确认码和白名单仍用发送人编号，不看这个对象。"""

    def __init__(self, bot_id, names=(), profile=""):
        self.bot_id = bot_id
        self.names = tuple(names or ())
        self.profile = profile or ""


def channel_name(cfg):
    """不写、写空，都是飞书。旧配置因此不用改。"""
    if not isinstance(cfg, dict):
        return "feishu"
    raw = cfg.get("channel")
    if raw is None:
        return "feishu"
    text = str(raw).strip().lower()
    return text or "feishu"


def experimental_enabled(cfg):
    """实验渠道默认关。环境变量只给本机测试，不写进配置。"""
    if os.environ.get("AGENTBOT_EXPERIMENTAL_CHANNELS") == "1":
        return True
    block = cfg.get("channels") if isinstance(cfg, dict) else None
    return isinstance(block, dict) and bool(block.get("experimental"))


def unavailable(cap):
    return _UNAVAILABLE.get(cap, "这个渠道没有这项能力")


def status_modes(channel, want_card):
    """进度想用卡片时，渠道没有卡片就退回文字。飞书有卡片，结果和以前一样。"""
    if want_card and channel is not None and channel.supports(Cap.CARDS):
        return ["card", "text"]
    return ["text"]


def can_update(channel, mode):
    """文字进度要能改已发消息；卡片进度要能原地更新。飞书两项都有。"""
    if channel is None:
        return True
    if mode == "card":
        return channel.supports(Cap.CARD_UPDATE)
    return channel.supports(Cap.EDIT_TEXT)


class Channel:
    name = "base"
    experimental = False

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def capabilities(self):
        return frozenset()

    def supports(self, cap):
        return cap in self.capabilities()

    def unavailable(self, cap):
        return unavailable(cap)

    def identity(self):
        raise ChannelError(unavailable(Cap.IDENTITY))

    def classify(self, obj):
        """返回 (kind, payload)。kind 是 recall / member / message / drop。"""
        if isinstance(obj, dict) and (obj.get("message_id") or obj.get("id")):
            return "message", obj
        return "drop", None

    def ready(self):
        """正式收消息之前。飞书为真。实验骨架为假，这样默认不会去连。"""
        return True

    def blocked_reason(self):
        return ""

    def transport_read(self, tool, argv, call):
        """读历史和附件的传输。默认仍走原来的调用。飞书不要在这里改参数。"""
        return call()

    def list_chats(self):
        raise ChannelError(unavailable(Cap.CHAT_LIST))

    def list_members(self, chat_id):
        raise ChannelError(unavailable(Cap.MEMBERS))

    def consume_argv(self, event_key):
        raise ChannelError(unavailable(Cap.RECEIVE))

    def send_text(self, mid, text, key):
        raise ChannelError(unavailable(Cap.SEND_TEXT))

    def push_text(self, kind, target, text, key):
        """主动发给一个人或一个群，不是回复某一条消息。返回 ok / fail / unknown。
        默认失败。飞书在自己的适配器里实现，不让核心直接拼命令。"""
        return "fail"

    def reply_message(self, mid, msg_type, payload, idempotency_key, reply_in_thread):
        raise ChannelError(unavailable(Cap.SEND_TEXT))

    def edit_text(self, mid, text):
        raise ChannelError(unavailable(Cap.EDIT_TEXT))

    def patch_card(self, mid, card):
        raise ChannelError(unavailable(Cap.CARD_UPDATE))

    def delete_message(self, mid):
        raise ChannelError(unavailable(Cap.RECALL))

    def add_reaction(self, mid, emoji):
        raise ChannelError(unavailable(Cap.REACTIONS))

    def remove_reaction(self, mid, reaction_id):
        raise ChannelError(unavailable(Cap.REACTIONS))
