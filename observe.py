# 每次回答记一条结构化日志：用了哪个工具、错误码、耗时。不记问题原文。
# 字符串先脱敏。日志文件由本进程自己打开再关掉，所以可以轮转。
import json, os, re, threading, time
import guard

_SECRET_KEY = re.compile(r"(app_secret|api_key|password|passwd|secret)", re.I)


def code_of(detail):
    """从工具异常的 detail 里取出飞书错误码。拿不到就是空字符串。"""
    text = detail or ""
    if not isinstance(text, str):
        text = str(text)
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        if parsed.get("code") is not None:
            return str(parsed.get("code"))
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("code") is not None:
            return str(err.get("code"))
    found = re.search(r'"code"\s*:\s*(\d+)', text)
    return found.group(1) if found else ""


def _clean(value, names):
    if isinstance(value, str):
        return guard.redact(value, names)[:500]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                out[str(key)] = "[已隐藏]"
            else:
                out[str(key)] = _clean(item, names)
        return out
    if isinstance(value, list):
        return [_clean(item, names) for item in value[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return guard.redact(str(value), names)[:200]


class Observe:
    def __init__(self, log_dir, names=(), max_bytes=2_000_000, backups=5):
        self.log_dir = log_dir
        self.names = names
        self.max_bytes = max_bytes
        self.backups = backups
        self.path = os.path.join(log_dir, "events.jsonl")
        self._lock = threading.Lock()
        self._open = {}

    def begin(self, mid):
        with self._lock:
            self._open[str(mid)] = []

    def note_tool(self, mid, name, ok, code, elapsed, err):
        row = {"tool": str(name or ""), "ok": bool(ok), "code": "" if code is None else str(code),
               "elapsed": round(float(elapsed or 0), 3), "err": _clean(err or "", self.names)}
        with self._lock:
            self._open.setdefault(str(mid), []).append(row)
        self.write({"kind": "tool", "mid": str(mid)[:80], **row})

    def finish(self, mid, ok, elapsed):
        with self._lock:
            tools = list(self._open.pop(str(mid), []))
        brief = [{"tool": t["tool"], "ok": t["ok"], "code": t["code"]} for t in tools]
        self.write({"kind": "answer", "mid": str(mid)[:80], "ok": bool(ok),
                    "elapsed": round(float(elapsed or 0), 3), "tools": brief})

    def write(self, row):
        row = _clean(dict(row), self.names)
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        os.makedirs(self.log_dir, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._rotate_events()

    def _rotate_events(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.max_bytes:
            return
        backups = max(1, int(self.backups))
        for index in range(backups, 1, -1):
            src = "%s.%d" % (self.path, index - 1)
            dst = "%s.%d" % (self.path, index)
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    return
        try:
            os.replace(self.path, self.path + ".1")
            open(self.path, "a", encoding="utf-8").close()
        except OSError:
            return

    def shrink_stdio(self):
        for name in ("stdout.log", "stderr.log", "watchdog.log"):
            shrink_file(os.path.join(self.log_dir, name), self.max_bytes, self.backups)


def shrink_file(path, max_bytes, backups):
    """文件正在被别的句柄写着时，改名可能失败。这里把内容抄到备份再把原文件截断。截不断就放弃。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    try:
        max_bytes = int(max_bytes)
    except (TypeError, ValueError):
        return False
    if max_bytes < 1 or size < max_bytes:
        return False
    try:
        with open(path, "rb") as handle:
            if size > max_bytes * 4:
                handle.seek(size - max_bytes)
            data = handle.read(max_bytes if size > max_bytes * 4 else size)
    except OSError:
        return False
    backups = max(1, int(backups or 1))
    for index in range(backups, 1, -1):
        src = "%s.%d" % (path, index - 1)
        dst = "%s.%d" % (path, index)
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                return False
    try:
        with open(path + ".1", "wb") as handle:
            handle.write(data)
    except OSError:
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        os.ftruncate(fd, 0)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True
