# 内存渠道，只给测试。不联网，不写飞书，也不读密钥。
import threading

from channels.base import Cap, Channel, Identity

# 比飞书少了文档、日历和表格，用来测核心会不会在缺能力时停手。
MEMORY_CAPS = frozenset({
    Cap.RECEIVE, Cap.SEND_TEXT, Cap.EDIT_TEXT, Cap.CARDS, Cap.CARD_UPDATE,
    Cap.REACTIONS, Cap.RECALL, Cap.HISTORY, Cap.ATTACHMENTS, Cap.MEMBERS,
    Cap.CHAT_LIST, Cap.IDENTITY, Cap.THREADS,
})


def _flag(argv, name):
    if name not in argv:
        return ""
    i = list(argv).index(name)
    return argv[i + 1] if i + 1 < len(argv) else ""


class MemoryChannel(Channel):
    name = "memory"

    def __init__(self, cfg=None, caps=None):
        super().__init__(cfg)
        self._caps = frozenset(caps) if caps is not None else set(MEMORY_CAPS)
        self.outbox = []          # 发出的文字、卡片、表情
        self.history = {}         # chat_id -> [消息]
        self.messages = {}        # message_id -> 消息
        self.members = {}         # chat_id -> [open_id]
        self.chats = []
        self._lock = threading.Lock()

    def capabilities(self):
        return self._caps

    def identity(self):
        bot = self.cfg.get("bot_open_id")
        if not bot:
            bot = "memory-bot"
        return Identity(bot, self.cfg.get("bot_names") or ["内存助手"], self.cfg.get("profile") or "")

    def push(self, ev):
        """测试把一条已经归一化的消息放进来。核心仍用 message_id / chat_id / sender_id。"""
        if not isinstance(ev, dict):
            return
        mid = str(ev.get("message_id") or ev.get("id") or "")
        cid = str(ev.get("chat_id") or "")
        with self._lock:
            if mid:
                self.messages[mid] = ev
            if cid:
                self.history.setdefault(cid, []).append(ev)

    def send_text(self, mid, text, key):
        with self._lock:
            self.outbox.append({"op": "send_text", "mid": mid, "text": text, "key": key})
        return True

    def reply_message(self, mid, msg_type, payload, idempotency_key, reply_in_thread):
        with self._lock:
            self.outbox.append({"op": "reply", "mid": mid, "msg_type": msg_type, "payload": payload,
                                "key": idempotency_key, "thread": bool(reply_in_thread)})
        return {"ok": True, "data": {"message_id": "mem_status_" + str(mid)}}, ""

    def edit_text(self, mid, text):
        with self._lock:
            self.outbox.append({"op": "edit_text", "mid": mid, "text": text})
        return {"ok": True, "data": {"message_id": mid}}, ""

    def patch_card(self, mid, card):
        with self._lock:
            self.outbox.append({"op": "patch_card", "mid": mid, "card": card})
        return {"ok": True, "data": {"message_id": mid}}, ""

    def delete_message(self, mid):
        with self._lock:
            self.outbox.append({"op": "delete", "mid": mid})
        return {"ok": True, "data": {}}, ""

    def add_reaction(self, mid, emoji):
        with self._lock:
            self.outbox.append({"op": "react", "mid": mid, "emoji": emoji})
        return {"ok": True, "data": {"reaction_id": "mem_react_" + str(mid)}}, ""

    def remove_reaction(self, mid, reaction_id):
        with self._lock:
            self.outbox.append({"op": "unreact", "mid": mid, "reaction_id": reaction_id})
        return {"ok": True, "data": {}}, ""

    def list_chats(self):
        return {"ok": True, "data": {"items": list(self.chats)}}, ""

    def list_members(self, chat_id):
        ids = self.members.get(chat_id)
        if ids is None:
            return None, "not found"
        users = [{"member_id": i} for i in ids]
        # 和飞书成员接口认的形状一样：完整名单才有 user_total，且和人数相等。
        return {"ok": True, "data": {"users": users, "user_total": len(users)}}, ""

    def transport_read(self, tool, argv, call):
        """历史和附件从内存里拿。别的读法仍交给原来的调用，方便测试确认它没被偷偷换掉。"""
        if tool == "history":
            cid = _flag(argv, "--chat-id")
            return {"ok": True, "data": {"messages": list(self.history.get(cid) or [])}}
        if tool in ("message_attachment", "attachment"):
            mid = _flag(argv, "--message-ids")
            msg = self.messages.get(mid)
            if not msg:
                return {"ok": False, "error": {"message": "not found", "code": 3380002}}
            return {"ok": True, "data": {"messages": [msg]}}
        return call()
