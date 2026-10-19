# 单实例锁和优雅停机。不连飞书，不结束身份对不上的进程。
import json, os, shutil, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import lifecycle, proc_identity

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


def _cfg(base):
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = base
    src["bot_open_id"] = "ou_placeholder_not_a_real_account"
    src["chat_ids"] = ["oc_placeholder"]
    src["profile"] = "placeholder"
    path = os.path.join(base, "bot.json")
    json.dump(src, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return path


def _run_bot(cfg, args, timeout=30):
    return subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


# ---------- 纯判断：创建时间对不上就是别人，不能当成本机器人 ----------
base = r"C:\bots\xiaojing"
ours = {"pid": 42, "created": "100", "cmdline": r"python C:\bots\xiaojing\agentbot.py C:\bots\xiaojing\agents\a.json", "cwd": base}
check("命令行和工作目录都是本目录时，锁仍有效",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, ours, base) == "ours")
reused = dict(ours, created="200", cmdline=r"python C:\bots\xiaojing\agentbot.py")
check("创建时间变了就是 PID 被复用，即使命令行看起来还像本机器人",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, reused, base) == "stale")
other = {"pid": 42, "created": "100", "cmdline": r"python D:\other\agentbot.py", "cwd": r"D:\other"}
check("另一个目录的 agentbot.py 不是本机器人",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, other, base) == "stale")
prefix = {"pid": 42, "created": "100", "cmdline": r"python C:\bots\xiaojing2\agentbot.py C:\bots\xiaojing2\agents\a.json", "cwd": r"C:\bots\xiaojing2"}
bare = {"pid": 42, "created": "100", "cmdline": r"python agentbot.py agents\a.json", "cwd": base}
check("目录名互为前缀时不是同一个机器人",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, prefix, base) == "stale"
      and proc_identity.base_in_text(prefix["cmdline"], base) is False
      and proc_identity.base_in_text(ours["cmdline"], base) is True)
check("命令行只有文件名时，工作目录在本目录才算自己",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, bare, base) == "ours")
note = {"pid": 42, "created": "100", "cmdline": r"C:\Windows\notepad.exe", "cwd": r"C:\Windows"}
check("锁被记事本复用时可以接管，且判断本身不涉及结束进程",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, note, base) == "stale")
check("旧锁只有进程号、又读不到命令行时，宁可当成还活着",
      proc_identity.decide({"pid": 42, "created": "", "base": ""},
                           {"pid": 42, "created": "100", "cmdline": "", "cwd": ""}, base) == "ours")
check("创建时间一致但读不到命令行时，仍视为本进程",
      proc_identity.decide({"pid": 42, "created": "100", "base": base},
                           {"pid": 42, "created": "100", "cmdline": "", "cwd": ""}, base) == "ours")
check("进程已经不在，锁过期",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, None, base) == "stale")
check("进程号对不上，锁过期",
      proc_identity.decide({"pid": 42, "created": "100", "base": base}, dict(ours, pid=7), base) == "stale")

check("冲突列表：进程不在则不拦截", proc_identity.conflict_blocks(None) is False)
check("冲突列表：记事本复用编号则不拦截", proc_identity.conflict_blocks(note) is False)
check("冲突列表：看起来还是 agentbot 则拦截", proc_identity.conflict_blocks(ours) is True)
check("冲突列表：活着但读不到命令行则拦截",
      proc_identity.conflict_blocks({"pid": 9, "created": "1", "cmdline": "", "cwd": ""}) is True)

text = proc_identity.format_lock(42, "100", base)
parsed = proc_identity.parse_lock(text)
check("锁文件第一行仍只是进程号",
      text.splitlines()[0] == "42" and parsed["pid"] == 42 and parsed["created"] == "100" and parsed["base"] == base)
check("旧的一行锁仍能解析", proc_identity.parse_lock("42\n")["created"] == "")

