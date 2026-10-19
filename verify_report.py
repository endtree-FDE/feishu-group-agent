# 部署后验证的判断。只看调用方传进来的进程列表、锁文本和日志节选。
# 不列进程，不发消息，不写心跳，不结束任何进程。
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf8io  # noqa: F401
import proc_identity

_MAX_LOG = 2_000_000
_LEVEL = re.compile(r"^(OK|WARN|FAIL)\b\s*(.*)$")
_CONSUME = re.compile(r"(?:^|\s)event\s+consume\s+(\S+)", re.I)
_PROFILE = re.compile(r"--profile(?:=|\s+)([\"']?)([^\s\"']+)", re.I)
_JSON = re.compile(r'["\']([^"\']+\.json)["\']|(\S+\.json)', re.I)
_SKIP_FLAG = ("--doctor", "--check", "--dry-run", "--lock-test")


def line(level, name, info=""):
    return ("%s %s %s" % (level.ljust(4), name, info)).rstrip()


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def script_dir(cmdline):
    """agentbot.py 所在目录。相对路径返回空，交给工作目录。"""
    low = (cmdline or "").replace("/", "\\")
    token = "agentbot.py"
    find = low.lower().find(token)
    if find < 0:
        return ""
    j = find
    while j > 0 and low[j - 1] not in " \t\"'":
        j -= 1
    prefix = low[j:find].strip("\"'")
    cut = prefix.rfind("\\")
    if cut < 0:
        return ""
    return prefix[:cut].rstrip("\\")


def config_from_cmdline(cmdline):
    found = []
    for quoted, bare in _JSON.findall(cmdline or ""):
        path = quoted or bare
        if path.lower().endswith("agentbot.py"):
            continue
        found.append(path)
    return found[-1] if found else ""


def profile_of(cmdline):
    match = _PROFILE.search(cmdline or "")
    return match.group(2) if match else ""


def consume_event(cmdline):
    match = _CONSUME.search(cmdline or "")
    if not match:
        return ""
    return match.group(1).strip("\"'")


def is_serving_agent(cmdline):
    low = (cmdline or "").lower()
    if "agentbot.py" not in low:
        return False
    return not any(flag in low for flag in _SKIP_FLAG)


def _norm(path):
    return proc_identity.norm_path(path)


def judge_agentbots(processes):
    rows = []
    by_dir = {}
    for proc in processes:
        cmd = proc.get("cmdline") or ""
        if "agentbot.py" not in cmd.lower():
            continue
        directory = script_dir(cmd) or (proc.get("cwd") or "")
        flags = [flag for flag in _SKIP_FLAG if flag in cmd.lower()]
        pid = proc.get("pid")
        if flags:
            rows.append(("OK", "agentbot", "pid=%s dir=%s 这是 %s，不算正在服务" % (pid, directory, flags[0])))
            continue
        rows.append(("OK", "agentbot", "pid=%s dir=%s config=%s" % (pid, directory, config_from_cmdline(cmd))))
        key = _norm(directory)
        by_dir.setdefault(key, []).append(pid)
    for key, pids in by_dir.items():
        if len(pids) > 1:
            rows.append(("FAIL", "agentbot", "同一目录有 %d 个正在服务的进程 pids=%s" % (len(pids), ",".join(str(p) for p in pids))))
    if not any("agentbot.py" in (proc.get("cmdline") or "").lower() for proc in processes):
        rows.append(("WARN", "agentbot", "本机没有 agentbot.py 进程"))
    return rows


def judge_consumes(processes):
    """同一个 profile、同一种事件只能有一个 event consume。进群和出群是另外的事件。event _bus 不算。"""
    rows = []
    groups = {}
    for proc in processes:
        cmd = proc.get("cmdline") or ""
        if "lark-cli" not in cmd.lower():
            continue
        event = consume_event(cmd)
        if not event:
            continue
        profile = profile_of(cmd)
        groups.setdefault((profile, event), []).append(proc.get("pid"))
    if not groups:
        rows.append(("WARN", "consume", "没有看到 event consume（刚启动时可能还没有）"))
        return rows
    for (profile, event) in sorted(groups):
        pids = groups[(profile, event)]
        label = "profile=%s event=%s count=%d pids=%s" % (profile or "?", event, len(pids), ",".join(str(p) for p in pids))
        if not profile:
            rows.append(("WARN", "consume", label + " 没有 --profile"))
        elif len(pids) > 1:
            rows.append(("FAIL", "consume", label + " 同一个飞书应用同一种事件有两个接收进程。不要在这里结束它们"))
        else:
            rows.append(("OK", "consume", label))
    return rows


