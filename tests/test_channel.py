# 渠道适配器。不连飞书，也不连企业微信。
import json, logging, os, re, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import channels
from channels.base import Cap, ChannelError, FEISHU_CAPS, can_update, channel_name, status_modes
from channels.feishu import FeishuChannel, classify_event
from channels.memory import MemoryChannel
from channels.wecom import WeComChannel, to_inbound
from tools import ToolError, Tools

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


def test_defaults():
    check("不写 channel 仍是飞书", channel_name({}) == "feishu" and channel_name({"channel": ""}) == "feishu" and channel_name({"channel": "Lark"}) == "lark")
    check("飞书能力含卡片更新、日历和群历史", Cap.CARD_UPDATE in FEISHU_CAPS and Cap.CALENDAR in FEISHU_CAPS and Cap.HISTORY in FEISHU_CAPS and Cap.MEMBERS in FEISHU_CAPS)
    bare = type("C", (), {"supports": lambda self, cap: False})()
    check("没有卡片时进度退回文字", status_modes(bare, True) == ["text"] and can_update(bare, "card") is False)
    full = type("C", (), {"supports": lambda self, cap: True})()
    check("飞书这种全能力渠道仍先试卡片", status_modes(full, True) == ["card", "text"] and can_update(full, "text") is True)


def test_feishu_argv():
    seen = []

    def api(argv, payload=None, timeout=30):
        seen.append((list(argv), payload, timeout))
        return {"ok": True, "data": {"users": [{"member_id": "ou_a"}], "user_total": 1}}, ""

    cfg = {"profile": "placeholder", "bot_open_id": "ou_bot_placeholder", "bot_names": ["小助手"], "reply_in_thread": True}
    ch = FeishuChannel(cfg, exe=lambda: ["lark-cli"], api=api, tmp="", dry=True, log=logging.getLogger("ch"), run_capture=None, names=())
    j, err = ch.list_members("oc_placeholder")
    check("成员查询仍是 chat-members-list，并且翻完",
          j and seen[-1][0][:2] == ["im", "+chat-members-list"] and "--page-limit" in seen[-1][0]
          and seen[-1][0][seen[-1][0].index("--page-limit") + 1] == "0"
          and seen[-1][0][seen[-1][0].index("--chat-id") + 1] == "oc_placeholder" and seen[-1][2] == 120, seen)
    seen.clear()
    ch.list_chats()
    check("群列表命令没有改", seen[-1][0][:2] == ["im", "+chat-list"] and "--page-all" in seen[-1][0], seen)
    cmd = ch.consume_argv("im.message.receive_v1")
    check("长连接命令仍带 profile 和 bot 身份",
          cmd[:4] == ["lark-cli", "event", "consume", "im.message.receive_v1"] and "--profile" in cmd and cmd[cmd.index("--as") + 1] == "bot", cmd)
    ident = ch.identity()
    check("飞书身份就是配置里的 open_id", ident.bot_id == "ou_bot_placeholder" and ident.names == ("小助手",) and ident.profile == "placeholder")
    recall = {"header": {"event_type": "im.message.recalled_v1", "event_id": "evt_recall_demo1"}, "event": {"message_id": "msg_recall_1"}}
    kind, payload = classify_event(recall)
    check("撤回事件归到 recall，不交给模型", kind == "recall" and payload is None)
    wrapped = {"event": {"message_id": "om_1", "chat_id": "oc_placeholder", "sender_id": "ou_a", "content": "你好"}}
    kind, payload = ch.classify(wrapped)
    check("包在 event 里的消息仍原样交给核心", kind == "message" and payload.get("message_id") == "om_1" and payload.get("content") == "你好")
    added = {"header": {"event_type": "im.chat.member.bot.added_v1", "event_id": "evt_add_1"}, "event": {"chat_id": "oc_new", "operator_id": {"open_id": "ou_a"}}}
    kind, payload = ch.classify(added)
    check("进群事件只分类，不在渠道里回复", kind == "member" and payload["event_type"].endswith("added_v1") and payload["body"].get("chat_id") == "oc_new")


