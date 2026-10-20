# 写入确认：按钮、确认码、四种方式、别人点、过期、重放、篡改、退回确认码。不连飞书。
import json, logging, os, re, sqlite3, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import confirm_mode, doctor, feature_switch
from checks.check_config import check_data, load_schema
from tools import Tools

results = []
os.environ.pop("AGENTBOT_CARD_CALLBACK", None)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


def actor(user="user_admin", chat="chat_group1", scope="", mid="msg_src1"):
    return {"user": user, "chat_id": chat, "chat_type": "group", "message_id": mid,
            "scope": scope, "question": "建一份文档"}


def new_tools(cards, **confirm):
    base = tempfile.mkdtemp(prefix="confirm_")
    os.makedirs(os.path.join(base, "state"), exist_ok=True)
    with open(os.path.join(base, "redact.txt"), "w", encoding="utf-8") as handle:
        handle.write("杭州某某科技有限公司\n")
    os.environ["AGENTBOT_CARD_CALLBACK"] = "1" if cards else "0"
    cfg = {
        "name": "t",
        "profile": "placeholder",
        "redact_file": "redact.txt",
        "tools": {
            "docs_create": {"enabled": True},
            "calendar_create": {"enabled": True},
            "calendar_create_batch": {"enabled": True},
        },
        "write": {
            "enabled": True,
            "confirm_ttl_sec": 300,
            "allowed_users": {"user_admin": "管理员"},
            "resources": {
                "folder_token": "fldTEST",
                "docs_append_tokens": ["doxTEST"],
                "calendar_id": "calTEST",
                "base_token": "basTEST",
                "table_id": "tblTEST",
            },
        },
        "skills": {"enabled": False},
        "memory": {"enabled": False},
    }
    if confirm:
        cfg["confirm"] = dict(confirm)
    log = logging.getLogger("confirm-test")
    tools = Tools(cfg, base, lambda: ["lark-cli"], log, dry=True)
    calls = []

    def fake(argv):
        calls.append(list(argv))
        return {"ok": True, "data": {"url": "https://example.com/docx/NEW"}}

    tools.fake = fake
    return tools, calls, base, cfg


def propose(tools, name="docs_create", args=None, ctx=None):
    ctx = actor() if ctx is None else ctx
    if args is None:
        args = {"title": "周报", "markdown": "内容"}
    text = tools.propose_write(name, args, ctx)
    return text, tools.take_confirm_card(), ctx


def buttons(card):
    found = {}
    for el in (card or {}).get("card", {}).get("body", {}).get("elements", []):
        if el.get("tag") != "button":
            continue
        val = el["behaviors"][0]["value"]
        found[val["op"]] = val
    return found


def only(tools):
    code, rec = next(iter(tools.pending.items()))
    return code, rec


def switch_cfg(base):
    return {
        "name": "t",
        "profile": "placeholder",
        "report": {"enabled": False, "owner_open_ids": ["user_owner"]},
        "alerts": {"enabled": False},
        "write": {"allowed_users": {"user_owner": "管理员"}, "confirm_ttl_sec": 300},
        "knowledge": {"enabled": False},
        "memory": {"enabled": False},
        "digest": {"enabled": False},
        "skills": {"enabled": False},
        "confirm": {"mode": "button", "enabled": True},
    }


def switch_ctx():
    return {"user": "user_owner", "chat_id": "chat_private", "chat_type": "p2p",
            "message_id": "msg_sw1", "scope": ""}


def button_op(card, op):
    for el in card.get("body", {}).get("elements", []):
        if el.get("tag") != "button":
            continue
        val = el["behaviors"][0]["value"]
        if val.get("op") == op:
            return val
    return None


# ---------- 策略 ----------
check("没写 confirm 时默认点按钮，写成关掉也仍是点按钮",
      confirm_mode.configured_mode({}) == "button"
      and confirm_mode.configured_mode({"confirm": {"mode": "off"}}) == "button"
      and confirm_mode.configured_mode({"confirm": {"mode": "none"}}) == "button")