def judge_lock(directory, lock_exists, lock_text, lock_proc):
    """旧锁可以只有一行进程号。created= 对不上就是编号被复用，只报告，不结束进程。"""
    name = "lock"
    shown = directory or "?"
    if not lock_exists:
        return [("OK", name, "%s 没有锁文件" % shown)]
    lock = proc_identity.parse_lock((lock_text or "").lstrip("\ufeff"))
    if not lock:
        return [("FAIL", name, "%s 锁文件不是以进程号开头" % shown)]
    proc = lock_proc or {}
    alive = bool(proc.get("alive"))
    if not alive:
        return [("WARN", name, "%s 锁里的 pid %s 已经不在。启动时会当成过期锁接管，不会结束别的进程" % (shown, lock["pid"]))]
    cmdline = proc.get("cmdline") or ""
    cwd = proc.get("cwd") or ""
    ours = proc_identity.is_our_agent(cmdline, cwd, directory)
    rows = []
    created_lock = lock.get("created") or ""
    created_now = str(proc.get("created") or "")
    if created_lock and created_now and created_lock != created_now:
        rows.append(("FAIL", name, "%s 创建时间对不上（锁 %s，进程 %s）。编号可能已被复用。不要结束这个进程" % (shown, created_lock, created_now)))
    elif created_lock and not created_now:
        rows.append(("WARN", name, "%s 锁里有 created=，但读不到进程的创建时间" % shown))
    elif not created_lock:
        if ours:
            rows.append(("WARN", name, "%s 旧锁只有进程号，没有 created=。进程仍是本目录的 agentbot.py，第一次用本版本启动后会写上。不要为了补这一行去结束进程" % shown))
        elif not cmdline and not cwd:
            rows.append(("WARN", name, "%s 旧锁没有 created=，也读不到命令行。为避免开出第二个接收进程，启动会拒绝。先确认这个进程是谁" % shown))
        else:
            rows.append(("FAIL", name, "%s 锁指向的进程不是本目录的机器人，而且没有 created=。不要结束它。启动时会当成过期锁接管" % shown))
    elif ours:
        rows.append(("OK", name, "%s pid=%s created 与进程一致" % (shown, lock["pid"])))
    else:
        rows.append(("FAIL", name, "%s 创建时间一致，但命令行不是本目录的 agentbot.py。不要结束这个进程" % shown))
    base_lock = lock.get("base") or ""
    if base_lock:
        if _norm(base_lock) == _norm(directory):
            rows.append(("OK", name, "%s base 与目录一致" % shown))
        else:
            rows.append(("FAIL", name, "%s 锁里的 base 是 %s，不是这个目录" % (shown, base_lock)))
    elif created_lock:
        rows.append(("WARN", name, "%s 锁里没有 base=" % shown))
    return rows


# 这两个文件是后来加的。还没答过题、看门狗还没写过，不存在是空，不是轮转坏了。
_EMPTY_OK = {"events.jsonl", "watchdog.log"}


def judge_rotation(logs):
    rows = []
    for item in as_list(logs):
        name = item.get("name") or "?"
        if not item.get("exists"):
            if name in _EMPTY_OK:
                rows.append(("OK", "rotate", "%s 不存在（空）" % name))
            else:
                rows.append(("WARN", "rotate", "%s 不存在" % name))
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            backups = int(item.get("backups") or 0)
        except (TypeError, ValueError):
            backups = 0
        if size >= _MAX_LOG and backups <= 0:
            rows.append(("WARN", "rotate", "%s 已有 %d 字节，超过约 2MB，但没有 .1 备份。轮转可能没生效" % (name, size)))
        elif backups > 0:
            rows.append(("OK", "rotate", "%s 有 %d 份备份，当前 %d 字节" % (name, backups, size)))
        else:
            rows.append(("OK", "rotate", "%s 未超过 2MB（%d 字节）" % (name, size)))
    return rows