def test_memory_and_degrade():
    cfg = {
        "profile": "placeholder", "bot_open_id": "ou_bot_placeholder", "bot_names": ["小助手"],
        "write": {"enabled": True, "allowed_users": {"user_admin": "管理员"}, "resources": {"folder_token": "fld_placeholder"}, "confirm_ttl_sec": 300},
        "tools": {"history": {"enabled": True, "days": 7}, "calendar_read": {"enabled": True}, "docs_create": {"enabled": True}, "calendar_create": {"enabled": True}},
    }
    base = tempfile.mkdtemp(prefix="agentbot_ch_")
    log = logging.getLogger("memch")
    ch = MemoryChannel(cfg)
    ch.members["oc_placeholder"] = ["user_admin", "user_m1"]
    ch.push({"message_id": "om_h1", "chat_id": "oc_placeholder", "chat_type": "group", "sender_id": "user_m1",
             "content": "周五交报名", "message_type": "text", "create_time": "2026-10-19T10:00:00+08:00"})
    t = Tools(cfg, base, lambda: ["lark-cli"], log, dry=True)
    t.channel = ch
    called = []
    t.fake = lambda argv: called.append(list(argv)) or {"ok": True, "data": {}}
    ctx = {"user": "user_admin", "chat_id": "oc_placeholder", "chat_type": "group", "message_id": "om_ask", "scope": ""}
    text = t.run_read("history", {}, ctx)
    check("内存渠道能读群历史，并且不调用 lark-cli", "周五交报名" in text and not called, text)
    try:
        t.run_read("calendar_read", {}, ctx)
        cal = ""
    except ToolError as e:
        cal = e.user_msg
    check("没有日历时核心拒绝，不打到传输层", "日历" in cal and not called, cal)
    try:
        t.propose_write("calendar_create", {"summary": "评审", "start": "2026-10-21T15:00:00+08:00", "end": "2026-10-21T16:00:00+08:00"}, ctx)
        proposed = ""
    except ToolError as e:
        proposed = e.user_msg
    check("没有日历时写提案也不会生成确认码", "日历" in proposed and not t.pending, proposed)
    # 文档能力打开之后，确认码仍绑发起人。渠道不能绕过。
    t.channel = MemoryChannel(cfg, caps=set(ch.capabilities()) | {Cap.DOCS})
    msg = t.propose_write("docs_create", {"title": "周报", "markdown": "正文"}, ctx)
    found = re.search(r"确认 (\d{6})", msg or "")
    code = found.group(1) if found else ""
    other = {"user": "user_other", "chat_id": "oc_placeholder", "chat_type": "group", "message_id": "om_other", "scope": ""}
    denied = t.confirm(code, other) if code else ""
    check("别的人拿不到确认码，渠道没有执行写入", code and "只有发起人" in denied and not called, denied)
    ok = t.confirm(code, ctx) if code else ""
    check("发起人确认后才调用原来的写入命令", code and "已完成" in ok and called and called[0][:2] == ["docs", "+create"], (ok, called))
    j, err = ch.list_members("oc_placeholder")
    ids = [u["member_id"] for u in (j or {}).get("data", {}).get("users", [])]
    check("内存渠道的成员名单是完整的", err == "" and ids == ["user_admin", "user_m1"] and j["data"]["user_total"] == 2, j)
    t.channel.send_text("om_ask", "13812345678 不应由渠道改写", "re_om_ask")
    sent = t.channel.outbox[-1]
    check("渠道发送不负责脱敏，原文交给核心先处理", sent["text"] == "13812345678 不应由渠道改写" and sent["key"] == "re_om_ask")
    import shutil
    shutil.rmtree(base, ignore_errors=True)


