# 早晨摘要。默认关。到点往配置里的群发一条：今天的团队日历，以及任务表里还没完成的行。
# 不调用模型。每个群本地日历日最多一条，重启也不会再发。正文不点名所有人。
import datetime, json, os, re, time
import guard, outbound

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 及更早没有。本仓库要求 3.12。
    ZoneInfo = None

_PROMPT_NOTE = "早晨摘要由程序在配置的时间发给指定群，不是你这一轮要写的。回答里不要点名所有人，也不要写出提及所有人的标记。"
_ID = outbound._ID
_DONE = ["已完成", "完成", "已关闭", "关闭", "取消", "已取消", "done", "closed", "cancelled", "canceled"]
_TITLE = ("任务", "标题", "名称", "事项", "title", "name", "summary")
_OWNER = ("负责人", "执行人", "owner", "assignee")
_DUE = ("截止", "截止日期", "到期", "due", "deadline")
_CANCELLED = {"cancelled", "canceled", "已取消"}


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
    if not text or text.startswith("REPLACE_") or not _ID.match(text):
        return ""
    return text


def enabled(cfg):
    raw = (cfg or {}).get("digest")
    return isinstance(raw, dict) and bool(raw.get("enabled"))


def prompt_note(cfg):
    """放进稳定前缀。关掉时是空字符串，提示词和没有这项时一样。"""
    return _PROMPT_NOTE if enabled(cfg) else ""


def settings(cfg):
    raw = (cfg or {}).get("digest")
    if not isinstance(raw, dict):
        raw = {}
    hh, mm = 8, 30
    text = str(raw.get("time") or "08:30").strip()
    matched = re.match(r"^(\d{1,2}):(\d{2})$", text)
    ok_time = False
    if matched:
        hh, mm = int(matched.group(1)), int(matched.group(2))
        ok_time = 0 <= hh <= 23 and 0 <= mm <= 59
    if not ok_time:
        hh, mm = 8, 30
    window = raw.get("window_min", 30)
    if window is None or window == "":
        window = 30
    else:
        window = _num(window, 30)
    chats = []
    for item in raw.get("chat_ids") or []:
        cid = _clean_id(item)
        if cid and cid not in chats:
            chats.append(cid)
    done = []
    source = raw.get("done_values")
    if not isinstance(source, list) or not source:
        source = _DONE
    for item in source:
        word = str(item or "").strip()
        if word and word not in done:
            done.append(word)
    title_field = str(raw.get("title_field") or "").strip()[:40]
    status_field = str(raw.get("status_field") or "状态").strip()[:40] or "状态"
    include_cal = raw.get("include_calendar")
    include_tasks = raw.get("include_tasks")
    return {
        "enabled": bool(raw.get("enabled")),
        "hour": hh,
        "minute": mm,
        "time_ok": ok_time or "time" not in raw,
        "window": window,
        "timezone": str(raw.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
        "chats": chats,
        "done": done,
        "title_field": title_field,
        "status_field": status_field,
        "include_calendar": True if include_cal is None else bool(include_cal),
        "include_tasks": True if include_tasks is None else bool(include_tasks),
    }


def calendar_id(cfg):
    tools = ((cfg or {}).get("tools") or {})
    cal = tools.get("calendar_read") if isinstance(tools.get("calendar_read"), dict) else {}
    resources = (((cfg or {}).get("write") or {}).get("resources") or {})
    return _clean_id(cal.get("calendar_id")) or _clean_id(resources.get("calendar_id"))


def task_table(cfg):
    tools = ((cfg or {}).get("tools") or {})
    base = tools.get("base_read") if isinstance(tools.get("base_read"), dict) else {}
    token = _clean_id(base.get("base_token"))
    table = _clean_id(base.get("table_id"))
    if token and table:
        return token, table
    return None


# 中国没有夏令时。Windows 上如果没有 tzdata，ZoneInfo 会失败，默认时区仍按东八区计算。
_FIXED_ZONES = {
    "Asia/Shanghai": datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai"),
    "Asia/Chongqing": datetime.timezone(datetime.timedelta(hours=8), "Asia/Chongqing"),
    "Asia/Harbin": datetime.timezone(datetime.timedelta(hours=8), "Asia/Harbin"),
    "UTC": datetime.timezone.utc,
    "Etc/UTC": datetime.timezone.utc,
}


def zone(name):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return _FIXED_ZONES.get(name)


def local_now(now, tz):
    return datetime.datetime.fromtimestamp(float(now), tz)


def in_window(moment, hour, minute, window):
    cur = moment.hour * 60 + moment.minute
    start = hour * 60 + minute
    if window <= 0:
        return cur == start
    end = start + window
    if end <= 24 * 60:
        return start <= cur < end
    return cur >= start or cur < (end % (24 * 60))


def day_key(moment, hour, minute, window):
    """跨过午夜的窗口仍算开始那天，避免 23:50 和 00:10 各发一条。"""
    cur = moment.hour * 60 + moment.minute
    start = hour * 60 + minute
    end = start + max(window, 0)
    if window > 0 and end > 24 * 60 and cur < (end % (24 * 60)):
        return (moment.date() - datetime.timedelta(days=1)).isoformat()
    return moment.date().isoformat()


def _state_path(base):
    return os.path.join(base, "state", "digest_state.json")


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


def _slot(day, chat):
    return "%s|%s" % (day, chat)


def _field_text(value):
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_field_text(item) for item in value[:8]]
        return "、".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if isinstance(value.get(key), str) and value.get(key).strip():
                return value[key].strip()
        return ""
    return ""