check("没写 high_risk_mode 时跟 mode 走",
      confirm_mode.high_risk_mode({"confirm": {"mode": "code"}}) == "code"
      and confirm_mode.high_risk_mode({"confirm": {"mode": "button", "high_risk_mode": "button_and_code"}}) == "button_and_code")
check("批量写入、改开关、重启算高风险",
      confirm_mode.is_high_risk("calendar_create_batch") and confirm_mode.is_high_risk("feature_switch")
      and confirm_mode.is_high_risk("restart") and confirm_mode.is_high_risk("confirm_mode")
      and not confirm_mode.is_high_risk("docs_create"))
try:
    confirm_mode.write_mode({}, "off")
    check("write_mode 拒绝关掉", False)
except ValueError:
    check("write_mode 拒绝关掉", True)
kept = {"name": "t", "confirm": {"mode": "code", "enabled": True, "ttl_sec": 600}}
turned = confirm_mode.write_mode(kept, "button_or_code")
check("换确认方式只改 mode，并丢掉关掉用的键",
      turned["confirm"]["mode"] == "button_or_code" and "enabled" not in turned["confirm"]
      and turned["confirm"]["ttl_sec"] == 600 and kept["confirm"]["enabled"] is True)
check("普通聊天里提到确认不会被当成关掉确认",
      confirm_mode.refusing_off("关掉确认") and not confirm_mode.refusing_off("不要确认收到这份周报"))
check("断线和超时不算没开通卡片回调",
      confirm_mode.ready_line("feishu-websocket: connected")
      and not confirm_mode.denied_line("websocket: close 1006")
      and not confirm_mode.denied_line("connection reset by peer")
      and confirm_mode.denied_line("permission denied for card.action.trigger"))

schema = load_schema()
example = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
ok_cfg = confirm_mode.write_mode(example, "button_or_code")
ok_err, _hints = check_data(ok_cfg, "copied.json", schema)
check("配置检查接受四种确认方式", not ok_err, ok_err)
bad_cfg = confirm_mode.write_mode(example, "button")
bad_cfg["confirm"]["mode"] = "off"
bad_err, _hints = check_data(bad_cfg, "copied.json", schema)
check("配置检查不接受把确认关掉", bool(bad_err), bad_err)

# ---------- 退回确认码 ----------
tools, calls, _base, _cfg = new_tools(False)
text, card, ctx = propose(tools)
code, rec = only(tools)
check("没开通卡片回调时仍是原来的确认码提示，并且不发卡片",
      card is None and rec["mode"] == "code" and rec["fallback"] is True
      and text == "将要执行：在专用文件夹新建文档《周报》\n请发起人在 5 分钟内 @我 回复「确认 %s」执行，或「取消 %s」。" % (code, code)
      and not calls, text)

# ---------- 本人点击、别人点击、篡改、过期、重放 ----------
tools, calls, _base, _cfg = new_tools(True, mode="button", ttl_sec=600)
text, card, ctx = propose(tools, ctx=actor(scope="topic_a"))
code, rec = only(tools)
vals = buttons(card)
check("按钮模式的卡片写明要做什么，并且带确认和取消",
      card and "周报" in text and "请点下面的「确认」" in text
      and vals.get("yes", {}).get("kind") == "write_confirm" and "no" in vals
      and "确认 %s" % code not in text and not calls, text)
other = actor(user="user_other", scope="topic_a", mid="msg_other")
denied = tools.confirm_click(rec["token"], "yes", rec["action_hash"], other, "msg_card1")
check("别人点击不执行，也不把这次用掉",
      "只有发起人" in denied["text"] and not denied["ok"] and not calls and rec["token"] in [p.get("token") for p in tools.pending.values()],
      denied["text"])
