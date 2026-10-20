# 用量周报。默认关。到了配置的星期和时间，把上一周的合计私聊给主人，每周只发一次。
# 统计只读本机 logs/usage.jsonl。里面没有问题原文、人名、open_id 或群编号。
# 演练和补答会做标记，周报不把它们算进真实用量。
import csv, datetime, hashlib, io, json, os, re, threading, time
import guard


def _digest():
    """用到时再导入，避免 tools → usage → digest → outbound → tools 的环。"""
    import digest
    return digest


def _outbound():
    import outbound
    return outbound

_TOPIC_RULES = (
    ("帮助", ("/help", "/状态", "/status", "帮助", "你是谁", "怎么用")),
    ("记忆", ("记一下", "记住", "备忘", "/记忆", "/忘记")),
    ("日历", ("日历", "日程", "会议", "约会")),
    ("表格", ("多维表格", "数据表", "表格")),
    ("文档", ("云文档", "知识库", "文档")),
    ("文件", ("附件", "图片", "pdf", "word", "excel", "ppt", "文件")),
    ("搜索", ("搜索", "查一下", "上网", "网页", "新闻")),
    ("写入", ("新建", "创建", "追加", "写到", "加一条")),
)
_TOPICS = tuple(label for label, _keys in _TOPIC_RULES) + ("其他",)
_TOPIC_ID = {
    "帮助": "help", "记忆": "memory", "日历": "calendar", "表格": "base",
    "文档": "docs", "文件": "file", "搜索": "search", "写入": "write", "其他": "other",
}
_WEEKDAYS = {
    "mon": 0, "monday": 0, "周一": 0, "星期一": 0,
    "tue": 1, "tuesday": 1, "周二": 1, "星期二": 1,
    "wed": 2, "wednesday": 2, "周三": 2, "星期三": 2,
    "thu": 3, "thursday": 3, "周四": 3, "星期四": 3,
    "fri": 4, "friday": 4, "周五": 4, "星期五": 4,
    "sat": 5, "saturday": 5, "周六": 5, "星期六": 5,
    "sun": 6, "sunday": 6, "周日": 6, "星期日": 6, "星期天": 6,
}
_TOOLS = ("web_search", "web_fetch", "file", "docs", "calendar", "base", "other")
_READ_BUCKET = {
    "docs_read": "docs", "docs_search": "docs",
    "calendar_read": "calendar", "base_read": "base",
    "kb_read": "file", "attachment": "file", "message_attachment": "file",
}
_WRITE_ACTION = {
    "write_proposed": "proposed", "write_done": "confirmed",
    "write_cancelled": "cancelled", "write_expired": "expired",
}
_WRITE_ORDER = ("proposed", "confirmed", "cancelled", "expired")
_WRITE_ZH = {"proposed": "提议", "confirmed": "确认", "cancelled": "取消", "expired": "过期"}
_TOOL_ZH = {
    "web_search": "网页搜索", "web_fetch": "打开网页", "file": "读文件",
    "docs": "文档", "calendar": "日历", "base": "多维表格", "other": "其他",
}
_FILE_TYPE = re.compile(r"^[a-z0-9]{1,8}$")
_LABEL = re.compile(r"^g\d{1,6}$")
_LOCK = threading.Lock()
_CTX = threading.local()
_BASE = ""
_TEST = False


def configure(base, test=False):
    """告诉计数器日志写到哪。没调用过时，所有 note 都直接返回，不创建文件。"""
    global _BASE, _TEST
    _BASE = base or ""
    _TEST = bool(test)


def test_mode():
    return bool(_TEST)


def traffic_for(replay=False):
    if replay:
        return "replay"
    if _TEST:
        return "test"
    return "live"


def bind(chat_id="", scope="group", traffic=""):
    _CTX.chat_id = str(chat_id or "")
    _CTX.scope = "p2p" if scope == "p2p" else "group"
    _CTX.traffic = traffic if traffic in ("live", "test", "replay") else ""


def unbind():
    _CTX.chat_id = ""
    _CTX.scope = ""
    _CTX.traffic = ""


def current_traffic():
    local = getattr(_CTX, "traffic", "") or ""
    if local in ("live", "test", "replay"):
        return local
    if _TEST:
        return "test"
    return "live"


def has_chat():
    return bool(getattr(_CTX, "chat_id", ""))


def safe_file_type(name):
    """只留扩展名。没有扩展名、或扩展名不像类型时，返回 unknown。不返回文件名。"""
    base = str(name or "").replace("\\", "/").split("/")[-1].strip().lower()
    if not base or "." not in base:
        return "unknown"
    ext = base.rsplit(".", 1)[-1]
    if _FILE_TYPE.match(ext):
        return ext
    return "unknown"


def topic_of(text):
    """粗分类。只返回固定类别，不返回原文。"""
    raw = (text or "").casefold()
    for label, keys in _TOPIC_RULES:
        for key in keys:
            if key and key.casefold() in raw:
                return label
    return "其他"