def judge_events(tail):
    """只报工具名和错误码。不打印 err，避免把残留的原文打出来。"""
    lines = as_list(tail)
    if not lines:
        return [("WARN", "events", "没有 events.jsonl 或文件是空的。刚启动、还没答过题时正常")]
    counts = {}
    bad = 0
    for raw in lines:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        if row.get("kind") == "tool":
            code = str(row.get("code") or "")
            if code and code != "0":
                key = (str(row.get("tool") or "?"), code)
                counts[key] = counts.get(key, 0) + 1
            elif row.get("ok") is False:
                key = (str(row.get("tool") or "?"), "")
                counts[key] = counts.get(key, 0) + 1
    rows = []
    if bad:
        rows.append(("WARN", "events", "有 %d 行不是 JSON" % bad))
    if not counts:
        rows.append(("OK", "events", "最近 %d 行没有错误码" % len([ln for ln in lines if str(ln).strip()])))
        return rows
    for (tool, code) in sorted(counts):
        if code:
            rows.append(("WARN", "events", "tool=%s 错误码 %s 共 %d 条（最近 %d 行）" % (tool, code, counts[(tool, code)], len(lines))))
        else:
            rows.append(("WARN", "events", "tool=%s 失败但没有错误码 共 %d 条（最近 %d 行）" % (tool, counts[(tool, code)], len(lines))))
    return rows


def judge_doctor(doctors):
    rows = []
    items = as_list(doctors)
    if not items:
        rows.append(("WARN", "doctor", "没有运行 --doctor"))
        return rows
    for item in items:
        label = item.get("label") or item.get("config") or "?"
        try:
            code = int(item.get("code"))
        except (TypeError, ValueError):
            code = 1
        output = item.get("output") or ""
        saw_fail = False
        kept = 0
        for raw in output.splitlines():
            match = _LEVEL.match(raw.strip())
            if not match:
                if raw.strip():
                    rows.append(("WARN", "doctor", "%s %s" % (label, raw.strip()[:180])))
                continue
            level, rest = match.group(1), match.group(2)
            if level == "FAIL":
                saw_fail = True
            rows.append((level, "doctor", "%s %s" % (label, rest)))
            kept += 1
        if code != 0 and not saw_fail:
            rows.append(("FAIL", "doctor", "%s 退出码 %s" % (label, code)))
        elif kept == 0:
            rows.append(("WARN", "doctor", "%s 没有输出" % label))
    return rows


def build_report(snap):
    snap = snap or {}
    processes = as_list(snap.get("processes"))
    rows = []
    if snap.get("process_error"):
        rows.append(("FAIL", "processes", "列不出本机进程"))
    rows.extend(judge_agentbots(processes))
    rows.extend(judge_consumes(processes))
    for bot in as_list(snap.get("bots")):
        directory = bot.get("dir") or ""
        rows.extend(judge_lock(directory, bool(bot.get("lock_exists")), bot.get("lock") or "", bot.get("lock_proc")))
        rows.extend(judge_rotation(bot.get("logs")))
        rows.extend(judge_events(bot.get("events_tail")))
    rows.extend(judge_doctor(snap.get("doctors")))
    ok = sum(1 for level, _name, _info in rows if level == "OK")
    warn = sum(1 for level, _name, _info in rows if level == "WARN")
    fail = sum(1 for level, _name, _info in rows if level == "FAIL")
    rows.append(("FAIL" if fail else "OK", "summary", "ok=%d warn=%d fail=%d" % (ok, warn, fail)))
    return rows


def render(rows):
    return "\n".join(line(level, name, info) for level, name, info in rows)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        print(line("FAIL", "usage", "需要从标准输入读一份 JSON，没有读到"))
        return 1
    try:
        snap = json.loads(raw)
    except Exception:
        print(line("FAIL", "usage", "标准输入不是 JSON"))
        return 1
    rows = build_report(snap)
    sys.stdout.write(render(rows) + "\n")
    return 1 if any(level == "FAIL" for level, _name, _info in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