typed = tools.confirm(code, actor(scope="topic_a", mid="msg_type1"))
check("按钮模式不接受回复数字", "请点卡片" in typed and not calls, typed)
tampered = tools.confirm_click(rec["token"], "yes", "f" * 64, actor(scope="topic_a"), "msg_card1")
check("动作被改过不执行，也不把这次用掉",
      "对不上" in tampered["text"] and not calls and code in tools.pending, tampered["text"])
wrong_topic = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(scope="topic_b"), "msg_card1")
check("别的话题点下来不算", "只有发起人" in wrong_topic["text"] and code in tools.pending, wrong_topic["text"])
tools.bind_card_message(rec["token"], "msg_card1")
wrong_card = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(scope=""), "msg_othercard")
check("不是原来那张卡片不算", "不是原来的那张卡片" in wrong_card["text"] and not calls and code in tools.pending, wrong_card["text"])
empty_scope = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(scope=""), "msg_card1")
check("本人点击、卡片没带话题时可以确认并执行",
      empty_scope["text"].startswith("已完成") and empty_scope["ok"] and empty_scope["patch"]
      and empty_scope["card"]["header"]["title"]["content"] == "已确认" and len(calls) == 1, empty_scope["text"])
again = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(scope=""), "msg_card1")
check("同一张卡片再点一次不会重复执行",
      "已经处理过" in again["text"] and len(calls) == 1, again["text"])

tools, calls, _base, _cfg = new_tools(True, mode="button", ttl_sec=600)
text, card, ctx = propose(tools)
code, rec = only(tools)
rec["expires"] = time.time() - 5
tools.pending[code] = rec
tools._update_pending(code, rec)
expired = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
check("过期的按钮不执行", "过期" in expired["text"] and not calls and code not in tools.pending, expired["text"])
late = tools.confirm(code, actor(mid="msg_late"))
check("过期之后回复确认码也无效", "无效或已过期" in late and not calls, late)

tools, calls, _base, _cfg = new_tools(True, mode="button")
text, card, ctx = propose(tools)
code, rec = only(tools)
cancelled = tools.confirm_click(rec["token"], "no", rec["action_hash"], actor(), "msg_card1")
check("点取消不会写入", cancelled["text"] == "已取消。" and not calls and cancelled["card"]["header"]["title"]["content"] == "已取消", cancelled["text"])

# ---------- 四种方式 ----------
tools, calls, _base, _cfg = new_tools(True, mode="code")
text, card, ctx = propose(tools)
code, rec = only(tools)
clicked = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
check("确认码模式不接受点按钮", "请回复确认码" in clicked["text"] and not calls and code in tools.pending, clicked["text"])
done = tools.confirm(code, actor(mid="msg_code1"))
check("确认码模式本人回复后执行", done.startswith("已完成") and len(calls) == 1, done)
replay = tools.confirm(code, actor(mid="msg_code2"))
check("确认码用过一次就无效", "无效或已过期" in replay and len(calls) == 1, replay)

tools, calls, _base, _cfg = new_tools(True, mode="button_or_code", ttl_sec=600)
text, card, ctx = propose(tools)
code, rec = only(tools)
check("两种都行时，卡片和确认码都会给出来",
      card and ("确认 %s" % code) in text and "取消 %s" % code in text, text)
by_code = tools.confirm(code, actor(mid="msg_or1"))
check("两种都行时，只回复确认码也能执行", by_code.startswith("已完成") and len(calls) == 1, by_code)

tools, calls, _base, _cfg = new_tools(True, mode="button_or_code")
text, card, ctx = propose(tools)
code, rec = only(tools)
by_click = tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
check("两种都行时，只点按钮也能执行", by_click["text"].startswith("已完成") and len(calls) == 1, by_click["text"])