check("答完了就记成已回答", lifecycle.should_mark_answered(True, True) is True)
check("停机时还没答完就留下", lifecycle.should_mark_answered(True, False) is False)
check("不是停机时，异常也记成已回答", lifecycle.should_mark_answered(False, False) is True)
check("正常答完记成已回答", lifecycle.should_mark_answered(False, True) is True)
check("没写 shutdown_wait_sec 时等 15 秒，0 表示不等，写错按 15 秒",
      lifecycle.wait_sec({}) == 15 and lifecycle.wait_sec({"shutdown_wait_sec": 0}) == 0
      and lifecycle.wait_sec({"shutdown_wait_sec": -3}) == 15 and lifecycle.wait_sec({"shutdown_wait_sec": "x"}) == 15
      and lifecycle.wait_sec({"shutdown_wait_sec": 3}) == 3)

lifecycle.reset_notes()
note_dir = tempfile.mkdtemp(prefix="agentbot_note_")
try:
    lifecycle.touch(note_dir, "om_mid_1", "阶段里有 13812345678 和 C:\\secret\\kb\\a.md", ())
    raw = open(lifecycle.inflight_path(note_dir), encoding="utf-8").read()
    data = json.loads(raw)
    row = data["messages"][0]
    check("进度文件只记编号和阶段，并打掉手机号和本地路径",
          set(row) == {"mid", "stage", "started"} and "13812345678" not in raw and "secret" not in raw
          and "手机号已隐藏" in row["stage"] and row["mid"] == "om_mid_1", raw)
    lifecycle.touch(note_dir, "om_mid_1", "仍在思考", ())
    again = json.loads(open(lifecycle.inflight_path(note_dir), encoding="utf-8").read())["messages"][0]
    check("同一条进度不改开始时间", again["started"] == row["started"] and again["stage"] == "仍在思考")
finally:
    shutil.rmtree(note_dir, ignore_errors=True)
    lifecycle.reset_notes()


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _alive(proc):
    return proc.poll() is None


# ---------- 真进程：接管锁时不能把那个进程杀掉 ----------
holder = None
try:
    work = tempfile.mkdtemp(prefix="agentbot_lock_")
    cfg = _cfg(work)
    holder = _sleeper()
    snap = None
    # Windows 上刚拉起的进程，有时第一次查不到命令行。多等一会儿再判失败。
    for _ in range(8):
        time.sleep(0.25)
        snap = proc_identity.snapshot(holder.pid)
        if snap and snap.get("created") and "python" in (snap.get("cmdline") or "").lower():
            break
    check("能读到本机进程的创建时间和命令行",
          bool(snap and snap.get("created") and "python" in (snap.get("cmdline") or "").lower()), str(snap))
    os.makedirs(os.path.join(work, "state"), exist_ok=True)
    open(os.path.join(work, "state", "bot.lock"), "w", encoding="utf-8").write(str(holder.pid))
    got = _run_bot(cfg, ["--lock-test", "0.2"])
    out = (got.stdout or "") + (got.stderr or "")
    check("旧锁指向别的程序时接管，而不是拒绝启动",
          got.returncode == 0 and "stale lock" in out and "REFUSED" not in out, out)
    check("接管锁不会结束那个被复用编号的进程", _alive(holder), out)
finally:
    if holder and _alive(holder):
        holder.kill(); holder.wait(timeout=5)

holder = None
try:
    work = tempfile.mkdtemp(prefix="agentbot_lock_")
    other = tempfile.mkdtemp(prefix="agentbot_other_")
    cfg = _cfg(work)
    open(os.path.join(other, "agentbot.py"), "w", encoding="utf-8").write("import time\ntime.sleep(60)\n")
    holder = subprocess.Popen([sys.executable, os.path.join(other, "agentbot.py")], cwd=other)
    time.sleep(0.2)
    snap = proc_identity.snapshot(holder.pid)
    os.makedirs(os.path.join(work, "state"), exist_ok=True)
    open(os.path.join(work, "state", "bot.lock"), "w", encoding="utf-8").write(
        proc_identity.format_lock(holder.pid, (snap or {}).get("created") or "", work))
    got = _run_bot(cfg, ["--lock-test", "0.2"])
    out = (got.stdout or "") + (got.stderr or "")
    check("另一个目录的 agentbot 占着编号时仍可接管，并且不结束它",
          got.returncode == 0 and "stale lock" in out and _alive(holder), out)
finally:
    if holder and _alive(holder):
        holder.kill(); holder.wait(timeout=5)

