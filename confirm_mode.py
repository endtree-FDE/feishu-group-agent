# 写入确认方式。默认点卡片上的按钮。飞书没开通卡片回调时，自动改用确认码。
# 确认不能关掉。高风险动作可以单独指定一种方式。
import hashlib, json, os, re, secrets, time

CARD_EVENT = "card.action.trigger"
MODES = ("button", "code", "button_or_code", "button_and_code")
_HIGH = ("calendar_create_batch", "feature_switch", "restart", "confirm_mode")
_OFF = ("off", "none", "disabled", "false", "0", "disable")
_CODE_RX = re.compile(r"^\d{4,8}$")
_DENY = re.compile(
    r"permission|forbidden|not subscribed|unknown event|event not found|99991672|"
    r"无权限|未订阅|未开通|没有权限|不认识这个事件",
    re.I)
_READY = re.compile(r"feishu-websocket:\s*connected|ready event_key=", re.I)
CARD_STEPS = (
    "没开通时，写入确认会自动改用确认码。"
    "开通：打开飞书开放平台，进入本应用的「事件与回调」，"
    "订阅方式选「使用长连接接收事件」，"
    "添加事件「卡片回传交互」（card.action.trigger），然后发布版本。"
    "不需要公网地址。"
)


def _block(cfg):
    raw = (cfg or {}).get("confirm")
    return raw if isinstance(raw, dict) else {}


def _pos(raw, default):
    try:
        num = int(raw)
    except (TypeError, ValueError):
        return default
    if num < 1:
        return default
    return num


def code_digits(cfg):
    """确认码位数。不写是 6。只接受 4 到 8。"""
    num = _pos(_block(cfg).get("code_digits", 6), 6)
    if num < 4 or num > 8:
        return 6
    return num


def code_ttl(cfg):
    """确认码有效秒数。优先 confirm.code_ttl_sec，否则沿用 write.confirm_ttl_sec，再不写是 300。"""
    block = _block(cfg)
    if "code_ttl_sec" in block and block.get("code_ttl_sec") not in (None, ""):
        return _pos(block.get("code_ttl_sec"), 300)
    raw = ((cfg or {}).get("write") or {}).get("confirm_ttl_sec", 300)
    return _pos(raw, 300)


def button_ttl(cfg):
    """按钮有效秒数。不写是 600（10 分钟）。"""
    return _pos(_block(cfg).get("ttl_sec", 600), 600)


def _named(raw, fallback):
    text = str(raw or "").strip().lower()
    if text in _OFF:
        return "button"
    if text in MODES:
        return text
    return fallback


def configured_mode(cfg):
    """没写 confirm.mode 时是 button。写成关掉也按 button，确认不能关。"""
    return _named(_block(cfg).get("mode"), "button")


def high_risk_mode(cfg):
    """没写 high_risk_mode 时，和高风险之外的方式相同。"""
    block = _block(cfg)
    if "high_risk_mode" not in block or block.get("high_risk_mode") in (None, ""):
        return configured_mode(cfg)
    return _named(block.get("high_risk_mode"), configured_mode(cfg))


def is_high_risk(name):
    return str(name or "") in _HIGH


def wants_button(mode):
    return mode in ("button", "button_or_code", "button_and_code")


def wants_code(mode):
    return mode in ("code", "button_or_code", "button_and_code")


def wants_cards(cfg):
    """配置里只要有一种方式要点按钮，就去订阅卡片回调。"""
    return wants_button(configured_mode(cfg)) or wants_button(high_risk_mode(cfg))


def card_callback_setting(cfg):
    """confirm.card_callback。不写是 auto。on 总是去启动，off 不启动，写错也按 auto。"""
    text = str(_block(cfg).get("card_callback", "auto") or "auto").strip().lower()
    if text in ("on", "true", "1", "yes"):
        return "on"
    if text in ("off", "false", "0", "no", "none"):
        return "off"
    return "auto"


def wants_card_process(cfg):
    """off 时即使选了点按钮也不启动卡片接收进程。auto 和 on 在配置层面仍算要启动，启动前再探测。"""
    if card_callback_setting(cfg) == "off":
        return False
    return wants_cards(cfg)


def drop_unsubscribed_card(keys, setting, probe):
    """auto 且探测明确没订阅时，这次不要把卡片事件放进要启动的列表。on 不去掉。"""
    keys = list(keys or [])
    if setting == "off" or (setting == "auto" and probe is False):
        return [key for key in keys if key != CARD_EVENT]
    return keys


def confirm_pattern(cfg):
    n = code_digits(cfg)
    return re.compile(r"^(确认|取消)\s*(\d{%d})$" % n)


def code_fullmatch(cfg, code):
    n = code_digits(cfg)
    return bool(re.fullmatch(r"\d{%d}" % n, str(code or "")))