def both(order):
    box, made, _base, _cfg = new_tools(True, mode="button_and_code", ttl_sec=600)
    body, _card, _ctx = propose(box)
    num, rec = only(box)
    if order == "click":
        first = box.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
        held = len(made)
        second = box.confirm(num, actor(mid="msg_and1"))
        first_ok = "确认码" in first["text"] and held == 0
    else:
        first = box.confirm(num, actor(mid="msg_and1"))
        held = len(made)
        second = box.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
        first_ok = "请再点卡片" in first and held == 0
    second_text = second["text"] if isinstance(second, dict) else second
    return first_ok and str(second_text).startswith("已完成") and len(made) == 1, first, second_text

ok_click, first_c, second_c = both("click")
check("两样都要时，先点按钮再回复确认码才执行", ok_click, "%s\n%s" % (first_c, second_c))
ok_code, first_d, second_d = both("code")
check("两样都要时，先回复确认码再点按钮才执行", ok_code, "%s\n%s" % (first_d, second_d))

tools, calls, _base, _cfg = new_tools(True, mode="button", high_risk_mode="code")
plain, plain_card, _ctx = propose(tools)
batch, batch_card, _ctx = propose(tools, "calendar_create_batch", {"events": [
    {"summary": "评审", "start": "2026-10-21T15:00:00+08:00", "end": "2026-10-21T16:00:00+08:00"},
    {"summary": "复盘", "start": "2026-10-22T15:00:00+08:00", "end": "2026-10-22T16:00:00+08:00"},
]})
check("高风险可以单独改成确认码，普通写入仍点按钮",
      plain_card and batch_card is None and "确认 " in batch and "一次创建 2 个日程" in batch, batch)

tools, calls, base, _cfg = new_tools(True, mode="button", code_digits=4, code_ttl_sec=120)
os.environ["AGENTBOT_CARD_CALLBACK"] = "0"
text, card, ctx = propose(tools)
code, rec = only(tools)
check("确认码位数可以改成 4，短有效期不会被抬高",
      card is None and len(code) == 4 and rec["expires"] - rec["ts"] == 120, text)

# ---------- 卡片发不出去就改回确认码 ----------
tools, calls, _base, _cfg = new_tools(True, mode="button", ttl_sec=600)
text, card, ctx = propose(tools)
code, rec = only(tools)
shown = tools.reveal_code(rec["token"])
done = tools.confirm(code, actor(mid="msg_fb1"))
check("卡片发不出去时改回确认码，本人仍能确认",
      code in shown and "改用确认码" in shown and done.startswith("已完成") and len(calls) == 1, shown + "\n" + done)

# ---------- 审计脱敏 ----------
tools, calls, base, _cfg = new_tools(True, mode="button")
propose(tools, args={"title": "杭州某某科技有限公司周报", "markdown": "内容"})
code, rec = only(tools)
tools.confirm_click(rec["token"], "yes", rec["action_hash"], actor(), "msg_card1")
folder = os.path.join(base, "audit")
blob = ""
for name in os.listdir(folder):
    blob += open(os.path.join(folder, name), encoding="utf-8").read()
rows = [json.loads(line) for line in blob.splitlines() if line.strip()]
raw_keys = [row for row in rows if "code" in row or "token" in row]
check("写入日志不留确认码、按钮记号和人的编号，客户名会换成某客户",
      rows and not raw_keys and "user_admin" not in blob and "杭州某某科技有限公司" not in blob
      and "某客户" in blob and any(row.get("code_hash") and row.get("token_hash") for row in rows), blob[:800])

# ---------- 私聊里换确认方式，不能关掉 ----------
os.environ["AGENTBOT_CARD_CALLBACK"] = "1"
base = tempfile.mkdtemp(prefix="confirm_sw_")
cfg_path = os.path.join(base, "agent.json")
cfg = switch_cfg(base)
with open(cfg_path, "w", encoding="utf-8") as handle:
    json.dump(cfg, handle, ensure_ascii=False)