def _rows(data):
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict) or data.get("_error"):
        return []
    for key in ("items", "events", "event_list", "agendas", "records"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    inner = data.get("data")
    if isinstance(inner, (dict, list)) and inner is not data:
        return _rows(inner)
    if any(k in data for k in ("summary", "start_time", "start", "fields")):
        return [data]
    return []


def _clock(value, tz):
    if value is None:
        return ""
    if isinstance(value, dict):
        if value.get("date") and not value.get("timestamp"):
            return "全天"
        value = value.get("timestamp") or value.get("date_time") or value.get("datetime") or ""
    text = str(value).strip()
    if not text:
        return ""
    if re.match(r"^\d{10,13}$", text):
        stamp = int(text)
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        try:
            moment = datetime.datetime.fromtimestamp(stamp, tz)
        except (OverflowError, OSError, ValueError):
            return ""
        return moment.strftime("%H:%M")
    matched = re.search(r"T(\d{2}):(\d{2})", text)
    if matched:
        return "%s:%s" % (matched.group(1), matched.group(2))
    matched = re.search(r"(\d{2}):(\d{2})", text)
    if matched:
        return "%s:%s" % (matched.group(1), matched.group(2))
    return ""


def _event_lines(data, tz):
    lines = []
    for item in _rows(data):
        status = str(item.get("status") or "").strip().lower()
        if status in _CANCELLED:
            continue
        title = _field_text(item.get("summary") or item.get("title") or item.get("name") or "")
        if not title:
            continue
        start = item.get("start_time") if item.get("start_time") is not None else item.get("start")
        end = item.get("end_time") if item.get("end_time") is not None else item.get("end")
        left, right = _clock(start, tz), _clock(end, tz)
        if left == "全天":
            when = "全天"
        elif left and right:
            when = "%s–%s" % (left, right)
        elif left:
            when = left
        else:
            when = ""
        lines.append((left or "99:99", ("- %s %s" % (when, title)).strip()))
        if len(lines) >= 12:
            break
    lines.sort(key=lambda pair: pair[0])
    return [pair[1] for pair in lines]


def _is_done(text, done):
    cur = text.strip().casefold()
    if not cur:
        return False
    for word in done:
        if cur == str(word).strip().casefold():
            return True
    return False


def _pick(fields, keys, explicit=""):
    if explicit and explicit in fields:
        return _field_text(fields.get(explicit))
    for key in keys:
        if key in fields:
            return _field_text(fields.get(key))
    return ""


def _task_lines(data, spec):
    lines = []
    for item in _rows(data):
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else item
        if not isinstance(fields, dict):
            continue
        status = _field_text(fields.get(spec["status_field"]))
        if _is_done(status, spec["done"]):
            continue
        title = _pick(fields, _TITLE, spec["title_field"])
        if not title:
            continue
        extra = []
        owner = _pick(fields, _OWNER)
        due = _pick(fields, _DUE)
        if owner:
            extra.append(owner)
        if due:
            extra.append("截止 " + due)
        suffix = ("（%s）" % "，".join(extra)) if extra else ""
        lines.append("- %s%s" % (title, suffix))
        if len(lines) >= 12:
            break
    return lines


def _range(moment):
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def requests_for(cfg, moment):
    """返回要调用的 (工具名, argv)。没配置或关掉的栏不出现。"""
    spec = settings(cfg)
    out = []
    if spec["include_calendar"]:
        cid = calendar_id(cfg)
        if cid:
            start, end = _range(moment)
            argv = ["calendar", "+agenda", "--calendar-id", cid, "--start", start, "--end", end]
            out.append(("calendar_read", argv))
    if spec["include_tasks"]:
        table = task_table(cfg)
        if table:
            token, tid = table
            argv = ["base", "+record-list", "--base-token", token, "--table-id", tid, "--limit", "50", "--format", "json"]
            out.append(("base_read", argv))
    return out


def render(cfg, moment, calendar_data, task_data, names):
    spec = settings(cfg)
    name = ""
    bot_names = (cfg or {}).get("bot_names") or []
    if bot_names and isinstance(bot_names[0], str):
        name = bot_names[0].strip()[:40]
    if not name:
        name = "机器人"
    lines = ["【%s】今日摘要（%s）" % (name, moment.date().isoformat())]
    failed = []
    if spec["include_calendar"] and calendar_id(cfg):
        if isinstance(calendar_data, dict) and calendar_data.get("_error"):
            lines += ["", "日程：暂时没有读到。"]
            failed.append("calendar")
        else:
            events = _event_lines(calendar_data, moment.tzinfo)
            lines += ["", "日程"]
            lines += events or ["今天没有日程。"]
    if spec["include_tasks"] and task_table(cfg):
        if isinstance(task_data, dict) and task_data.get("_error"):
            lines += ["", "未完成任务：暂时没有读到。"]
            failed.append("tasks")
        else:
            tasks = _task_lines(task_data, spec)
            lines += ["", "未完成任务"]
            lines += tasks or ["没有未完成的任务。"]
    if len(lines) == 1:
        return "", failed
    text = guard.neutralize(guard.redact("\n".join(lines), names))
    text = outbound.prepare(text, names)
    return text, failed


def deliver(cfg, base, names, now, fetch, sender, served, log=None):
    """fetch(tool, argv) -> data。sender(chat, text, key) -> ok/fail/unknown。
    served 是允许回答的群。不在这份名单里的群不发。"""
    spec = settings(cfg)
    if not spec["enabled"] or not spec["time_ok"]:
        return []
    tz = zone(spec["timezone"])
    if tz is None:
        _note_once(base, "tz", spec["timezone"] or "empty", log, "digest skipped: bad timezone")
        return []
    moment = local_now(now, tz)
    if not in_window(moment, spec["hour"], spec["minute"], spec["window"]):
        return []
    day = day_key(moment, spec["hour"], spec["minute"], spec["window"])
    allowed = []
    served_set = {str(item) for item in (served or [])}
    for chat in spec["chats"]:
        if chat in served_set:
            allowed.append(chat)
    if not allowed:
        _note_once(base, day, "no-chat", log, "digest skip: no served chat")
        return []
    wanted = requests_for(cfg, moment)
    if not wanted:
        _note_once(base, day, "no-source", log, "digest skip: no calendar or task table")
        return []
    path = _state_path(base)
    pending = []
    with outbound_lock(path):
        state = _load(path)
        retry = float(state.get("retry_after") or 0)
        items = state.get("items") or {}
        for chat in allowed:
            slot = items.get(_slot(day, chat)) or {}
            if slot.get("status") not in ("posted", "claimed"):
                pending.append(chat)
    if not pending:
        return []
    # 上一轮日历和任务都没读到时，先等几分钟再打接口。
    if float(now) < retry:
        return []
    got = {}
    for tool, argv in wanted:
        try:
            got[tool] = fetch(tool, argv)
        except Exception as exc:
            got[tool] = {"_error": guard.redact(str(exc), names)[:200]}
    text, failed = render(cfg, moment, got.get("calendar_read"), got.get("base_read"), names)
    if not text or outbound.mentions_everyone(text):
        return []
    # 配置的栏都失败时先不占坑，窗口里还可以再试。有一部分成功就发出去，避免同一天再发一条。
    if failed and len(failed) >= len(wanted):
        with outbound_lock(path):
            state = _load(path)
            state["retry_after"] = float(now) + 300
            _save(path, state)
        if log:
            log.info("digest retry later: sources failed")
        return []
    sent = []
    for chat in pending:
        key = outbound.idem_key("digest|%s|%s" % (day, chat))
        with outbound_lock(path):
            state = _load(path)
            items = state.setdefault("items", {})
            slot = items.get(_slot(day, chat)) or {}
            if slot.get("status") in ("posted", "claimed"):
                continue
            items[_slot(day, chat)] = {"status": "claimed", "at": float(now)}
            _save(path, state)
        try:
            result = sender(chat, text, key)
        except Exception:
            result = "fail"
        with outbound_lock(path):
            state = _load(path)
            items = state.setdefault("items", {})
            if result == "ok":
                items[_slot(day, chat)] = {"status": "posted", "at": float(now)}
                sent.append(chat)
            elif result == "fail":
                items.pop(_slot(day, chat), None)
            else:
                items[_slot(day, chat)] = {"status": "claimed", "at": float(now)}
            _save(path, state)
        if log:
            log.info("digest %s result=%s", "posted" if result == "ok" else result, result)
    return sent


def _note_once(base, day, kind, log, message):
    path = _state_path(base)
    slot = "%s|%s" % (day, kind)
    try:
        with outbound_lock(path):
            state = _load(path)
            notes = state.setdefault("notes", {})
            if notes.get(slot):
                return
            notes[slot] = 1
            _save(path, state)
    except TimeoutError:
        return
    if log:
        log.info(message)


def outbound_lock(path):
    import health_alert
    return health_alert._Lock(path)