def card_note_state(base=None, env=None):
    """True 连上过，False 明确没有，None 还没有记录。测试可用 AGENTBOT_CARD_CALLBACK=1/0 强制。"""
    env = os.environ if env is None else env
    flag = str(env.get("AGENTBOT_CARD_CALLBACK") or "")
    if flag == "1":
        return True
    if flag == "0":
        return False
    if not base:
        return None
    path = os.path.join(base, "state", "card_callback.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "ok" not in data:
        return None
    return bool(data.get("ok"))


def cards_ready(base=None, env=None, cfg=None):
    """只有明确连上过才算能点按钮。没有记录和明确没开通都先用确认码。
    confirm.card_callback 为 off 时一律不能点按钮。"""
    if card_callback_setting(cfg) == "off":
        return False
    return card_note_state(base, env) is True


def note_cards(base, ok, reason=""):
    """长连接连上或明确没有权限时记一笔。原因里不留令牌。"""
    if not base:
        return
    folder = os.path.join(base, "state")
    try:
        os.makedirs(folder, exist_ok=True)
        text = str(reason or "")
        text = re.sub(r"(?i)(token|secret|password)\s*[:=]\s*\S+", r"\1=[已隐藏]", text)
        payload = {"ok": bool(ok), "reason": text[:180], "ts": time.time()}
        tmp = os.path.join(folder, "card_callback.json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, "card_callback.json"))
    except OSError:
        pass


def ready_line(line):
    return bool(_READY.search(line or ""))


def denied_line(line):
    text = line or ""
    if _READY.search(text):
        return False
    if re.search(r"connection reset|timeout|websocket:\s*close|disconnected", text, re.I):
        return False
    return bool(_DENY.search(text))


_UNSUB = re.compile(r"failed_precondition|not subscribed|requires callbacks|未订阅|未开通", re.I)


def unsubscribed_exit(rc, text):
    """卡片接收进程因为后台没订阅而退出。日志里有 failed_precondition / not subscribed，或者退出码是 2。
    断线、超时不算。消息长连接退出码是 2 时不要拿这个函数去停它。"""
    if _UNSUB.search(text or ""):
        return True
    try:
        return int(rc) == 2
    except (TypeError, ValueError):
        return False


def interpret_probe(rc, text, still_running):
    """探测结果。False 明确没订阅。True 还在跑或已经连上。None 看不出来，调用方仍可去启动。"""
    if unsubscribed_exit(rc, text):
        return False
    if still_running or ready_line(text):
        return True
    return None


def card_notice_text():
    """私聊主人的那一句。不带编号，也不带日志原文。"""
    return (
        "写入确认先改用确认码。"
        "这个应用还没开通卡片回调，所以不会反复重连卡片事件。"
        "收消息的长连接不受影响，也不会因此发断线提醒。\n"
        + CARD_STEPS
    )


def _notice_path(base):
    return os.path.join(base, "state", "card_notice.json")


def notice_sent(base):
    if not base:
        return False
    try:
        with open(_notice_path(base), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("sent"))


def mark_notice(base):
    if not base:
        return
    folder = os.path.join(base, "state")
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "card_notice.json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"sent": True, "ts": time.time()}, handle)
        os.replace(tmp, _notice_path(base))
    except OSError:
        pass


def deliver_card_notice(base, owners, send):
    """send(owner) 返回 ok / unknown / fail。已经提醒过就不再发。发出去或结果看不懂才记一笔，避免再发一条。
    没有主人时不记，下次配了主人还可以发。返回这次实际发出的人数。"""
    if not base or notice_sent(base):
        return []
    sent = []
    for owner in owners or []:
        try:
            result = send(owner)
        except Exception:
            result = "fail"
        if result in ("ok", "unknown", True):
            sent.append(owner)
    if sent:
        mark_notice(base)
    return sent


def effective_mode(cfg, high, cards_ok):
    mode = high_risk_mode(cfg) if high else configured_mode(cfg)
    if cards_ok or not wants_button(mode):
        return mode
    return "code"


def fell_back(cfg, high, cards_ok):
    wanted = high_risk_mode(cfg) if high else configured_mode(cfg)
    return bool(wants_button(wanted) and not cards_ok)


def ttl_for(cfg, mode, fallback):
    if fallback or mode == "code":
        return code_ttl(cfg)
    if mode == "button":
        return button_ttl(cfg)
    return max(button_ttl(cfg), code_ttl(cfg))


def plan(cfg, base, high=False, env=None):
    cards = cards_ready(base, env, cfg)
    mode = effective_mode(cfg, high, cards)
    fallback = fell_back(cfg, high, cards)
    # 确认码模式也留记号。这样误点旧卡片时能对上这次操作并拒绝，而不是执行。
    token = new_token()
    return {"mode": mode, "fallback": fallback, "token": token, "ttl": ttl_for(cfg, mode, fallback), "cards": cards}


def new_token():
    return secrets.token_hex(16)


def action_hash(tool, steps, user, chat, scope, extra=""):
    blob = {
        "tool": str(tool or ""),
        "steps": steps if isinstance(steps, list) else [],
        "user": str(user or ""),
        "chat": str(chat or ""),
        "scope": str(scope or ""),
        "extra": str(extra or ""),
    }
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def impact(name, notify=False):
    name = str(name or "")
    if name.startswith("calendar"):
        tail = "成功后的回复会提醒所有人。" if notify else "不邀请任何人，也不另外通知参会人。"
        return tail
    if name == "docs_create":
        return "会在专用文件夹里多一份新文档。已有文档不动。"
    if name == "docs_append":
        return "只在允许追加的文档末尾加一段，不覆盖原来的正文。"
    if name == "base_create":
        return "会在团队表格里多一条记录。"
    if name == "memory_propose":
        return "只写进这个群的记忆。别的群看不到。"
    if name in ("feature_switch", "restart"):
        return "会改一项功能开关，然后重新启动。这中间可能暂时不回消息。"
    if name == "confirm_mode":
        return "只改以后怎么确认。确认本身不会关掉。改完会重新启动。"
    return "只做上面这一件事。"


def _when(ttl_sec):
    try:
        num = int(ttl_sec)
    except (TypeError, ValueError):
        num = 600
    if num >= 60:
        return "%d 分钟" % (num // 60)
    return "%d 秒" % max(1, num)


def button_text(summary, impact_text, mode, code, ttl_sec):
    lines = [
        "请看清楚再确认。",
        "要做的事：%s" % summary,
        "影响谁：%s" % impact_text,
        "只有发起的人本人可以操作。%s内有效，用一次就作废。" % _when(ttl_sec),
    ]
    if mode == "button_or_code":
        lines.append("可以点卡片上的「确认」，也可以回复：确认 %s" % code)
        lines.append("不想做就点「取消」，或回复：取消 %s" % code)
    elif mode == "button_and_code":
        lines.append("这件两样都要：点「确认」，并且回复：确认 %s" % code)
        lines.append("顺序不限。不想做就点「取消」，或回复：取消 %s" % code)
    else:
        lines.append("请点下面的「确认」或「取消」。")
    return "\n".join(lines)


def build_card(text, token, action_hash_value):
    """按钮上只带一次性记号和动作哈希。真正要做什么记在本机，不放进卡片。"""
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "summary": {"content": "请确认"}},
        "header": {"title": {"tag": "plain_text", "content": "请确认"}, "template": "orange"},
        "body": {"elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": text}},
            _button("确认", "primary", token, "yes", action_hash_value),
            _button("取消", "danger", token, "no", action_hash_value),
        ]},
    }