ctx = switch_ctx()
refused = feature_switch.handle_text(cfg, base, "关掉确认", ctx, (), cfg_path=cfg_path)
after_refuse = json.load(open(cfg_path, encoding="utf-8"))
check("私聊不能关掉确认，配置文件不动",
      refused and "不能关掉" in refused["text"] and after_refuse["confirm"].get("enabled") is True
      and after_refuse["confirm"]["mode"] == "button", refused["text"] if refused else "")
proposed = feature_switch.handle_text(cfg, base, "确认方式 确认码", ctx, (), cfg_path=cfg_path)
yes = button_op(proposed.get("card") or {}, "yes")
stranger = feature_switch.try_click(
    cfg, base, cfg_path, yes["id"], "yes", yes["hash"],
    dict(ctx, user="user_other", message_id="msg_sw2"), apply=True, check=lambda _p: ([], []), message_id="msg_sw9")
still = json.load(open(cfg_path, encoding="utf-8"))
check("别人不能替主人改确认方式",
      stranger and "只有你本人" in stranger["text"] and still["confirm"]["mode"] == "button", stranger["text"] if stranger else "")
changed = feature_switch.try_click(
    cfg, base, cfg_path, yes["id"], "yes", yes["hash"], ctx, apply=True,
    check=lambda _p: ([], []), message_id="msg_sw1")
written = json.load(open(cfg_path, encoding="utf-8"))
check("主人点确认后才改确认方式，并要求重启",
      changed and changed.get("restart") and written["confirm"]["mode"] == "code" and "enabled" not in written["confirm"],
      changed["text"] if changed else "")
replayed = feature_switch.try_click(
    cfg, base, cfg_path, yes["id"], "yes", yes["hash"], ctx, apply=True,
    check=lambda _p: ([], []), message_id="msg_sw1")
check("改确认方式的按钮也不能再点一次", replayed and "已经处理过" in replayed["text"], replayed["text"] if replayed else "")

os.environ["AGENTBOT_CARD_CALLBACK"] = "0"
base = tempfile.mkdtemp(prefix="confirm_swc_")
cfg = switch_cfg(base)
cfg["confirm"] = {"mode": "button", "high_risk_mode": "button_and_code"}
item = feature_switch.feature("report")
offered = feature_switch._propose(cfg, base, switch_ctx(), item, True, (), time.time())
check("卡片没准备好时，功能开关仍给确认码",
      offered and offered.get("card") is None and "确认 " in offered["text"] and "取消 " in offered["text"], offered["text"] if offered else "")

os.environ["AGENTBOT_CARD_CALLBACK"] = "1"
base = tempfile.mkdtemp(prefix="confirm_swb_")
cfg = switch_cfg(base)
cfg["confirm"] = {"mode": "button", "high_risk_mode": "button_and_code", "ttl_sec": 600}
offered = feature_switch._propose(cfg, base, switch_ctx(), item, True, (), time.time())
yes = button_op(offered.get("card") or {}, "yes")
num = ""
db = sqlite3.connect(os.path.join(base, "state", "switches.db"))
for row_code, payload in db.execute("select code, payload from pending_switches"):
    rec = json.loads(payload)
    if rec.get("token") == yes["id"]:
        num = row_code
db.close()
half = feature_switch.try_click(
    cfg, base, os.path.join(base, "missing.json"), yes["id"], "yes", yes["hash"], switch_ctx(),
    apply=False, message_id="msg_sw1")
rest = feature_switch.try_confirm(
    cfg, base, os.path.join(base, "missing.json"), num, switch_ctx(), apply=False)
check("高风险开关可以要求按钮和确认码两样都要",
      offered and yes and num and half and "确认码" in half["text"] and not half.get("restart")
      and rest and "演练" in rest["text"], "%s\n%s" % (half, rest))

menu = {"header": {"event_type": "card.action.trigger", "event_id": "evtmenu"},
        "event": {"operator": {"open_id": "user_owner"},
                  "action": {"value": {"switch": "report", "op": "flip"}},
                  "context": {"open_chat_id": "chat_private", "open_message_id": "msg_menu"}}}