holder = None
root = None
try:
    root = tempfile.mkdtemp(prefix="agentbot_sib_")
    short = os.path.join(root, "bot")
    longer = os.path.join(root, "bot2")
    os.makedirs(short)
    os.makedirs(longer)
    cfg = _cfg(short)
    open(os.path.join(longer, "agentbot.py"), "w", encoding="utf-8").write("import time\ntime.sleep(60)\n")
    holder = subprocess.Popen([sys.executable, os.path.join(longer, "agentbot.py")], cwd=longer)
    time.sleep(0.2)
    snap = proc_identity.snapshot(holder.pid)
    os.makedirs(os.path.join(short, "state"), exist_ok=True)
    open(os.path.join(short, "state", "bot.lock"), "w", encoding="utf-8").write(
        proc_identity.format_lock(holder.pid, (snap or {}).get("created") or "", short))
    got = _run_bot(cfg, ["--lock-test", "0.2"])
    out = (got.stdout or "") + (got.stderr or "")
    check("目录名互为前缀的真进程占着编号时可以接管，并且不结束它",
          got.returncode == 0 and "stale lock" in out and "REFUSED" not in out and _alive(holder), out)
finally:
    if holder and _alive(holder):
        holder.kill(); holder.wait(timeout=5)
    if root:
        shutil.rmtree(root, ignore_errors=True)