def _num(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return number


def _clean_id(value):
    text = str(value or "").strip()
    if not text or text.startswith("REPLACE_") or not _outbound()._ID.match(text):
        return ""
    return text


def _weekday(value):
    if isinstance(value, bool) or value is None or value == "":
        return 0, True
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 6:
            return value, True
        return 0, False
    text = str(value).strip().lower()
    if text in _WEEKDAYS:
        return _WEEKDAYS[text], True
    return 0, False


def enabled(cfg):
    raw = (cfg or {}).get("report")
    return isinstance(raw, dict) and bool(raw.get("enabled"))


def settings(cfg):
    raw = (cfg or {}).get("report")
    if not isinstance(raw, dict):
        raw = {}
    weekday, weekday_ok = _weekday(raw.get("weekday", "mon"))
    if "weekday" not in raw:
        weekday, weekday_ok = 0, True
    hh, mm = 9, 0
    text = str(raw.get("time") or "09:00").strip()
    matched = re.match(r"^(\d{1,2}):(\d{2})$", text)
    ok_time = False
    if matched:
        hh, mm = int(matched.group(1)), int(matched.group(2))
        ok_time = 0 <= hh <= 23 and 0 <= mm <= 59
    if not ok_time:
        hh, mm = 9, 0
    window = raw.get("window_min", 180)
    if window is None or window == "":
        window = 180
    else:
        window = _num(window, 180)
    owners = []
    for item in raw.get("owner_open_ids") or []:
        oid = _clean_id(item)
        if oid and oid not in owners:
            owners.append(oid)
    return {
        "enabled": bool(raw.get("enabled")),
        "owners": owners,
        "weekday": weekday,
        "weekday_ok": weekday_ok,
        "hour": hh,
        "minute": mm,
        "time_ok": ok_time or "time" not in raw,
        "window": window,
        "timezone": str(raw.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
    }


def owner_ids(cfg):
    """report.owner_open_ids 有可用编号时用它，否则用 alerts.owner_open_ids。不要求 alerts 已打开。"""
    spec = settings(cfg)
    if spec["owners"]:
        return list(spec["owners"])
    raw = (cfg or {}).get("alerts")
    found = []
    if isinstance(raw, dict):
        for item in raw.get("owner_open_ids") or []:
            oid = _clean_id(item)
            if oid and oid not in found:
                found.append(oid)
    return found


def is_owner(cfg, user):
    uid = str(user or "")
    return bool(uid) and uid in owner_ids(cfg)


def _state_path(base):
    return os.path.join(base, "state", "usage_groups.json")


def _report_state(base):
    return os.path.join(base, "state", "report_state.json")


def _log_path(base):
    return os.path.join(base, "logs", "usage.jsonl")


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _gid(chat_id):
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]


def _known_scope(chat_id):
    if not _BASE or not chat_id:
        return ""
    data = _load(_state_path(_BASE))
    cur = (data.get("items") or {}).get(_gid(chat_id))
    if isinstance(cur, dict) and cur.get("scope") in ("group", "p2p"):
        return cur["scope"]
    return ""


def group_of(chat_id, scope="group"):
    """稳定的匿名标签。状态文件里只存聊天编号的哈希，周报里只出现 g1 这种标签。"""
    if not _BASE or not chat_id:
        return "", "group"
    scope = "p2p" if scope == "p2p" else "group"
    path = _state_path(_BASE)
    key = _gid(chat_id)
    with _digest().outbound_lock(path):
        data = _load(path)
        items = data.setdefault("items", {})
        cur = items.get(key)
        if isinstance(cur, dict) and cur.get("n"):
            kept = cur.get("scope") if cur.get("scope") in ("group", "p2p") else scope
            return "g%d" % int(cur["n"]), kept
        try:
            n = int(data.get("next") or 1)
        except (TypeError, ValueError):
            n = 1
        if n < 1:
            n = 1
        items[key] = {"n": n, "scope": scope}
        data["next"] = n + 1
        _save(path, data)
        return "g%d" % n, scope


def _scope_for(chat_id, scope):
    if scope in ("group", "p2p"):
        return scope
    ctx_chat = getattr(_CTX, "chat_id", "") or ""
    if chat_id and ctx_chat == str(chat_id):
        ctx_scope = getattr(_CTX, "scope", "") or ""
        if ctx_scope in ("group", "p2p"):
            return ctx_scope
    return _known_scope(chat_id) or "group"


def _scrub(row):
    kind = row.get("kind")
    if kind not in ("turn", "tool", "write", "redact", "error", "health", "link"):
        return None
    out = {"t": float(row.get("t") or time.time()), "kind": kind}
    traffic = row.get("traffic")
    out["traffic"] = traffic if traffic in ("live", "test", "replay") else "live"
    label = str(row.get("group") or "")
    if _LABEL.match(label):
        out["group"] = label
        scope = row.get("scope")
        out["scope"] = scope if scope in ("group", "p2p") else "group"
    if kind == "turn":
        out["ok"] = 1 if row.get("ok") else 0
        out["partial"] = 1 if row.get("partial") else 0
        if row.get("elapsed_ms") is not None:
            try:
                out["elapsed_ms"] = max(0, int(row.get("elapsed_ms")))
            except (TypeError, ValueError):
                pass
        topic = row.get("topic")
        out["topic"] = topic if topic in _TOPICS else "其他"
        try:
            out["redact_hits"] = max(0, int(row.get("redact_hits") or 0))
        except (TypeError, ValueError):
            out["redact_hits"] = 0
    elif kind == "tool":
        tool = row.get("tool")
        out["tool"] = tool if tool in _TOOLS else "other"
        if out["tool"] == "file":
            out["file_type"] = safe_file_type(row.get("file_type")) if "." in str(row.get("file_type") or "") else (
                str(row.get("file_type")) if _FILE_TYPE.match(str(row.get("file_type") or "")) else "unknown")
    elif kind == "write":
        action = row.get("action")
        if action not in _WRITE_ORDER:
            return None
        out["action"] = action
    elif kind == "redact":
        try:
            hits = int(row.get("hits") or 0)
        except (TypeError, ValueError):
            return None
        if hits <= 0:
            return None
        out["hits"] = hits
    elif kind == "error":
        category = row.get("category")
        if category not in ("model", "timeout", "permission"):
            return None
        out["category"] = category
    elif kind == "health":
        action = row.get("action")
        if action not in ("down", "remind", "recover"):
            return None
        out["action"] = action
    elif kind == "link":
        event = row.get("event")
        if event not in ("down", "up"):
            return None
        out["event"] = event
        if event == "up":
            try:
                out["downtime_sec"] = max(0, int(row.get("downtime_sec") or 0))
            except (TypeError, ValueError):
                out["downtime_sec"] = 0
    return out


def _rotate(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < 4_000_000:
        return
    try:
        os.replace(path, path + ".1")
    except OSError:
        return


def _append(row):
    if not _BASE:
        return
    safe = _scrub(row)
    if not safe:
        return
    path = _log_path(_BASE)
    line = json.dumps(safe, ensure_ascii=False)
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _placed(chat_id, scope):
    if chat_id is None:
        chat_id = getattr(_CTX, "chat_id", "") or ""
        scope = getattr(_CTX, "scope", "") or "group"
    label, kept = group_of(chat_id, _scope_for(chat_id, scope))
    return label, kept


def note_turn(chat_id, scope, ok, elapsed_ms=None, topic="其他", redact_hits=0, partial=False):
    label, kept = _placed(chat_id, scope)
    row = {"kind": "turn", "traffic": current_traffic(), "group": label, "scope": kept,
           "ok": 1 if ok else 0, "partial": 1 if partial else 0, "topic": topic,
           "redact_hits": redact_hits}
    if elapsed_ms is not None:
        row["elapsed_ms"] = elapsed_ms
    _append(row)


def note_tool(bucket, file_type="", chat_id=None, scope=None):
    label, kept = _placed(chat_id, scope)
    row = {"kind": "tool", "traffic": current_traffic(), "group": label, "scope": kept, "tool": bucket}
    if bucket == "file":
        row["file_type"] = file_type or "unknown"
    _append(row)


def note_write(action, chat_id=None, scope=None):
    label, kept = _placed(chat_id, scope)
    _append({"kind": "write", "traffic": current_traffic(), "group": label, "scope": kept, "action": action})


def note_redact(hits, chat_id=None, scope=None):
    label, kept = _placed(chat_id, scope)
    _append({"kind": "redact", "traffic": current_traffic(), "group": label, "scope": kept, "hits": hits})


def note_error(category, chat_id=None, scope=None):
    label, kept = _placed(chat_id, scope)
    _append({"kind": "error", "traffic": current_traffic(), "group": label, "scope": kept, "category": category})


def note_health(action):
    _append({"kind": "health", "traffic": current_traffic(), "action": action})


def note_link(event, downtime_sec=0, when=None):
    row = {"kind": "link", "traffic": current_traffic(), "event": event, "t": float(when if when is not None else time.time())}
    if event == "up":
        row["downtime_sec"] = downtime_sec
    _append(row)


def note_audit(kind, tool, file_types=None, redact_hits=0, chat_id="", scope=None, count_tool=True):
    if kind in _WRITE_ACTION:
        note_write(_WRITE_ACTION[kind], chat_id=chat_id or None, scope=scope)
    if redact_hits:
        note_redact(redact_hits, chat_id=chat_id or None, scope=scope)
    if not count_tool:
        return
    bucket = _READ_BUCKET.get(tool or "")
    if not bucket:
        return
    if bucket == "file":
        types = [item for item in (file_types or []) if item]
        for item in types:
            note_tool("file", item, chat_id=chat_id or None, scope=scope)
        return
    note_tool(bucket, chat_id=chat_id or None, scope=scope)


def make_link_handler():
    """第一次连上不算恢复。只有先记过断开，再连上，才记下中断时长。"""
    state = {"down": None}

    def on_link(up, prev_up, prev_since, now):
        if prev_up and not up:
            state["down"] = now
            note_link("down", when=now)
        elif (not prev_up) and up and state["down"] is not None:
            try:
                span = max(0, float(now) - float(prev_since or now))
            except (TypeError, ValueError):
                span = 0
            note_link("up", downtime_sec=span, when=now)
            state["down"] = None

    return on_link


def read_rows(base):
    rows = []
    for name in ("usage.jsonl.1", "usage.jsonl"):
        path = os.path.join(base, "logs", name)
        try:
            handle = open(path, encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(raw, dict):
                    continue
                safe = _scrub(raw)
                if safe:
                    rows.append(safe)
    rows.sort(key=lambda item: item.get("t") or 0)
    return rows


def _blank():
    return {
        "questions": 0, "answers": 0, "elapsed": [], "redact": 0,
        "tools": {name: 0 for name in _TOOLS},
        "files": {},
        "writes": {name: 0 for name in _WRITE_ORDER},
        "errors": {"model": 0, "timeout": 0, "permission": 0},
        "topics": {name: 0 for name in _TOPICS},
        "health": {"down": 0, "remind": 0, "recover": 0},
    }


def _add_turn(bucket, row):
    bucket["questions"] += 1
    if row.get("ok") and not row.get("partial"):
        bucket["answers"] += 1
    if "elapsed_ms" in row:
        bucket["elapsed"].append(row["elapsed_ms"])
    bucket["redact"] += int(row.get("redact_hits") or 0)
    topic = row.get("topic") if row.get("topic") in _TOPICS else "其他"
    bucket["topics"][topic] += 1


def _apply(bucket, row):
    kind = row.get("kind")
    if kind == "turn":
        _add_turn(bucket, row)
    elif kind == "tool":
        tool = row.get("tool") if row.get("tool") in _TOOLS else "other"
        bucket["tools"][tool] += 1
        if tool == "file":
            ft = row.get("file_type") or "unknown"
            bucket["files"][ft] = bucket["files"].get(ft, 0) + 1
    elif kind == "write":
        action = row.get("action")
        if action in bucket["writes"]:
            bucket["writes"][action] += 1
    elif kind == "redact":
        bucket["redact"] += int(row.get("hits") or 0)
    elif kind == "error":
        category = row.get("category")
        if category in bucket["errors"]:
            bucket["errors"][category] += 1
    elif kind == "health":
        action = row.get("action")
        if action in bucket["health"]:
            bucket["health"][action] += 1


def _link_stats(events, start, end):
    open_down = None
    drops = 0
    downtime = 0.0
    for row in events:
        t = float(row.get("t") or 0)
        event = row.get("event")
        if t < start:
            if event == "down":
                open_down = t
            elif event == "up":
                open_down = None
            continue
        if t >= end:
            break
        if event == "down":
            if open_down is None:
                drops += 1
                open_down = t
        elif event == "up" and open_down is not None:
            downtime += t - max(open_down, start)
            open_down = None
    if open_down is not None:
        downtime += end - max(open_down, start)
    return drops, int(round(downtime))


def median(values):
    xs = sorted(v for v in values if v is not None)
    n = len(xs)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def percentile(values, p):
    """最近秩。p90 在 10 条里取第 9 条（从 1 数）。"""
    xs = sorted(v for v in values if v is not None)
    n = len(xs)
    if not n:
        return None
    rank = int((p / 100.0) * n + 0.999999) - 1
    if rank < 0:
        rank = 0
    if rank >= n:
        rank = n - 1
    return float(xs[rank])


def _pair(cur, prev):
    return {"cur": cur, "prev": prev}


def _countable(row):
    """只收已经洗过的行。群标签必须是 g1 这种形式，坏行整行丢掉。"""
    kind = row.get("kind")
    if kind not in ("turn", "tool", "write", "redact", "error", "health", "link"):
        return False
    if kind in ("health", "link"):
        return True
    return bool(_LABEL.match(str(row.get("group") or "")))


def _snapshot(rows, start, end, link_rows):
    total = _blank()
    groups = {}
    for row in rows:
        t = float(row.get("t") or 0)
        if t < start or t >= end:
            continue
        if not _countable(row):
            continue
        if row.get("traffic") in ("test", "replay"):
            continue
        if row.get("kind") == "link":
            continue
        _apply(total, row)
        if row.get("kind") == "health":
            continue
        label = row.get("group") or ""
        bucket = groups.get(label)
        if bucket is None:
            bucket = _blank()
            bucket["scope"] = row.get("scope") or "group"
            groups[label] = bucket
        _apply(bucket, row)
    drops, down = _link_stats(link_rows, start, end)
    return total, groups, drops, down


def _materialize(bucket, drops, downtime):
    files = {name: bucket["files"].get(name, 0) for name in bucket["files"]}
    return {
        "questions": bucket["questions"],
        "answers": bucket["answers"],
        "elapsed": list(bucket["elapsed"]),
        "p50": median(bucket["elapsed"]),
        "p90": percentile(bucket["elapsed"], 90),
        "redact": bucket["redact"],
        "tools": dict(bucket["tools"]),
        "files": files,
        "writes": dict(bucket["writes"]),
        "errors": dict(bucket["errors"]),
        "topics": {name: bucket["topics"][name] for name in _TOPICS if bucket["topics"][name]},
        "health": dict(bucket["health"]),
        "drops": drops,
        "downtime": downtime,
        "scope": bucket.get("scope") or "group",
    }


def _view(cur, prev, title, group, scope, include_link):
    file_names = sorted(set(cur["files"]) | set(prev["files"]))
    topic_names = sorted(set(cur["topics"]) | set(prev["topics"]), key=lambda name: (-(cur["topics"].get(name, 0)), name))
    return {
        "title": title,
        "group": group,
        "scope": scope,
        "questions": _pair(cur["questions"], prev["questions"]),
        "answers": _pair(cur["answers"], prev["answers"]),
        "p50": _pair(cur["p50"], prev["p50"]),
        "p90": _pair(cur["p90"], prev["p90"]),
        "redact": _pair(cur["redact"], prev["redact"]),
        "tools": {name: _pair(cur["tools"].get(name, 0), prev["tools"].get(name, 0)) for name in _TOOLS},
        "files": {name: _pair(cur["files"].get(name, 0), prev["files"].get(name, 0)) for name in file_names},
        "writes": {name: _pair(cur["writes"].get(name, 0), prev["writes"].get(name, 0)) for name in _WRITE_ORDER},
        "errors": {name: _pair(cur["errors"].get(name, 0), prev["errors"].get(name, 0)) for name in ("model", "timeout", "permission")},
        "drops": _pair(cur["drops"], prev["drops"]) if include_link else None,
        "downtime": _pair(cur["downtime"], prev["downtime"]) if include_link else None,
        "health": {name: _pair(cur["health"].get(name, 0), prev["health"].get(name, 0)) for name in ("down", "remind", "recover")} if include_link else None,
        "topics": [(name, _pair(cur["topics"].get(name, 0), prev["topics"].get(name, 0))) for name in topic_names],
    }


def _group_title(label, scope):
    number = label[1:] if label.startswith("g") and label[1:].isdigit() else label
    if scope == "p2p":
        return "私聊 %s" % number
    return "群 %s" % number


def _skipped(rows, start, end):
    test = replay = 0
    for row in rows:
        t = float(row.get("t") or 0)
        if t < start or t >= end or row.get("kind") != "turn":
            continue
        if row.get("traffic") == "test":
            test += 1
        elif row.get("traffic") == "replay":
            replay += 1
    return test, replay


def collect(base, start, end):
    rows = read_rows(base)
    live_links = [row for row in rows if row.get("kind") == "link" and row.get("traffic") not in ("test", "replay")]
    prev_start = start - 7 * 86400
    prev_end = end - 7 * 86400
    total, groups, drops, down = _snapshot(rows, start, end, live_links)
    prev_total, prev_groups, prev_drops, prev_down = _snapshot(rows, prev_start, prev_end, live_links)
    cur_total = _materialize(total, drops, down)
    old_total = _materialize(prev_total, prev_drops, prev_down)
    sections = [_view(cur_total, old_total, "合计", "all", "total", True)]
    labels = sorted(set(groups) | set(prev_groups), key=lambda item: (int(item[1:]) if item[1:].isdigit() else 9999, item))
    for label in labels:
        cur_b = groups.get(label) or _blank()
        old_b = prev_groups.get(label) or _blank()
        scope = cur_b.get("scope") or old_b.get("scope") or "group"
        cur_m = _materialize(cur_b, 0, 0)
        old_m = _materialize(old_b, 0, 0)
        sections.append(_view(cur_m, old_m, _group_title(label, scope), label, scope, False))
    skipped_test, skipped_replay = _skipped(rows, start, end)
    return {"sections": sections, "skipped_test": skipped_test, "skipped_replay": skipped_replay,
            "start": start, "end": end}


def _fmt_delta(cur, prev, seconds=False):
    if cur is None or prev is None:
        return "—"
    if seconds:
        return "%+.1f 秒" % ((cur - prev) / 1000.0)
    return "%+d" % (cur - prev)


def _fmt_count(pair):
    return "%d" % pair["cur"], "%d" % pair["prev"], _fmt_delta(pair["cur"], pair["prev"])


def _fmt_time(pair):
    def one(value):
        if value is None:
            return "—"
        return "%.1f 秒" % (value / 1000.0)
    return one(pair["cur"]), one(pair["prev"]), _fmt_delta(pair["cur"], pair["prev"], seconds=True)


def _fmt_files(files):
    if not files:
        return "无"
    parts = []
    for name, pair in files.items():
        parts.append("%s %d（%s）" % (name, pair["cur"], _fmt_delta(pair["cur"], pair["prev"])))
    return "、".join(parts)


def _top_topics(topics, limit=5):
    ranked = [(name, pair) for name, pair in topics if pair["cur"] or pair["prev"]]
    ranked.sort(key=lambda item: (-item[1]["cur"], item[0]))
    return ranked[:limit]


def render_markdown(report, moment_label, tz_name, names=()):
    lines = ["用量周报（%s）" % moment_label, "时区：%s。对比的是再往前的 7 天。演练和补答没有算进来。" % tz_name]
    if report["skipped_test"] or report["skipped_replay"]:
        lines.append("未计入演练 %d 条、补答 %d 条。" % (report["skipped_test"], report["skipped_replay"]))
    lines.append("连接中断和健康提醒按整台机器人统计，只写在合计里。")
    for section in report["sections"]:
        lines.append("")
        lines.append("## %s" % section["title"])
        q = _fmt_count(section["questions"])
        a = _fmt_count(section["answers"])
        p50 = _fmt_time(section["p50"])
        p90 = _fmt_time(section["p90"])
        red = _fmt_count(section["redact"])
        lines.append("| 指标 | 本周 | 上周 | 变化 |")
        lines.append("| --- | --- | --- | --- |")
        lines.append("| 处理问题 | %s | %s | %s |" % q)
        lines.append("| 完成回答 | %s | %s | %s |" % a)
        lines.append("| 响应时间中位数 | %s | %s | %s |" % p50)
        lines.append("| 响应时间 P90 | %s | %s | %s |" % p90)
        lines.append("| 脱敏命中 | %s | %s | %s |" % red)
        lines.append("")
        lines.append("工具")
        for name in ("web_search", "docs", "calendar", "base"):
            pair = section["tools"][name]
            lines.append("- %s：%d（%s）" % (_TOOL_ZH[name], pair["cur"], _fmt_delta(pair["cur"], pair["prev"])))
        fetch = section["tools"]["web_fetch"]
        if fetch["cur"] or fetch["prev"]:
            lines.append("- %s：%d（%s）" % (_TOOL_ZH["web_fetch"], fetch["cur"], _fmt_delta(fetch["cur"], fetch["prev"])))
        lines.append("- 读文件：%s" % _fmt_files(section["files"]))
        other = section["tools"]["other"]
        if other["cur"] or other["prev"]:
            lines.append("- 其他工具：%d（%s）" % (other["cur"], _fmt_delta(other["cur"], other["prev"])))
        bits = []
        for name in _WRITE_ORDER:
            pair = section["writes"][name]
            bits.append("%s %d（%s）" % (_WRITE_ZH[name], pair["cur"], _fmt_delta(pair["cur"], pair["prev"])))
        lines.append("写入：" + "，".join(bits))
        err = section["errors"]
        lines.append("错误：模型 %d（%s），超时 %d（%s），权限 %d（%s）" % (
            err["model"]["cur"], _fmt_delta(err["model"]["cur"], err["model"]["prev"]),
            err["timeout"]["cur"], _fmt_delta(err["timeout"]["cur"], err["timeout"]["prev"]),
            err["permission"]["cur"], _fmt_delta(err["permission"]["cur"], err["permission"]["prev"]),
        ))
        if section["drops"] is not None:
            lines.append("连接中断：%d 次（%s），累计中断 %d 秒（%s）" % (
                section["drops"]["cur"], _fmt_delta(section["drops"]["cur"], section["drops"]["prev"]),
                section["downtime"]["cur"], _fmt_delta(section["downtime"]["cur"], section["downtime"]["prev"]),
            ))
            health = section["health"]
            lines.append("健康提醒：中断 %d（%s），仍未恢复 %d（%s），恢复 %d（%s）" % (
                health["down"]["cur"], _fmt_delta(health["down"]["cur"], health["down"]["prev"]),
                health["remind"]["cur"], _fmt_delta(health["remind"]["cur"], health["remind"]["prev"]),
                health["recover"]["cur"], _fmt_delta(health["recover"]["cur"], health["recover"]["prev"]),
            ))
        topics = _top_topics(section["topics"])
        if topics:
            shown = "、".join("%s %d" % (name, pair["cur"]) for name, pair in topics)
            lines.append("问题类别：" + shown)
        else:
            lines.append("问题类别：无")
    text = "\n".join(lines).strip() + "\n"
    return guard.redact(text, names)


def render_csv(report):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["scope", "group", "metric", "current", "previous", "delta"])

    def put(section, metric, cur, prev):
        if cur is None or prev is None:
            delta = ""
        elif isinstance(cur, float) or isinstance(prev, float):
            delta = "%.3f" % (float(cur) - float(prev))
        else:
            delta = "%d" % (int(cur) - int(prev))
        writer.writerow([section["scope"], section["group"], metric, "" if cur is None else cur, "" if prev is None else prev, delta])

    for section in report["sections"]:
        put(section, "questions", section["questions"]["cur"], section["questions"]["prev"])
        put(section, "answers", section["answers"]["cur"], section["answers"]["prev"])
        put(section, "latency_p50_ms", section["p50"]["cur"], section["p50"]["prev"])
        put(section, "latency_p90_ms", section["p90"]["cur"], section["p90"]["prev"])
        put(section, "redact_hits", section["redact"]["cur"], section["redact"]["prev"])
        for name in _TOOLS:
            if name == "file":
                continue
            pair = section["tools"][name]
            if name == "web_fetch" and not pair["cur"] and not pair["prev"]:
                continue
            put(section, "tool_" + name, pair["cur"], pair["prev"])
        for name, pair in section["files"].items():
            put(section, "tool_file_" + name, pair["cur"], pair["prev"])
        for name in _WRITE_ORDER:
            pair = section["writes"][name]
            put(section, "write_" + name, pair["cur"], pair["prev"])
        for name in ("model", "timeout", "permission"):
            pair = section["errors"][name]
            put(section, "error_" + name, pair["cur"], pair["prev"])
        if section["drops"] is not None:
            put(section, "error_connection_drops", section["drops"]["cur"], section["drops"]["prev"])
            put(section, "error_connection_downtime_sec", section["downtime"]["cur"], section["downtime"]["prev"])
            for name in ("down", "remind", "recover"):
                pair = section["health"][name]
                put(section, "health_" + name, pair["cur"], pair["prev"])
        for name, pair in _top_topics(section["topics"], limit=20):
            put(section, "topic_" + _TOPIC_ID.get(name, "other"), pair["cur"], pair["prev"])
    return buf.getvalue()


def _at(moment, weekday):
    """最近一个已经结束的统计周，以配置的星期 0 点为界。返回 (start, end)，end 不包含。"""
    days_since = (moment.weekday() - weekday) % 7
    end_day = moment.date() - datetime.timedelta(days=days_since)
    end = datetime.datetime.combine(end_day, datetime.time(), tzinfo=moment.tzinfo)
    if moment < end:
        end -= datetime.timedelta(days=7)
    start = end - datetime.timedelta(days=7)
    return start, end


def _stamp(moment):
    return moment.timestamp()


def period_for(cfg, now, week_text=""):
    spec = settings(cfg)
    tz = _digest().zone(spec["timezone"])
    if tz is None:
        return None, None, "timezone"
    text = (week_text or "").strip()
    if not text:
        moment = _digest().local_now(now, tz)
        if not spec["weekday_ok"]:
            return None, None, "weekday"
        start, end = _at(moment, spec["weekday"])
        return start, end, ""
    matched = re.fullmatch(r"(\d{4})-W(\d{2})", text)
    if matched:
        try:
            day = datetime.date.fromisocalendar(int(matched.group(1)), int(matched.group(2)), 1)
        except ValueError:
            return None, None, "week"
        start = datetime.datetime.combine(day, datetime.time(), tzinfo=tz)
        return start, start + datetime.timedelta(days=7), ""
    matched = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if matched:
        try:
            day = datetime.date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
        except ValueError:
            return None, None, "week"
        if not spec["weekday_ok"]:
            return None, None, "weekday"
        # 包含这一天的统计周：从当天所在的配置星期 0 点起，共 7 天。
        days_since = (day.weekday() - spec["weekday"]) % 7
        start_day = day - datetime.timedelta(days=days_since)
        start = datetime.datetime.combine(start_day, datetime.time(), tzinfo=tz)
        return start, start + datetime.timedelta(days=7), ""
    return None, None, "week"


def _label(start, end):
    last = end - datetime.timedelta(seconds=1)
    return "%s ~ %s" % (start.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"))


def build(base, cfg, now, week_text="", names=()):
    start, end, err = period_for(cfg, now, week_text)
    if err:
        return None, None, err
    report = collect(base, _stamp(start), _stamp(end))
    spec = settings(cfg)
    md = render_markdown(report, _label(start, end), spec["timezone"], names)
    return md, render_csv(report), ""


def split_text(text, limit=3000):
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    parts, rest = [], text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        piece, rest = rest[:cut].rstrip(), rest[cut:].lstrip("\n")
        if piece:
            parts.append(piece)
    return parts


def _owner_key(owner):
    return hashlib.sha256(str(owner).encode("utf-8")).hexdigest()[:12]


def in_send_slot(moment, weekday, hour, minute, window):
    if not _digest().in_window(moment, hour, minute, window):
        return False
    start_min = hour * 60 + minute
    cur = moment.hour * 60 + moment.minute
    if window > 0 and start_min + window > 24 * 60 and cur < (start_min + window) % (24 * 60):
        nominal = (moment.weekday() - 1) % 7
    else:
        nominal = moment.weekday()
    return nominal == weekday


def push_report(cfg, base, names, now, sender, targets, week_text=""):
    """立刻发给 targets。不占每周一次的名额。返回成功的人数。"""
    md, _csv, err = build(base, cfg, now, week_text, names)
    if err or not md:
        return 0
    parts = split_text(md)
    if not parts:
        return 0
    start, end, _err = period_for(cfg, now, week_text)
    week = start.date().isoformat() if start else "week"
    sent = 0
    for owner in targets:
        ok = True
        for index, part in enumerate(parts):
            key = _outbound().idem_key("report-now|%s|%s|%d" % (week, _owner_key(owner), index))
            try:
                result = sender("user", owner, part, key)
            except Exception:
                result = "fail"
            if result != "ok":
                ok = False
                break
        if ok:
            sent += 1
    return sent


def deliver(cfg, base, names, now, sender, log=None):
    """到点后每个主人每周最多一份。先占坑再发送。明确失败会放掉名额。"""
    spec = settings(cfg)
    if not spec["enabled"] or not spec["time_ok"] or not spec["weekday_ok"]:
        return []
    tz = _digest().zone(spec["timezone"])
    if tz is None:
        return []
    moment = _digest().local_now(now, tz)
    if not in_send_slot(moment, spec["weekday"], spec["hour"], spec["minute"], spec["window"]):
        return []
    owners = owner_ids(cfg)
    path = _report_state(base)
    start, end = _at(moment, spec["weekday"])
    week = start.date().isoformat()
    if not owners:
        _note_once(base, week, log)
        return []
    md, _csv, err = build(base, cfg, now, "", names)
    if err or not md or _outbound().mentions_everyone(md):
        return []
    parts = split_text(md)
    if not parts:
        return []
    sent = []
    for owner in owners:
        slot = "%s|%s" % (week, _owner_key(owner))
        with _digest().outbound_lock(path):
            state = _load(path)
            items = state.setdefault("items", {})
            cur = items.get(slot) or {}
            if cur.get("status") in ("posted", "claimed"):
                continue
            items[slot] = {"status": "claimed", "at": float(now)}
            _save(path, state)
        results = []
        for index, part in enumerate(parts):
            key = _outbound().idem_key("report|%s|%s|%d" % (week, _owner_key(owner), index))
            try:
                results.append(sender("user", owner, part, key))
            except Exception:
                results.append("fail")
                break
        if results and all(item == "ok" for item in results):
            outcome = "ok"
        elif any(item == "fail" for item in results):
            outcome = "fail"
        else:
            outcome = "unknown"
        with _digest().outbound_lock(path):
            state = _load(path)
            items = state.setdefault("items", {})
            if outcome == "ok":
                items[slot] = {"status": "posted", "at": float(now)}
                sent.append(owner)
            elif outcome == "fail":
                items.pop(slot, None)
            else:
                items[slot] = {"status": "claimed", "at": float(now)}
            _save(path, state)
        if log:
            log.info("report %s week=%s parts=%d", "posted" if outcome == "ok" else outcome, week, len(parts))
    return sent


def _note_once(base, week, log):
    path = _report_state(base)
    slot = week + "|no-owner"
    try:
        with _digest().outbound_lock(path):
            state = _load(path)
            notes = state.setdefault("notes", {})
            if notes.get(slot):
                return
            notes[slot] = 1
            _save(path, state)
    except TimeoutError:
        return
    if log:
        log.info("report skip: no owner")


def run_cli(cfg, base, names, week, out_path):
    """打印或导出，不发送。返回进程退出码。"""
    if not _BASE:
        configure(base)
    md, csv_text, err = build(base, cfg, time.time(), week, names)
    if err == "timezone":
        print("report: 时区认不出来，没有导出")
        return 2
    if err == "weekday":
        print("report: weekday 认不出来，没有导出")
        return 2
    if err == "week":
        print("report: --week 要用 2026-W42 或 2026-10-12")
        return 2
    if out_path:
        low = out_path.lower()
        if low.endswith(".csv"):
            body, encoding = csv_text, "utf-8-sig"
        elif low.endswith(".md"):
            body, encoding = md, "utf-8"
        else:
            print("report: --out 只接受 .md 或 .csv")
            return 2
        parent = os.path.dirname(os.path.abspath(out_path))
        try:
            os.makedirs(parent, exist_ok=True)
            with open(out_path, "w", encoding=encoding, newline="") as handle:
                handle.write(body)
        except OSError as exc:
            print("report: 没有写成文件 (%s)" % type(exc).__name__)
            return 2
        print("report wrote %s" % os.path.basename(out_path))
        return 0
    print(md, end="" if md.endswith("\n") else "\n")
    return 0