turned = feature_switch.card_event(menu)
check("功能清单上的按钮不会被当成写入确认",
      confirm_mode.parse_click(menu) is None and turned and turned.get("content", "").startswith("第"), turned)

# ---------- doctor ----------
warn_level, warn_info = confirm_mode.doctor_card('{"events":["im.message.receive_v1"]}')
ok_level, ok_info = confirm_mode.doctor_card('{"subscribed_events":["card.action.trigger"]}')
blind_level, blind_info = confirm_mode.doctor_card("不是订阅列表")
check("看不出卡片回调时给出警告和开通步骤，看出来时是 OK",
      warn_level == "WARN" and "长连接" in warn_info and "card.action.trigger" in warn_info
      and ok_level == "OK" and blind_level == "WARN" and "不需要公网" in warn_info, warn_info)
events_default = doctor.required_events({"chat_ids": ["chat_group1"], "progress": {"enabled": False}})
events_code = doctor.required_events({"chat_ids": [], "chats": {"auto_join": True},
                                      "confirm": {"mode": "code", "high_risk_mode": "code"}})
check("要按钮时诊断会提到卡片回调；纯确认码且没开自动认群之外不硬加",
      "card.action.trigger" in events_default and "im.chat.member.bot.added_v1" not in events_default
      and "card.action.trigger" not in events_code and "im.chat.member.bot.added_v1" in events_code,
      events_default)

note_base = tempfile.mkdtemp(prefix="confirm_note_")
confirm_mode.note_cards(note_base, False, "token=sekretvalue secret=zzz permission denied")
noted = json.load(open(os.path.join(note_base, "state", "card_callback.json"), encoding="utf-8"))
check("记下卡片回调状态时不留令牌",
      noted.get("ok") is False and "sekretvalue" not in json.dumps(noted) and "zzz" not in json.dumps(noted), noted)

cfg_doc = {"profile": "placeholder", "features": {"p2p": False, "attachments": False},
           "progress": {"enabled": False}, "tools": {}, "chat_ids": ["chat_group1"]}
need = doctor.required_scopes(cfg_doc)


def run_doc(argv):
    if "consume" in argv:
        raise AssertionError("doctor must not consume")
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identities": {"bot": {"available": True, "status": "ready"}}}) + "\n", ""
    if "scopes" in argv:
        return 0, json.dumps({"scopes": need}) + "\n", ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_doc, note_base, ["lark-cli"], run_doc, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {})
text = "\n".join(lines)
check("诊断在卡片回调没开通时是警告，不因此失败",
      code == 0 and "FAIL" not in text and "WARN" in text and "card" in text and "长连接" in text
      and "card.action.trigger" in text, text)

# ---------- PowerShell 5.1 ----------
reload = open(os.path.join(FW, "scripts", "reload.ps1"), "rb").read()
ps_bad = []
for name in sorted(os.listdir(os.path.join(FW, "scripts"))):
    if not name.endswith(".ps1"):
        continue
    raw = open(os.path.join(FW, "scripts", name), "rb").read()
    body = raw.decode("utf-8-sig")
    if any(b > 127 for b in raw) and not raw.startswith(b"\xef\xbb\xbf"):
        ps_bad.append("BOM " + name)
    for token in ("&&", "??", "-Parallel"):
        if token in body:
            ps_bad.append(token + " " + name)
check("重启脚本保持 UTF-8 BOM，脚本里没有 PowerShell 7 才有的写法",
      reload.startswith(b"\xef\xbb\xbf") and not ps_bad, ps_bad)

# ---------- 长连接回调进到点击处理 ----------
wire_base = tempfile.mkdtemp(prefix="confirm_wire_")
fake_path = os.path.join(wire_base, "fake.json")
json.dump({"docs +create": {"ok": True, "data": {"url": "https://example.com/docx/NEW"}}},
          open(fake_path, "w", encoding="utf-8"))