first = None
try:
    work = tempfile.mkdtemp(prefix="agentbot_lock_")
    cfg = _cfg(work)
    first = subprocess.Popen([sys.executable, os.path.join(FW, "agentbot.py"), cfg, "--lock-test", "8"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lock_path = os.path.join(work, "state", "bot.lock")
    for _ in range(50):
        if os.path.isfile(lock_path):
            break
        time.sleep(0.1)
    body = open(lock_path, encoding="utf-8").read() if os.path.isfile(lock_path) else ""
    second = _run_bot(cfg, ["--lock-test", "0.2"])
    out = (second.stdout or "") + (second.stderr or "")
    check("本机器人还在时第二个进程退出 3，锁的第一行仍是进程号",
          second.returncode == 3 and "REFUSED" in out and body.splitlines()[:1] == [str(first.pid)]
          and "created=" in body, out + "\n" + body)
    check("拒绝第二个实例时，已经在跑的那个还在", _alive(first), out)
finally:
    if first and _alive(first):
        first.terminate()
        try: first.wait(timeout=5)
        except subprocess.TimeoutExpired: first.kill()


def _py(src, env):
    r = subprocess.run([sys.executable, "-c", src], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=40)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _env(cfg):
    env = dict(os.environ)
    env["CFG"] = cfg
    env["AGENTBOT_FAKE_AGENT"] = "0"
    return env


# 停掉子进程时，创建时间对不上或为空就不能发终止信号。
stop_src = r'''
import os, sys, subprocess, time
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(0.2)
kind = os.environ["KIND"]
if kind == "mismatch":
    agentbot._CONSUMERS.append((child, "not-the-real-created"))
elif kind == "empty":
    agentbot._CONSUMERS.append((child, ""))
else:
    snap = agentbot.proc_identity.snapshot(child.pid) or {}
    agentbot._CONSUMERS.append((child, snap.get("created") or ""))
agentbot._stop_consumers()
time.sleep(0.3)
print("ALIVE" if child.poll() is None else "DEAD")
if child.poll() is None:
    child.kill()
'''
for kind, expect, title in (
    ("mismatch", "ALIVE", "创建时间对不上的子进程不会被终止"),
    ("empty", "ALIVE", "没有记下创建时间的子进程不会被终止"),
    ("match", "DEAD", "创建时间仍对得上的子进程才会被终止"),
):
    work = tempfile.mkdtemp(prefix="agentbot_stop_")
    try:
        code, out = _py(stop_src, dict(_env(_cfg(work)), KIND=kind))
        check(title, code == 0 and expect in out.split(), out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

conflict_src = r'''
import os, sys
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot
agentbot.CFG["conflict_pidfiles"] = [os.environ["PF"]]
try:
    agentbot.check_conflicts()
    print("NOBLOCK")
except SystemExit as e:
    print("EXIT", e.code)
'''
holder = None
try:
    work = tempfile.mkdtemp(prefix="agentbot_cf_")
    holder = _sleeper()
    time.sleep(0.2)
    pf = os.path.join(work, "old.pid")
    open(pf, "w", encoding="utf-8").write(str(holder.pid))
    code, out = _py(conflict_src, dict(_env(_cfg(work)), PF=pf))
    check("冲突列表里的编号若已是别的程序，不拒绝启动，也不结束它",
          code == 0 and "NOBLOCK" in out and _alive(holder), out)
finally:
    if holder and _alive(holder):
        holder.kill(); holder.wait(timeout=5)

holder = None
try:
    work = tempfile.mkdtemp(prefix="agentbot_cf_")
    other = tempfile.mkdtemp(prefix="agentbot_cfbot_")
    open(os.path.join(other, "agentbot.py"), "w", encoding="utf-8").write("import time\ntime.sleep(60)\n")
    holder = subprocess.Popen([sys.executable, os.path.join(other, "agentbot.py")], cwd=other)
    time.sleep(0.2)
    pf = os.path.join(work, "old.pid")
    open(pf, "w", encoding="utf-8").write(str(holder.pid))
    code, out = _py(conflict_src, dict(_env(_cfg(work)), PF=pf))
    check("冲突列表里仍是 agentbot 时拒绝启动，但不结束它",
          "EXIT 4" in out and "REFUSED" in out and _alive(holder), out)
finally:
    if holder and _alive(holder):
        holder.kill(); holder.wait(timeout=5)

handle_src = r'''
import json, os, sys
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run", os.environ["EMPTY"]]
os.environ.setdefault("AGENTBOT_FAKE_AGENT", "0")
import agentbot
ev = json.loads(os.environ["EV"])
mode = os.environ["MODE"]
def boom(*a, **k):
    if mode == "stop":
        agentbot.lifecycle.request_shutdown(agentbot.STATE)
    raise RuntimeError("mid-question")
if mode != "ok":
    agentbot._handle_claimed = boom
try:
    agentbot.handle(ev)
except RuntimeError:
    pass
row = agentbot.DB.execute("select status from seen where mid=?", (ev["message_id"],)).fetchone()
print("STATUS", row[0] if row else None)
'''
for mode, mid, title, expect in (
    ("stop", "mid_stop", "停机时题目还没走完，状态留在 received", ("STATUS received", "shutdown keeps inflight")),
    ("error", "mid_err", "不是停机时，处理中途出错仍记成已回答", ("STATUS answered",)),
):
    work = tempfile.mkdtemp(prefix="agentbot_hd_")
    try:
        cfg = _cfg(work)
        empty = os.path.join(work, "empty.jsonl")
        open(empty, "w", encoding="utf-8").write("")
        ev = {"message_id": mid, "chat_id": "oc_placeholder", "chat_type": "group", "message_type": "text",
              "sender_id": "user_sender", "sender_type": "user", "content": "@小助手 问题原文不要进进度文件 13812345678",
              "mentions": [{"id": "ou_placeholder_not_a_real_account", "key": "@_user_1", "name": "小助手"}]}
        env = _env(cfg)
        env.update(EMPTY=empty, EV=json.dumps(ev, ensure_ascii=False), MODE=mode)
        code, out = _py(handle_src, env)
        ok = code == 0 and all(part in out for part in expect)
        if mode == "error":
            ok = ok and "shutdown keeps" not in out
        check(title, ok, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)

stop_txt = open(os.path.join(FW, "scripts", "stop.ps1"), encoding="utf-8").read()
start_txt = open(os.path.join(FW, "scripts", "start.ps1"), encoding="utf-8").read()
check("停止脚本先请求自行退出，再按身份结束，且不含按编号强杀",
      "shutdown.request" in stop_txt and "Test-OurAgent" in stop_txt and "Stop-OwnedTree" in stop_txt
      and "bot.pid" in stop_txt and "bot.lock" in stop_txt
      and "taskkill" not in stop_txt.lower() and "Stop-Process -Id $id -Force" not in stop_txt
      and "Kill-Tree" not in stop_txt)
already = start_txt.find("already running")
req = start_txt.find("shutdown.request")
check("启动脚本只在真正启动时清掉停止请求，已经在跑时不清",
      already >= 0 and req > already and "ParentProcessId" not in start_txt and "taskkill" not in start_txt.lower())

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
