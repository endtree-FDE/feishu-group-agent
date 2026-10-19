# 判断一个 PID 是不是「这个目录里的机器人」。
# Windows 会把退掉的 PID 很快分给别的程序。只看进程还在不在，会把别的程序当成自己还活着，
# 或者在停进程时打到别人。这里同时看创建时间和命令行。对不上就当成锁已经过期。
# 本模块不结束任何进程。
import hashlib, os, subprocess, tempfile, time


def norm_path(path):
    return (path or "").replace("/", "\\").rstrip("\\").lower()


def under_base(path, base):
    n, b = norm_path(path), norm_path(base)
    if not n or not b:
        return False
    return n == b or n.startswith(b + "\\")


def parse_lock(text):
    """锁文件第一行永远是 PID，给旧的 stop.ps1 用。后面两行是创建时间和目录，可以没有。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or not lines[0].isdigit():
        return None
    lock = {"pid": int(lines[0]), "created": "", "base": ""}
    for line in lines[1:]:
        if line.startswith("created="):
            lock["created"] = line.split("=", 1)[1].strip()
        elif line.startswith("base="):
            lock["base"] = line.split("=", 1)[1].strip()
    return lock


def format_lock(pid, created, base):
    return "%s\ncreated=%s\nbase=%s\n" % (int(pid), created or "", base or "")


def publish_exclusive(path, body):
    """把完整内容一次变成 path。别人要么看到完整文件，要么看到文件还不存在。
    先创建再写入时，Windows 上另一个进程会读到空文件，并把还活着的锁当成过期。"""
    if isinstance(body, str):
        body = body.encode("utf-8")
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.%d.%d.tmp" % (os.path.basename(path), os.getpid(), time.time_ns()))
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        view = body
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("lock write stalled")
            view = view[n:]
    except Exception:
        os.close(fd)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    try:
        if os.name == "nt":
            os.rename(tmp, path)
            return
        os.link(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        os.remove(tmp)
    except OSError:
        pass


def base_in_text(text, base):
    """base 作为完整路径出现在命令行里。后面只能是结尾、斜杠或引号。
    c:\\bots\\bot 不能命中 c:\\bots\\bot2。"""
    n, b = norm_path(text), norm_path(base)
    if not n or not b:
        return False
    start = 0
    while True:
        i = n.find(b, start)
        if i < 0:
            return False
        end = i + len(b)
        before_ok = i == 0 or n[i - 1] in " \\:\"'="
        after_ok = end >= len(n) or n[end] in "\\ \"'"
        if before_ok and after_ok:
            return True
        start = i + 1
    return False


def is_our_agent(cmdline, cwd, base):
    """命令行里要有 agentbot.py，并且本目录作为完整路径出现（脚本或配置文件都算）。
    命令行只有文件名时，才看工作目录是不是落在本目录。"""
    cl = cmdline or ""
    if "agentbot.py" not in cl.lower():
        return False
    if base_in_text(cl, base):
        return True
    if cwd and under_base(cwd, base):
        return True
    return False


def decide(lock, snap, base):
    """ours：这个锁还是本机器人，不能再启动。stale：可以接管，不要去动 snap 里的那个进程。"""
    if not lock or not lock.get("pid"):
        return "stale"
    if not snap:
        return "stale"
    if int(snap.get("pid") or 0) != int(lock["pid"]):
        return "stale"
    if lock.get("created") and snap.get("created") and str(lock["created"]) != str(snap["created"]):
        return "stale"
    cmd = snap.get("cmdline") or ""
    cwd = snap.get("cwd") or ""
    if cmd or cwd:
        return "ours" if is_our_agent(cmd, cwd, base) else "stale"
    # 读不到命令行。创建时间一致，或旧锁根本没写创建时间：宁可当成还活着，避免开出第二个。
    if lock.get("created") and snap.get("created") and str(lock["created"]) == str(snap["created"]):
        return "ours"
    if not lock.get("created"):
        return "ours"
    return "stale"


def app_lock_file(bot_id, directory=None):
    """同一应用的接收锁。文件名是编号的摘要，路径里不出现 open_id。"""
    digest = hashlib.sha256(str(bot_id or "").encode("utf-8")).hexdigest()[:24]
    directory = directory or os.environ.get("AGENTBOT_APP_LOCK_DIR") or os.path.join(tempfile.gettempdir(), "agentbot-locks")
    return os.path.join(directory, digest + ".lock")


def app_lock_held(lock, snap):
    """另一个还活着的 agentbot.py 占着这把锁（不管它的目录）。死进程或被别的程序复用的编号不算。"""
    if not lock or not lock.get("pid") or not snap:
        return False
    if int(snap.get("pid") or 0) != int(lock["pid"]):
        return False
    if lock.get("created") and snap.get("created") and str(lock["created"]) != str(snap["created"]):
        return False
    cmd = snap.get("cmdline") or ""
    if "agentbot.py" in cmd.lower():
        return True
    if not cmd and not (snap.get("cwd") or ""):
        if not lock.get("created") or (snap.get("created") and str(lock["created"]) == str(snap["created"])):
            return True
    return False


def conflict_blocks(snap):
    """conflict_pidfiles 里的旧进程。只有它看起来还是某个 agentbot，才拒绝启动。PID 被别的程序复用则不拒绝，也不结束它。"""
    if not snap:
        return False
    cmd = snap.get("cmdline") or ""
    cwd = snap.get("cwd") or ""
    if not cmd and not cwd:
        return True
    return "agentbot.py" in cmd.lower()


def _alive(pid):
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _linux_created(pid):
    try:
        stat = open("/proc/%s/stat" % int(pid), "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    right = stat.rfind(")")
    if right < 0:
        return ""
    fields = stat[right + 2:].split()
    # starttime 是 comm 之后的第 20 个字段（stat 的第 22 项）
    if len(fields) < 20:
        return ""
    return fields[19]


def _linux_cmdline(pid):
    try:
        raw = open("/proc/%s/cmdline" % int(pid), "rb").read()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def _linux_cwd(pid):
    try:
        return os.readlink("/proc/%s/cwd" % int(pid))
    except OSError:
        return ""


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


def _win_cmdline(pid):
    # 和 stop.ps1 一样问 WMI，不自己解析内存。PID 已收成整数，拼不进别的命令。
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + %d); if ($p) { $p.CommandLine }" % int(pid)],
            capture_output=True, timeout=8, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout or "").strip()


def snapshot(pid):
    """进程不在了返回 None。在的话带上创建时间、命令行、工作目录；某一项读不到就是空字符串。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or not _alive(pid):
        return None
    if os.name == "nt":
        return {"pid": pid, "created": _win_created(pid), "cmdline": _win_cmdline(pid), "cwd": ""}
    return {"pid": pid, "created": _linux_created(pid), "cmdline": _linux_cmdline(pid), "cwd": _linux_cwd(pid)}
