# 飞书渠道。传输仍是 lark-cli，参数和以前的调用相同。
# 长连接、成员、发消息、改卡片都从这里出去。确认码、脱敏、限流不在这里。
import hashlib, json, os, re, subprocess, time

import guard
from channels.base import FEISHU_CAPS, Channel, Identity

MEMBER_ADDED = "im.chat.member.bot.added_v1"
MEMBER_DELETED = "im.chat.member.bot.deleted_v1"
_RECALL_TYPES = {"im.message.recalled_v1", "im.message.message_recalled_v1"}


def event_type_of(obj):
    if not isinstance(obj, dict):
        return ""
    header = obj.get("header")
    if isinstance(header, dict) and header.get("event_type"):
        return header["event_type"]
    return obj.get("event_type") or ""


def event_id_of(obj):
    if not isinstance(obj, dict):
        return ""
    header = obj.get("header")
    if isinstance(header, dict) and header.get("event_id"):
        return str(header["event_id"])
    if obj.get("event_id"):
        return str(obj["event_id"])
    return ""


def member_body(obj):
    inner = obj.get("event") if isinstance(obj, dict) and isinstance(obj.get("event"), dict) else None
    if inner and (event_type_of(inner) in (MEMBER_ADDED, MEMBER_DELETED) or isinstance(inner.get("header"), dict)):
        return member_body(inner)
    if inner and inner.get("chat_id"):
        return inner
    return obj if isinstance(obj, dict) else {}


def is_recall(obj):
    if not isinstance(obj, dict):
        return False
    if event_type_of(obj) in _RECALL_TYPES:
        return True
    inner = obj.get("event")
    return isinstance(inner, dict) and event_type_of(inner) in _RECALL_TYPES


def classify_event(obj):
    """和原来 ingest 的分支一致：撤回、进群出群、包在 event 里的消息、普通消息。"""
    if not isinstance(obj, dict):
        return "drop", None
    if is_recall(obj):
        return "recall", None
    kind = event_type_of(obj)
    if kind not in (MEMBER_ADDED, MEMBER_DELETED):
        inner = obj.get("event") if isinstance(obj.get("event"), dict) else None
        if inner and not obj.get("message_id"):
            kind = event_type_of(inner)
            if kind in (MEMBER_ADDED, MEMBER_DELETED):
                return "member", {"event_type": kind, "body": member_body(inner),
                                  "event_id": event_id_of(obj) or event_id_of(inner)}
            if inner.get("message_id") or inner.get("id") or inner.get("chat_type") or inner.get("sender_id"):
                return "message", inner
    if kind in (MEMBER_ADDED, MEMBER_DELETED):
        return "member", {"event_type": kind, "body": member_body(obj), "event_id": event_id_of(obj)}
    return "message", obj