runner = os.path.join(wire_base, "wire.py")
open(runner, "w", encoding="utf-8").write(r'''
import json, os, sys, runpy
FW = os.environ["FW"]
base = os.environ["BASE"]
cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
cfg["base_dir"] = base
cfg["bot_open_id"] = "bot_placeholder"
cfg["bot_names"] = ["小助手"]
cfg["chat_ids"] = ["chat_group1"]
cfg["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
cfg["progress"] = {"enabled": False}
cfg.setdefault("memory", {})["enabled"] = False
cfg.setdefault("features", {})["tools"] = True
cfg["confirm"] = {"mode": "button", "ttl_sec": 600}
cfg.setdefault("write", {})["enabled"] = True
cfg["write"]["allowed_users"] = {"user_admin": "管理员"}
cfg["write"]["resources"] = {"folder_token": "fldTEST", "docs_append_tokens": ["doxTEST"],
                             "calendar_id": "calTEST", "base_token": "basTEST", "table_id": "tblTEST"}
cfg.setdefault("tools", {}).setdefault("docs_create", {})["enabled"] = True
path = os.path.join(base, "agent.json")
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
ev = {"message_id": "msg_ask1", "chat_id": "chat_group1", "chat_type": "group", "message_type": "text",
      "sender_id": "user_admin", "sender_type": "user",
      "content": "@小助手 <<FAKE_TOOL>>{\"tool\":\"docs_create\",\"args\":{\"title\":\"周报\",\"markdown\":\"内容\"}}<<END>> 建文档",
      "mentions": [{"id": "bot_placeholder", "key": "@_user_1", "name": "小助手"}]}
open(os.path.join(base, "events.jsonl"), "w", encoding="utf-8").write(json.dumps({"event": ev}, ensure_ascii=False) + "\n")
sys.argv = ["agentbot.py", path, "--dry-run", os.path.join(base, "events.jsonl")]
g = runpy.run_path(os.path.join(FW, "agentbot.py"), run_name="lib")
g["main"]()
pending = list(g["TOOLS"].pending.values())
if len(pending) != 1:
    print("PENDING %d" % len(pending))
    sys.exit(1)
rec = pending[0]
token, ah = rec["token"], rec["action_hash"]

def click(user, op="yes", h=None, mid="msg_card1"):
    return {"header": {"event_type": "card.action.trigger", "event_id": "evt" + user + op + mid[-4:]},
            "event": {"operator": {"open_id": user},
                      "action": {"value": {"kind": "write_confirm", "id": token, "op": op, "hash": ah if h is None else h}},
                      "context": {"open_chat_id": "chat_group1", "open_message_id": mid}}}

g["ingest"](click("user_other"))
g["ingest"](click("user_admin", h="f" * 64))
g["ingest"](click("user_admin"))
g["ingest"](click("user_admin"))
print("WIRED_DONE")
''')
env = dict(os.environ, FW=FW, BASE=wire_base, AGENTBOT_CARD_CALLBACK="1",
           AGENTBOT_FAKE_AGENT="0.2", AGENTBOT_FAKE_LARK=fake_path)
proc = subprocess.run([sys.executable, runner], cwd=FW, capture_output=True, text=True,
                      encoding="utf-8", errors="replace", env=env, timeout=90)
out = (proc.stdout or "") + (proc.stderr or "")
check("长连接里的卡片回调：别人点、篡改都不执行，本人点一次才写入，再点无效",
      proc.returncode == 0 and "WIRED_DONE" in out and "[dry-run] confirm-card" in out
      and "只有发起人" in out and "对不上" in out and "已完成" in out and "已经处理过" in out
      and out.count("[fake-lark] docs +create") == 1 and "[dry-run] patch-card" in out
      and "已确认" in out, out[-2000:])

os.environ.pop("AGENTBOT_CARD_CALLBACK", None)
failed = [name for name, ok in results if not ok]
print("%d passed, %d failed" % (len(results) - len(failed), len(failed)))
sys.exit(1 if failed else 0)
