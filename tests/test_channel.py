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
    # 回归夹具只在环境变量存在时生效。这里先清掉，避免默认能力被夹具改掉。
    os.environ.pop("AGENTBOT_MEMORY_FIXTURE", None)
    os.environ.pop("AGENTBOT_MEMORY_TRACE", None)
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
        pushed = ch.push_text("user", "user_placeholder", "你好", "k1")
        check("骨架的主动发送返回 fail，不连接", pushed == "fail")
    finally:
        os.environ.pop("AGENTBOT_EXPERIMENTAL_CHANNELS", None)


def test_push_text():
    seen = []

    def run_capture(argv, cwd=None, timeout=None):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "data": {"message_id": "om_push"}}', "")

    folder = tempfile.mkdtemp(prefix="agentbot_push_")
    try:
        cfg = {"profile": "placeholder", "bot_open_id": "ou_bot_placeholder"}
        ch = FeishuChannel(cfg, exe=lambda: ["lark-cli"], api=lambda *a, **k: ({}, ""), tmp=folder,
                           dry=True, log=logging.getLogger("ch"), run_capture=run_capture, names=())
        result = ch.push_text("user", "ou_owner_placeholder", "长连接断了", "k" + ("a" * 20))
        cmd = seen[-1] if seen else []
        check("飞书主动发送是 messages-send，不启动 consume",
              result == "ok" and cmd[:3] == ["lark-cli", "im", "+messages-send"]
              and "--user-id" in cmd and cmd[cmd.index("--user-id") + 1] == "ou_owner_placeholder"
              and "consume" not in cmd and "--markdown" not in cmd, cmd)
        seen.clear()
        result = ch.push_text("chat", "oc_chat_placeholder", "今天的安排", "k" + ("b" * 20))
        cmd = seen[-1] if seen else []
        check("群摘要走 --chat-id，同样不启动 consume",
              result == "ok" and "--chat-id" in cmd and cmd[cmd.index("--chat-id") + 1] == "oc_chat_placeholder"
              and "consume" not in cmd and "--markdown" not in cmd, cmd)
        mem = MemoryChannel(cfg)
        got = mem.push_text("user", "ou_owner_placeholder", "恢复了", "kmem")
        check("内存渠道记下主动发送并返回 ok",
              got == "ok" and mem.outbox[-1]["op"] == "push_text" and mem.outbox[-1]["kind"] == "user"
              and mem.outbox[-1]["target"] == "ou_owner_placeholder")
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="agentbot_push_")
    try:
        sample = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
        sample["base_dir"] = folder
        cfg_path = os.path.join(folder, "bot.json")
        json.dump(sample, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
        empty = os.path.join(folder, "empty.jsonl")
        open(empty, "w", encoding="utf-8").write("")
        real_log = os.path.join(FW, "logs", "bot.log")
        before_log = os.path.getsize(real_log) if os.path.isfile(real_log) else None
        src = r'''
import sys
sys.argv = ["agentbot.py", sys.argv[1], "--dry-run", sys.argv[2]]
import agentbot
seen = []
def fake(kind, target, text, key):
    seen.append((kind, target, text, key))
    return "ok"
agentbot.CHANNEL.push_text = fake
print("SEND", agentbot._outbound_send("user", "ou_owner_placeholder", "hello", "kabc"))
print("ARGS", seen)
def bad(kind, target, text, key):
    return "nope"
agentbot.CHANNEL.push_text = bad
print("BAD", agentbot._outbound_send("user", "ou_owner_placeholder", "hello", "kabc"))
def boom(kind, target, text, key):
    raise RuntimeError("down")
agentbot.CHANNEL.push_text = boom
print("EXC", agentbot._outbound_send("user", "ou_owner_placeholder", "hello", "kabc"))
'''
        r = subprocess.run([sys.executable, "-c", src, cfg_path, empty],
                           cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        after_log = os.path.getsize(real_log) if os.path.isfile(real_log) else None
        temp_log = os.path.join(folder, "logs", "bot.log")
        temp_body = open(temp_log, encoding="utf-8", errors="replace").read() if os.path.isfile(temp_log) else ""
        check("核心的主动发送只调用渠道 push_text",
              r.returncode == 0 and "SEND ok" in out
              and "('user', 'ou_owner_placeholder', 'hello', 'kabc')" in out
              and "BAD fail" in out and "EXC fail" in out and "event consume" not in out, out)
        check("假的 RuntimeError 写进临时日志，不写仓库 logs/bot.log",
              before_log == after_log and "RuntimeError: down" in temp_body and "boom" in temp_body, temp_body[-400:])
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def test_wired_default():
    """默认配置进主程序后，渠道是飞书，工具层挂的是同一个对象。"""
    folder = tempfile.mkdtemp(prefix="agentbot_chwire_")
    try:
        sample = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
        sample["base_dir"] = folder
        cfg_path = os.path.join(folder, "bot.json")
        json.dump(sample, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
        empty = os.path.join(folder, "empty.jsonl")
        open(empty, "w", encoding="utf-8").write("")
        src = r'''
import sys
sys.argv = ["agentbot.py", sys.argv[1], "--dry-run", sys.argv[2]]
import agentbot
from channels.base import Cap
from channels.feishu import MEMBER_ADDED, MEMBER_DELETED
print("NAME", agentbot.CHANNEL.name)
print("CARD", int(agentbot.CHANNEL.supports(Cap.CARD_UPDATE)))
print("CAL", int(agentbot.CHANNEL.supports(Cap.CALENDAR)))
print("SAME", int(agentbot.TOOLS.channel is agentbot.CHANNEL))
print("KEY", "channel" in agentbot.CFG)
print("ONE", int(agentbot.MEMBER_ADDED is MEMBER_ADDED and agentbot.MEMBER_DELETED is MEMBER_DELETED))
print("GONE", int(any(hasattr(agentbot, n) for n in ("_is_recall", "_member_type", "_member_body", "_event_id", "_RECALL_TYPES", "_MEMBER_ADDED", "_MEMBER_DELETED"))))
'''
        r = subprocess.run([sys.executable, "-c", src, cfg_path, empty],
                           cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        check("旧示例配置加载后仍是飞书，且没有 channel 键",
              r.returncode == 0 and "NAME feishu" in out and "CARD 1" in out and "CAL 1" in out and "SAME 1" in out and "KEY False" in out
              and "ONE 1" in out and "GONE 0" in out, out)
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def test_eval_fixture():
    """回归测试的夹具和追踪。变量不存在时，内存渠道和以前一样。"""
    import shutil
    cfg = {"bot_open_id": "bot_eval", "bot_names": ["小助手"], "profile": "eval_profile"}
    os.environ.pop("AGENTBOT_MEMORY_FIXTURE", None)
    os.environ.pop("AGENTBOT_MEMORY_TRACE", None)
    plain = MemoryChannel(cfg)
    check("没设夹具时仍没有日历，也没有预置文件", Cap.CALENDAR not in plain.capabilities() and plain.files == {})
    folder = tempfile.mkdtemp(prefix="agentbot_fix_")
    try:
        src = os.path.join(folder, "note.txt")
        open(src, "w", encoding="utf-8").write("夹具正文")
        fix = os.path.join(folder, "fix.json")
        json.dump({
            "caps": ["calendar"],
            "members": {"chat_eval": ["user_member"]},
            "messages": [{"message_id": "eval_h1", "chat_id": "chat_eval", "content": "历史一句", "message_type": "text"}],
            "files": {"eval_f1": [{"name": "note.txt", "path": src}]},
        }, open(fix, "w", encoding="utf-8"), ensure_ascii=False)
        trace = os.path.join(folder, "trace.jsonl")
        os.environ["AGENTBOT_MEMORY_FIXTURE"] = fix
        os.environ["AGENTBOT_MEMORY_TRACE"] = trace
        loaded = MemoryChannel(cfg)
        check("夹具可以补上日历能力", Cap.CALENDAR in loaded.capabilities())
        check("夹具里的成员和历史能读到",
              loaded.members.get("chat_eval") == ["user_member"]
              and "历史一句" in json.dumps(loaded.history, ensure_ascii=False))
        kind, payload = loaded.classify({"event": {"message_id": "eval_wrap", "content": "包一层"}})
        check("包在 event 里的消息能交给核心", kind == "message" and (payload or {}).get("content") == "包一层")
        kind2, payload2 = loaded.classify({"message_id": "eval_flat", "content": "平的"})
        check("平铺消息仍按原来的方式收下", kind2 == "message" and (payload2 or {}).get("message_id") == "eval_flat")
        down = os.path.join(folder, "down")
        os.makedirs(down)
        loaded.stage_download("attachment", ["im", "+messages-mget", "--message-ids", "eval_f1"], down)
        got_path = os.path.join(down, "lark-im-resources", "x", "note.txt")
        got = open(got_path, encoding="utf-8").read() if os.path.isfile(got_path) else ""
        check("下载目录能放上夹具文件", got == "夹具正文", got)
        loaded.send_text("eval_f1", "回复", "k1")
        loaded.push_text("user", "user_member", "主动", "k2")
        lines = open(trace, encoding="utf-8").read()
        check("设置了追踪文件才记下发送", '"send_text"' in lines and '"push_text"' in lines, lines)
    finally:
        os.environ.pop("AGENTBOT_MEMORY_FIXTURE", None)
        os.environ.pop("AGENTBOT_MEMORY_TRACE", None)
        shutil.rmtree(folder, ignore_errors=True)
    again = MemoryChannel(cfg)
    check("清掉变量后又回到默认能力", Cap.CALENDAR not in again.capabilities() and again.files == {})


def main():
    test_defaults()
    test_feishu_argv()
    test_memory_and_degrade()
    test_eval_fixture()
    test_wecom_skeleton()
    test_push_text()
    test_wired_default()
    passed = sum(1 for _, ok in results if ok)
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