def _button(label, color, token, op, action_hash_value):
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": color,
        "behaviors": [{"type": "callback", "value": {
            "kind": "write_confirm",
            "id": str(token or ""),
            "op": op,
            "hash": str(action_hash_value or ""),
        }}],
    }


def done_card(title, detail):
    tone = "green" if title == "已确认" else "grey"
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "summary": {"content": title}},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": tone},
        "body": {"elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": str(detail or title)[:500]}},
        ]},
    }


def parse_click(obj):
    """只认写入确认按钮。功能清单上的按钮返回 None，交给原来的处理。"""
    if not isinstance(obj, dict):
        return None
    header = obj.get("header") if isinstance(obj.get("header"), dict) else {}
    kind = str(header.get("event_type") or obj.get("event_type") or "")
    if "card.action" not in kind:
        return None
    event = obj.get("event") if isinstance(obj.get("event"), dict) else {}
    if not event and isinstance(obj.get("action"), dict):
        event = obj
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    if str(value.get("kind") or "") != "write_confirm":
        return None
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    op = str(value.get("op") or "")
    scope = str(context.get("thread_id") or context.get("open_thread_id") or "")
    return {
        "token": str(value.get("id") or ""),
        "op": "no" if op in ("no", "cancel") else "yes",
        "hash": str(value.get("hash") or ""),
        "user": str(operator.get("open_id") or ""),
        "chat_id": str(context.get("open_chat_id") or ""),
        "message_id": str(context.get("open_message_id") or ""),
        "event_id": str(header.get("event_id") or obj.get("event_id") or ""),
        "scope": scope,
    }