class FeishuChannel(Channel):
    name = "feishu"

    def __init__(self, cfg, exe, api, tmp, dry, log, run_capture, names=()):
        super().__init__(cfg)
        # exe / api / run_capture 都是调用时再查的函数，测试替换 agentbot 上的同名函数仍然有效。
        self.exe = exe
        self.api = api
        self.tmp = tmp
        self.dry = dry
        self.log = log
        self.run_capture = run_capture
        self.names = names or ()

    def capabilities(self):
        return FEISHU_CAPS

    def identity(self):
        # 缺 bot_open_id 时和以前一样抛 KeyError，不把空编号当成另一个机器人。
        return Identity(self.cfg["bot_open_id"], self.cfg.get("bot_names") or [], self.cfg.get("profile") or "")

    def classify(self, obj):
        return classify_event(obj)

    def list_chats(self):
        return self.api(["im", "+chat-list", "--page-all", "--page-size", "100", "--page-limit", "20", "--format", "json"],
                        None, 120)

    def list_members(self, chat_id):
        # im/v1/chats/{chat_id}/members ，member_id_type=open_id。--page-limit 0 表示翻完。
        argv = ["im", "+chat-members-list", "--chat-id", chat_id, "--member-id-type", "open_id",
                "--page-all", "--page-limit", "0", "--format", "json"]
        return self.api(argv, None, 120)

    def consume_argv(self, event_key):
        return self.exe() + ["event", "consume", event_key, "--profile", self.cfg["profile"], "--as", "bot"]

    def push_text(self, kind, target, text, key):
        """私聊主人或往群里发摘要。走 im +messages-send，不启动 event consume。"""
        import outbound
        exe = self.exe() if callable(self.exe) else self.exe
        folder = self.tmp or "."
        return outbound.send(exe, str(self.cfg.get("profile") or ""), kind, target, text, key,
                             self.names, folder, run=self.run_capture)

    def send_text(self, mid, text, key):
        """回复一条文字。命令和重试与原来的 _reply_one 相同。正文应已由核心脱敏。"""
        if self.dry:
            self.log.info("[dry-run] reply %s: %s", mid, (text or "")[:600])
            return True
        fn = os.path.join(self.tmp, "r_%s.json" % hashlib.md5(("%s:%s" % (mid, key)).encode()).hexdigest()[:12])
        with open(fn, "w", encoding="utf-8") as f:
            json.dump({"text": text}, f, ensure_ascii=False)
        cmd = self.exe() + ["im", "+messages-reply", "--profile", self.cfg["profile"], "--as", "bot", "--message-id", mid,
                            "--msg-type", "text", "--content", "@" + os.path.basename(fn), "--idempotency-key", key]
        if self.cfg.get("reply_in_thread", True):
            cmd.insert(cmd.index("--msg-type"), "--reply-in-thread")
        ok = False
        out = ""
        for i in range(4):
            try:
                r = self.run_capture(cmd, cwd=self.tmp, timeout=90)
            except subprocess.TimeoutExpired:
                r = subprocess.CompletedProcess(cmd, -1, "", "timeout")
            out = (r.stdout or "") + (r.stderr or "")
            try:
                ok = bool(json.loads((r.stdout or "{}")[(r.stdout or "{}").find("{"):]).get("ok"))
            except Exception:
                ok = '"ok": true' in out
            if ok or not re.search(r"unreachable|EOF|timeout|connection", out, re.I):
                break
            self.log.warning("reply retry %d: %s", i + 1, guard.redact(out[:200], self.names))
            time.sleep(5 * (i + 1))
        self.log.info("reply %s ok=%s %s", mid, ok, "" if ok else out[:300])
        try:
            os.remove(fn)
        except OSError:
            pass
        return ok

    def reply_message(self, mid, msg_type, payload, idempotency_key, reply_in_thread):
        cmd = ["im", "+messages-reply", "--message-id", mid, "--msg-type", msg_type,
               "--content", "@PAYLOAD", "--idempotency-key", idempotency_key]
        if reply_in_thread:
            cmd.insert(cmd.index("--msg-type"), "--reply-in-thread")
        return self.api(cmd, payload, 60)

    def edit_text(self, mid, text):
        return self.api(["im", "+messages-edit", "--message-id", mid, "--msg-type", "text", "--content", "@PAYLOAD"],
                        {"text": text}, 60)

    def patch_card(self, mid, card):
        return self.api(["im", "messages", "patch", "--message-id", mid, "--data", "@PAYLOAD"],
                        {"content": json.dumps(card, ensure_ascii=False)}, 60)

    def delete_message(self, mid):
        # DELETE /open-apis/im/v1/messages/:message_id 。high-risk-write，缺 --yes 时 lark-cli 退出码 10。
        return self.api(["im", "messages", "delete", "--message-id", mid, "--yes"], None, 60)

    def add_reaction(self, mid, emoji):
        return self.api(["im", "reactions", "create", "--message-id", mid, "--data", "@PAYLOAD"],
                        {"reaction_type": {"emoji_type": emoji}}, 30)

    def remove_reaction(self, mid, reaction_id):
        return self.api(["im", "reactions", "delete", "--message-id", mid, "--reaction-id", reaction_id], None, 30)

    def probe(self, argv, payload=None, timeout=30):
        """--check --probe 仍用原来的探针命令。api 会补上 --profile 和 --as bot。"""
        return self.api(argv, payload, timeout)
