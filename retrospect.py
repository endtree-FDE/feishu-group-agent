# 复盘写回：一轮群聊答完后，把「做了什么、哪里出错、下次怎么做」写成一条记忆提案。
# 默认关。不另调模型，不另开收消息进程。文字由刚结束的这一轮步骤拼出来。
# 只走现有的记忆成长审批：审批人回复「确认记忆」之后，才写入这个群的事实。没点头不写。
import datetime, re, time
import guard, memory, memory_growth

_END = re.compile(r"做完了|完成了|结束")
_FEISHU_ID = re.compile(r"(?i)\b(?:ou|on|oc|om|cli)_[A-Za-z0-9]{4,}\b")
_UNIX = re.compile(r"(?:/home/|/Users/|/root/)[^\s，。；、：）)》」\"'`]*")


def _memory(cfg):
    raw = (cfg or {}).get("memory")
    return raw if isinstance(raw, dict) else {}


def _raw(cfg):
    raw = _memory(cfg).get("retrospect")
    return raw if isinstance(raw, dict) else {}


def enabled(cfg):
    """缺省关。长期记忆和复盘都打开才跑。只开其中一项时不写提案。"""
    return bool(_memory(cfg).get("enabled")) and bool(_raw(cfg).get("enabled"))


def settings(cfg):
    opt = memory_growth.settings(cfg)
    return {
        "enabled": enabled(cfg),
        "approvers": opt["approvers"],
        "deliver": opt["deliver"],
        "confirm_ttl_sec": opt["confirm_ttl_sec"],
        "max_fact": opt["max_fact"],
    }


def worth(row):
    """闲聊不写。用过工具或技能，或者有人说做完了，才值得复盘。"""
    if not isinstance(row, dict):
        return False
    steps = row.get("steps") if isinstance(row.get("steps"), list) else []
    for step in steps:
        if isinstance(step, dict) and step.get("kind") in ("tool", "skill"):
            return True
    blob = "\n".join([
        str(step.get("text") or "") for step in steps if isinstance(step, dict) and step.get("kind") == "receive"
    ] + [str(row.get("result") or "")])
    return bool(_END.search(blob))


def draft(row, names, limit=300):
    """拼一句短复盘。客户名先换成「某客户」，再截到记忆能放下的长度。"""
    skills = []
    for name in row.get("skills") or []:
        text = str(name or "").strip()
        if text and text not in skills:
            skills.append(text)
    ok_tools, bad_tools = [], []
    for step in row.get("steps") or []:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        name = str(step.get("name") or "工具")
        if step.get("ok", True):
            if name not in ok_tools:
                ok_tools.append(name)
        elif name not in bad_tools:
            bad_tools.append(name)
    did = []
    if skills:
        did.append("技能 " + "、".join(skills[:4]))
    if ok_tools:
        did.append("工具 " + "、".join(ok_tools[:4]) + " 成功")
    if not did:
        did.append("按对话收了尾")
    if bad_tools:
        wrong = "、".join(bad_tools[:4]) + " 失败"
        nxt = "失败的步骤先核对再重试，不要重复同一条错路。"
    elif not row.get("ok"):
        wrong = "这轮没答成"
        nxt = "失败的步骤先核对再重试，不要重复同一条错路。"
    else:
        wrong = "没有失败的步骤"
        nxt = "同样的事可以沿用这次的步骤。"
    result = str(row.get("result") or "").replace("\n", " ").strip()
    text = "复盘。做了什么：%s。哪里出错：%s。下次怎么做：%s" % ("；".join(did), wrong, nxt)
    if result:
        text += "。结果：" + result[:80]
    return _clean(text, names, limit)


def _clean(text, names, limit):
    text = guard.redact(text or "", names, placeholder="某客户")
    text = _FEISHU_ID.sub("[编号已隐藏]", text)
    text = _UNIX.sub("[本地路径已隐藏]", text)
    text = guard.neutralize(re.sub(r"\s+", " ", text).strip())
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 300
    if limit < 2:
        limit = 300
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _pending(base, chat_id, now):
    """同一个群已经有一条还没处理的复盘时，不再叠一条。"""
    book = memory_growth._load_book(base)
    for rec in (book.get("codes") or {}).values():
        if not isinstance(rec, dict) or rec.get("used"):
            continue
        try:
            exp = float(rec.get("expires_at") or 0)
        except (TypeError, ValueError):
            continue
        if exp < now:
            continue
        if str(rec.get("chat_id") or "") != chat_id:
            continue
        src = (rec.get("payload") or {}).get("source") or {}
        if src.get("kind") == "retrospect":
            return True
    return False


def render_notice(item, ttl_sec):
    minutes = max(1, int(ttl_sec) // 60)
    body = (item.get("content") or "").replace("\n", " ")
    code = item.get("code") or ""
    return "\n".join([
        "这一轮做完了，有一条复盘还没写入这个群的记忆。",
        body,
        "审批人请在 %d 分钟内回复「确认记忆 %s」。" % (minutes, code),
        "不想记就回复「驳回记忆 %s」。过期、不回复或别人回复，都不会写入。" % code,
        "别的群看不到这一条。",
    ])


def consider(cfg, base, chat_id, chat_type, row, names=(), sender=None, log=None, now=None):
    """该写就发出确认码。这一步不改事实。私聊、没审批人、闲聊都直接返回空。"""
    if not enabled(cfg) or not worth(row):
        return []
    if chat_type == "p2p":
        return []
    chat_id = str(chat_id or "")
    if not memory._CHAT_RX.match(chat_id) or chat_id.startswith("REPLACE_"):
        return []
    opt = memory_growth.settings(cfg)
    if not opt["approvers"]:
        return []
    now = time.time() if now is None else float(now)
    if _pending(base, chat_id, now):
        return []
    text = draft(row, names, opt["max_fact"])
    rules = guard.policy_rules(cfg, "memory")
    if len(text) < 2 or guard.embeds_secret(text, names) or guard.policy_hit(text, rules):
        return []
    try:
        epoch = float((row or {}).get("epoch") or now)
    except (TypeError, ValueError):
        epoch = now
    day = datetime.date.fromtimestamp(epoch).isoformat()
    proposal = {
        "op": "add",
        "type": "note",
        "content": text,
        "confidence": 0.8,
        "source": {"msgs": [], "day": day, "kind": "retrospect"},
    }
    issued = memory_growth.issue(base, chat_id, [proposal], opt, now)
    if issued and sender:
        notice = guard.neutralize(guard.redact(render_notice(issued[0], opt["confirm_ttl_sec"]), names))
        try:
            memory_growth._send(sender, opt, chat_id, notice, now)
        except Exception:
            if log:
                log.warning("retrospect notice failed")
    return issued