def scrub_audit(kw):
    """审计里不留确认码、按钮记号和人的编号。用量统计仍用调用方手里的原编号。"""
    out = dict(kw or {})
    code = str(out.pop("code", "") or "")
    token = str(out.pop("token", "") or "")
    if code:
        out["code_hash"] = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
    if token:
        out["token_hash"] = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    for key in ("user", "chat"):
        val = str(out.get(key) or "")
        if val:
            out[key] = hashlib.sha256(val.encode("utf-8")).hexdigest()[:8]
    for key in ("args", "summary", "detail", "err", "result"):
        if not isinstance(out.get(key), str):
            continue
        text = out[key]
        if code and code in text:
            text = text.replace(code, "[确认码]")
        if token and len(token) >= 8 and token in text:
            text = text.replace(token, "[确认]")
        out[key] = text
    return out


def _events_in(text):
    raw = text or ""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    for key in ("events", "subscribed_events", "event_keys", "subscriptions"):
        val = obj.get(key)
        if isinstance(val, list):
            return [str(item) for item in val]
        inner = obj.get("data")
        if isinstance(inner, dict) and isinstance(inner.get(key), list):
            return [str(item) for item in inner.get(key)]
    return None


def doctor_card(text):
    """返回 (级别, 说明)。看不出订阅时是 WARN，不因此失败。"""
    found = _events_in(text)
    if found is not None and any("card.action" in item for item in found):
        return "OK", "已能收到卡片回调"
    if found is not None:
        return "WARN", "还没有卡片回调。" + CARD_STEPS
    return "WARN", "看不出有没有开通卡片回调。" + CARD_STEPS


def plain_mode(mode):
    return {
        "button": "点按钮",
        "code": "确认码",
        "button_or_code": "点按钮或确认码",
        "button_and_code": "点按钮并且回复确认码",
    }.get(mode, "点按钮")


def mode_help(cfg, base, env=None):
    chosen = configured_mode(cfg)
    cards = cards_ready(base, env, cfg)
    live = effective_mode(cfg, False, cards)
    lines = [
        "确认不能关掉。可以换下面四种里的一种。",
        "你选的是：%s。" % plain_mode(chosen),
        "现在实际用的是：%s。" % plain_mode(live),
    ]
    if chosen != "code" and not cards:
        lines.append("飞书还没准备好点按钮，所以先用确认码。开通之后会自己改回点按钮。")
    lines.append("回复其中一句就能换：")
    lines.append("确认方式 点按钮")
    lines.append("确认方式 确认码")
    lines.append("确认方式 都可以")
    lines.append("确认方式 都要")
    lines.append("「都要」表示点按钮和回复确认码两样都要。换完会重新启动。")
    return "\n".join(lines)


def mode_choice(text):
    """从一句人话里认出要换成哪种。认不出返回空字符串。"""
    raw = str(text or "").strip()
    raw = re.sub(r"^(确认方式|确认改成|换成|改成)\s*", "", raw).strip()
    if not raw or raw in ("确认方式", "怎么确认"):
        return ""
    if raw in ("都可以", "按钮或确认码", "或者", "都行"):
        return "button_or_code"
    if raw in ("都要", "两样", "两样都要", "按钮和确认码", "并且"):
        return "button_and_code"
    if raw in ("点按钮", "按钮", "用按钮", "按钮确认"):
        return "button"
    if raw in ("确认码", "验证码", "数字", "用确认码"):
        return "code"
    return ""


def asking_mode(text):
    q = str(text or "").strip()
    if q in ("确认方式", "怎么确认", "换确认方式", "看看确认方式"):
        return True
    return bool(mode_choice(q)) and bool(re.match(r"^(确认方式|确认改成|换成|改成)\b", q))


def refusing_off(text):
    """只认短指令。普通聊天里提到确认，不拦下来。"""
    q = re.sub(r"\s+", "", str(text or "").strip())
    if not q or len(q) > 16:
        return False
    off = r"(关掉|关闭|取消|不要|不用了?)"
    what = r"(确认方式|确认码|确认)"
    if re.fullmatch(r"(请)?(把)?" + what + off + r"(吧|了)?", q):
        return True
    if re.fullmatch(r"(请)?" + off + what + r"(吧|了)?", q):
        return True
    return False


def menu_line(cfg, base, env=None):
    live = effective_mode(cfg, False, cards_ready(base, env, cfg))
    return "确认方式现在是%s。回复「确认方式」可以更换。确认不能关掉。" % plain_mode(live)


def write_mode(data, mode):
    """只改 confirm.mode。不接受关掉。其它键不动。"""
    if mode not in MODES:
        raise ValueError("bad mode")
    import copy
    data = copy.deepcopy(data if isinstance(data, dict) else {})
    block = data.get("confirm")
    if not isinstance(block, dict):
        block = {}
    block["mode"] = mode
    block.pop("enabled", None)
    data["confirm"] = block
    return data