def test_wecom_skeleton():
    cfg = {"channel": "wecom", "bot_open_id": "bot_placeholder", "wecom": {"secret": "SECRET_SHOULD_NOT_LEAK"}}
    try:
        channels.open_channel(cfg)
        err = ""
    except ChannelError as e:
        err = e.user_msg
    check("没开实验开关时不加载企业微信，报错不含密钥", "实验" in err and "SECRET_SHOULD_NOT_LEAK" not in err, err)
    os.environ["AGENTBOT_EXPERIMENTAL_CHANNELS"] = "1"
    try:
        try:
            channels.open_channel(cfg)
            leaked = "loaded"
        except ChannelError as e:
            leaked = e.user_msg
        check("密钥写进配置会被拒绝，并且不回显", "不能写进配置" in leaked and "SECRET_SHOULD_NOT_LEAK" not in leaked, leaked)
        hook = {"channel": "wecom", "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=placeholder"}
        try:
            channels.open_channel(hook)
            hook_err = ""
        except ChannelError as e:
            hook_err = e.user_msg
        check("消息推送的 webhook 不能当收消息的配置", "webhook" in hook_err, hook_err)
        plain = {"channel": "wecom", "bot_names": ["小助手"], "wecom": {"bot_id": "bot_placeholder", "secret_env": "WECOM_BOT_SECRET"}}
        ch = channels.open_channel(plain)
        check("实验开关只放出骨架，ready 为假", isinstance(ch, WeComChannel) and ch.experimental and not ch.ready() and "不" in ch.blocked_reason() and not ch.supports(Cap.HISTORY) and not ch.supports(Cap.CALENDAR) and ch.supports(Cap.SEND_TEXT))
        body = {"cmd": "aibot_msg_callback", "headers": {"req_id": "r1"}, "body": {
            "msgid": "msg_placeholder", "aibotid": "bot_placeholder", "chatid": "wr_placeholder", "chattype": "group",
            "from": {"userid": "user_placeholder"}, "msgtype": "text", "text": {"content": "@小助手 进度"}}}
        kind, ev = ch.classify(body)
        check("群里的文本回调收成核心事件，并带上 @ 机器人",
              kind == "message" and ev["chat_type"] == "group" and ev["sender_id"] == "user_placeholder"
              and ev["message_id"] == "msg_placeholder" and ev["mentions"][0]["id"] == "bot_placeholder"
              and "进度" in ev["content"] and "req_id" not in json.dumps(ev), ev)
        welcome = {"msgtype": "event", "msgid": "evt_1", "event": {"eventtype": "enter_chat"}}
        check("进入会话不当成提问，骨架不会回欢迎语", to_inbound(welcome, "bot_placeholder") is None and ch.classify(welcome)[0] == "drop")
        try:
            ch.send_text("msg_placeholder", "你好", "re_1")
            sent = "sent"
        except ChannelError as e:
            sent = e.user_msg
        check("骨架的发送不会出去", "尚未连接" in sent, sent)
    finally:
        os.environ.pop("AGENTBOT_EXPERIMENTAL_CHANNELS", None)


def test_wired_default():
    """默认配置进主程序后，渠道是飞书，工具层挂的是同一个对象。"""
    empty = os.path.join(tempfile.gettempdir(), "agentbot_ch_empty.jsonl")
    open(empty, "w", encoding="utf-8").write("")
    src = r'''
import sys
sys.argv = ["agentbot.py", sys.argv[1], "--dry-run", sys.argv[2]]
import agentbot
from channels.base import Cap
print("NAME", agentbot.CHANNEL.name)
print("CARD", int(agentbot.CHANNEL.supports(Cap.CARD_UPDATE)))
print("CAL", int(agentbot.CHANNEL.supports(Cap.CALENDAR)))
print("SAME", int(agentbot.TOOLS.channel is agentbot.CHANNEL))
print("KEY", "channel" in agentbot.CFG)
'''
    r = subprocess.run([sys.executable, "-c", src, os.path.join(FW, "agents", "example.json"), empty],
                       cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    out = (r.stdout or "") + (r.stderr or "")
    check("旧示例配置加载后仍是飞书，且没有 channel 键",
          r.returncode == 0 and "NAME feishu" in out and "CARD 1" in out and "CAL 1" in out and "SAME 1" in out and "KEY False" in out, out)


def main():
    test_defaults()
    test_feishu_argv()
    test_memory_and_degrade()
    test_wecom_skeleton()
    test_wired_default()
    passed = sum(1 for _, ok in results if ok)
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
