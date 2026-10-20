# 长连接是不是还活着。安静的群不算断线：没有新消息时什么都不做。
# 只有进程退出、对方日志写明连接断了，或者墙上时钟比单调时钟多跳了一截（休眠或唤醒），才重连。
# 要结束的是我们拉起的那一个进程。创建时间对不上就不发信号，避免编号被别人复用。
import os, subprocess, threading, time

_DOWN = (
    "feishu-websocket: disconnected",
    "feishu-websocket: closed",
    "websocket: close",
    "connection reset by peer",
    "broken pipe",
    "use of closed network connection",
    "network is unreachable",
    "no such host",
    "i/o timeout",
)
_UP = ("feishu-websocket: connected", "ready event_key=")


def jump_setting(cfg):
    """没写 link_jump_sec 时用 20 秒。写成 0 表示不因为休眠去重连（进程退出仍会重连）。写错按 20 秒。"""
    try:
        value = float((cfg or {}).get("link_jump_sec", 20))
    except (TypeError, ValueError):
        return 20.0
    if value < 0:
        return 20.0
    return value


def clock_jump(prev_mono, prev_wall, now_mono, now_wall, min_jump):
    """墙上时钟比单调时钟多走的秒数。机器休眠时单调时钟基本停住，墙上时钟会跳。
    两者一起往前走（群里没人说话）返回 0。时钟回拨也返回 0。"""
    if not min_jump or min_jump <= 0:
        return 0.0
    try:
        mono = float(now_mono) - float(prev_mono)
        wall = float(now_wall) - float(prev_wall)
    except (TypeError, ValueError):
        return 0.0
    if wall <= 0 or mono < 0:
        return 0.0
    gap = wall - mono
    if gap >= float(min_jump):
        return gap
    return 0.0


def classify_log(line):
    """lark-cli 自己的日志。连上了返回 up，写明断开返回 down，其它包括「很久没有消息」返回空。"""
    text = (line or "").lower()
    if any(mark in text for mark in _UP):
        return "up"
    if any(mark in text for mark in _DOWN):
        return "down"
    return ""


def restart_delay(failures, cap=60):
    """连续失败次数。第一次等 1 秒，之后翻倍，最多 cap 秒。成功连上后调用方把次数清零。"""
    try:
        n = int(failures)
    except (TypeError, ValueError):
        n = 1
    if n <= 1:
        return 1
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 60
    if cap < 1:
        cap = 60
    return min(cap, 2 ** (n - 1))


def created_token(pid):
    """进程创建时间。读不到或进程不在了返回空字符串。不结束任何进程。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    if pid <= 0:
        return ""
    if os.name == "nt":
        return _win_created(pid)
    return _linux_created(pid)


def _linux_created(pid):
    try:
        stat = open("/proc/%s/stat" % int(pid), "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    right = stat.rfind(")")
    if right < 0:
        return ""
    fields = stat[right + 2:].split()
    if len(fields) < 20:
        return ""
    return fields[19]


def _win_created(pid):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class FT(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    h = k.OpenProcess(0x1000, False, int(pid))
    if not h:
        return ""
    created, exited, kernel, user = FT(), FT(), FT(), FT()
    try:
        if not k.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return ""
        return str((created.hi << 32) | created.lo)
    finally:
        k.CloseHandle(h)


def next_consumer_action(still_alive, reason, shutdown):
    """上一接收进程还活着时，不能再启动一个。停机则结束这一轮。"""
    if shutdown or reason == "shutdown":
        return "stop"
    if still_alive:
        return "wait"
    return "restart"


def may_signal(proc, created):
    """只有创建时间仍和拉起时一致，才可以向这个进程发信号。已经退出、没记下创建时间、或编号被复用，都返回 False。"""
    if proc is None or not created:
        return False
    try:
        if proc.poll() is not None:
            return False
    except OSError:
        return False
    current = created_token(proc.pid)
    return bool(current) and str(current) == str(created)


class LinkWatch:
    """记录时钟和每一路订阅的连续失败次数。本身不启动子进程。"""

    def __init__(self, jump_sec=20, on_wake=None):
        self.jump_sec = jump_sec
        self.on_wake = on_wake
        self.prev_mono = None
        self.prev_wall = None
        self.generation = 0
        self.failures = {}
        self._last_wake_wall = 0.0
        self.link_up = False
        self.link_since = None
        self.on_change = None
        self._lock = threading.Lock()

    def start(self, now_mono=None, now_wall=None):
        self.prev_mono = time.monotonic() if now_mono is None else now_mono
        self.prev_wall = time.time() if now_wall is None else now_wall
        with self._lock:
            if self.link_since is None:
                self.link_since = self.prev_wall

    def mark_link(self, up, now=None):
        """连接状态变化时才改计时。已经是断开时再记一次断开，不把计时清零。"""
        now = time.time() if now is None else float(now)
        changed = None
        with self._lock:
            if self.link_since is None:
                self.link_up = bool(up)
                self.link_since = now
                return
            if bool(up) == bool(self.link_up):
                return
            prev_up = bool(self.link_up)
            prev_since = float(self.link_since)
            self.link_up = bool(up)
            self.link_since = now
            changed = (bool(up), prev_up, prev_since, now)
        if changed and self.on_change:
            try:
                self.on_change(*changed)
            except Exception:
                pass

    def link_snapshot(self):
        with self._lock:
            since = self.link_since if self.link_since is not None else time.time()
            return bool(self.link_up), float(since)

    def pulse(self, now_mono=None, now_wall=None):
        """发现休眠或时钟前跳时返回多出来的秒数，并把 generation 加一。30 秒内不重复报。"""
        now_mono = time.monotonic() if now_mono is None else now_mono
        now_wall = time.time() if now_wall is None else now_wall
        if self.prev_mono is None:
            self.start(now_mono, now_wall)
            return 0.0
        gap = clock_jump(self.prev_mono, self.prev_wall, now_mono, now_wall, self.jump_sec)
        self.prev_mono, self.prev_wall = now_mono, now_wall
        if not gap:
            return 0.0
        if self._last_wake_wall and (now_wall - self._last_wake_wall) < 30:
            return 0.0
        self._last_wake_wall = now_wall
        with self._lock:
            self.generation += 1
        return gap

    def note(self, key, line):
        kind = classify_log(line)
        if kind == "up":
            with self._lock:
                self.failures[key] = 0
            self.mark_link(True)
        elif kind == "down":
            self.mark_link(False)
        return kind

    def fail(self, key):
        with self._lock:
            n = int(self.failures.get(key) or 0) + 1
            self.failures[key] = n
        return restart_delay(n)
