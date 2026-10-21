# 内存渠道，只给测试。不联网，不写飞书，也不读密钥。
# 回归测试可以设 AGENTBOT_MEMORY_FIXTURE / AGENTBOT_MEMORY_TRACE。
# 这两个变量都不写进配置。没设置时，行为和以前一样：空信箱、默认能力，不落盘。
import json, os, shutil, threading

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


_CAP_BY_NAME = {
    "receive": Cap.RECEIVE, "send_text": Cap.SEND_TEXT, "edit_text": Cap.EDIT_TEXT,
    "cards": Cap.CARDS, "card_update": Cap.CARD_UPDATE, "reactions": Cap.REACTIONS,
    "recall": Cap.RECALL, "history": Cap.HISTORY, "attachments": Cap.ATTACHMENTS,
    "members": Cap.MEMBERS, "chat_list": Cap.CHAT_LIST, "identity": Cap.IDENTITY,
    "docs": Cap.DOCS, "calendar": Cap.CALENDAR, "bitable": Cap.BITABLE, "threads": Cap.THREADS,
}


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
        self.files = {}           # message_id -> [{name, path}]，只在夹具里用
        self._lock = threading.Lock()
        self._load_fixture()

    def capabilities(self):
        return self._caps

    def identity(self):
        bot = self.cfg.get("bot_open_id")
        if not bot:
            bot = "memory-bot"
        return Identity(bot, self.cfg.get("bot_names") or ["内存助手"], self.cfg.get("profile") or "")

    def classify(self, obj):
        """和飞书一样：包在 event 里的消息也能交给核心。没有 message_id 的仍然丢掉。"""
        if not isinstance(obj, dict):
            return "drop", None
        if obj.get("message_id") or obj.get("id"):
            return "message", obj
        inner = obj.get("event")
        if isinstance(inner, dict) and (inner.get("message_id") or inner.get("id")):
            return "message", inner
        return "drop", None

    def _load_fixture(self):
        """只在回归测试设置了环境变量时读夹具。生产配置不会走到这里。"""
        path = os.environ.get("AGENTBOT_MEMORY_FIXTURE")
        if not path:
            return
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        extra = []
        for name in data.get("caps") or []:
            cap = _CAP_BY_NAME.get(str(name))
            if cap:
                extra.append(cap)
        if extra:
            self._caps = set(self._caps)
            self._caps.update(extra)
        members = data.get("members") if isinstance(data.get("members"), dict) else {}
        for cid, ids in members.items():
            if isinstance(ids, list):
                self.members[str(cid)] = [str(x) for x in ids]
        for msg in data.get("messages") or []:
            if isinstance(msg, dict):
                self.push(msg)
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        for mid, specs in files.items():
            if isinstance(specs, list):
                self.files[str(mid)] = [s for s in specs if isinstance(s, dict)]

    def _trace(self, rec):
        path = os.environ.get("AGENTBOT_MEMORY_TRACE")
        if not path:
            return
        try:
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def stage_download(self, tool, argv, cwd):
        """附件下载目录由核心建好后调用。没夹具时什么都不做，也不碰 lark-cli。"""
        if tool not in ("attachment", "message_attachment") or not cwd:
            return
        mid = _flag(argv, "--message-ids")
        specs = self.files.get(mid) or []
        if not specs:
            return
        folder = os.path.join(cwd, "lark-im-resources", "x")
        os.makedirs(folder, exist_ok=True)
        for spec in specs:
            name = os.path.basename(str(spec.get("name") or "file"))
            if not name or name in (".", ".."):
                continue
            dest = os.path.join(folder, name)
            src = spec.get("path")
            if src and os.path.isfile(src):
                shutil.copy(src, dest)
                continue
            if "text" in spec:
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(str(spec.get("text") or ""))

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
        rec = {"op": "send_text", "mid": mid, "text": text, "key": key}
        with self._lock:
            self.outbox.append(rec)
        self._trace(rec)
        return True

    def push_text(self, kind, target, text, key):
        rec = {"op": "push_text", "kind": kind, "target": target, "text": text, "key": key}
        with self._lock:
            self.outbox.append(rec)
        self._trace(rec)
        return "ok"

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
