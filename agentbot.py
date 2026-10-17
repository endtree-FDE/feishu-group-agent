# 通用飞书群/私聊智能体：一份 JSON 配置 = 一个智能体。
# 用法: python agentbot.py agents/<name>.json
#   --dry-run [文件]   从 UTF-8 JSONL 文件（省略则 stdin）读事件，不调用飞书（回复打印到日志；工具默认只打印命令）
#   --parallel         dry-run 时每条事件一个线程（测并发）
#   --lock-test N      只拿单实例锁并保持 N 秒（不启动 consumer）
#   --check            自检：配置、目录、cursor-agent、lark-cli，可选探测权限（--probe），不启动 consumer
#   环境变量 AGENTBOT_FAKE_AGENT=秒：用 sleep 代替 cursor-agent（仅测试）
#   环境变量 AGENTBOT_AGENT_CMD='["python","fake.py"]'：替换 cursor-agent 可执行文件（仅测试，用来模拟 stream-json）
# 模型名字来自配置 agent.model；没写时用 DEFAULT_MODEL。登录和额度是运行这台电脑的人自己的 Cursor 账号，程序里不写账号。
import json, os, re, subprocess, sys, time, glob, logging, collections, threading, sqlite3, hashlib, atexit, shutil, datetime

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import guard, memory
from tools import Tools, ToolError, TOOL_DOC, READ_TOOLS, WRITE_TOOLS, run_capture

CFG_PATH = sys.argv[1]; ARGS = sys.argv[2:]
def _opt(name):
    if name not in ARGS: return None
    i = ARGS.index(name); nxt = ARGS[i + 1] if i + 1 < len(ARGS) else ""
    return nxt if nxt and not nxt.startswith("--") else ""
DRY = _opt("--dry-run") is not None; PARALLEL = "--parallel" in ARGS; LOCK_TEST = _opt("--lock-test"); CHECK = "--check" in ARGS
FAKE = os.environ.get("AGENTBOT_FAKE_AGENT")
CFG = json.load(open(CFG_PATH, encoding="utf-8-sig"))
if not CFG.get("bot_open_id") and not DRY:
    sys.stderr.write("配置缺少 bot_open_id：请用户本人确认机器人 open_id 后填写；拒绝启动\n"); sys.exit(2)
BASE = os.path.abspath(CFG.get("base_dir") or os.path.dirname(os.path.abspath(CFG_PATH)))
KB = os.path.join(BASE, CFG.get("kb_dir", "kb"))
STATE = os.path.join(BASE, "state"); LOGD = os.path.join(BASE, "logs"); TMP = os.path.join(BASE, "tmp")
for d in (STATE, LOGD, TMP): os.makedirs(d, exist_ok=True)
for k in ("NO_PROXY", "no_proxy"): os.environ[k] = "open.feishu.cn,.feishu.cn,.larksuite.com,localhost,127.0.0.1"
FEAT = CFG.get("features", {})
HELP_RX = re.compile(CFG.get("help_pattern", r"你(能|会|可以)(干|做|帮)(什么|啥)|你有(什么|哪些|啥)(权限|功能|能力)|^有(什么|哪些|啥)(权限|功能)[?？]?$|你是谁|怎么用你"))
REDACT_NAMES = guard.load_names(os.path.join(BASE, CFG.get("redact_file", "redact.txt")))
GMEM = memory.GroupMemory(CFG, BASE, REDACT_NAMES)  # 长期记忆（群档案），默认关闭
POLICY_IN, POLICY_OUT, POLICY_PROG = (guard.policy_rules(CFG, st) for st in ("input", "output", "progress"))  # 话题红线：默认空 = 从不拦截

log = logging.getLogger(CFG["name"]); log.setLevel(logging.INFO)
_h = RotatingFileHandler = __import__("logging.handlers", fromlist=["RotatingFileHandler"]).RotatingFileHandler
_fh = _h(os.path.join(LOGD, "bot.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); log.addHandler(_fh)
log.addHandler(logging.StreamHandler(sys.stdout))

# ---------- 单实例锁 + 冲突检查 ----------
LOCK = os.path.join(STATE, "bot.lock")
def _pid_alive(pid):
    if os.name == "nt":
        # 用 Win32 API，不解析 tasklist 输出（中文系统 GBK 输出在 UTF-8 模式下会解码失败，曾导致看门狗重启崩溃）
        import ctypes
        k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return False
        code = ctypes.c_ulong(); ok = k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try: os.kill(pid, 0); return True
    except OSError: return False
def acquire_lock():
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.write(fd, str(os.getpid()).encode()); os.close(fd)
        atexit.register(lambda: os.path.exists(LOCK) and open(LOCK).read().strip() == str(os.getpid()) and os.remove(LOCK))
        log.info("lock acquired pid %s", os.getpid()); return
    except FileExistsError:
        old = open(LOCK).read().strip()
        if old and old.isdigit() and int(old) != os.getpid() and _pid_alive(int(old)):
            log.error("REFUSED: already running pid %s (lock %s)", old, LOCK); sys.exit(3)
        log.warning("stale lock (pid %s), taking over", old); os.remove(LOCK); return acquire_lock()
def check_conflicts():
    # 同一应用只能有一个 consumer：配置里列出的旧版 pid 文件若对应进程还活着，就拒绝启动
    for pf in CFG.get("conflict_pidfiles", []):
        if os.path.exists(pf):
            p = open(pf).read().strip()
            if p.isdigit() and _pid_alive(int(p)):
                log.error("REFUSED: conflicting instance still running (pid %s from %s)", p, pf); sys.exit(4)

# ---------- 外部命令 ----------
def lark_exe():
    if os.name == "nt":
        # 直接调原生 exe，绕过 lark-cli.cmd：经 cmd.exe 转发时参数里的 " & | % 会被当成命令执行（命令注入）
        exe = os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@larksuite", "cli", "bin", "lark-cli.exe")
        return [exe] if os.path.isfile(exe) else [os.path.join(os.environ["APPDATA"], "npm", "lark-cli.cmd")]
    return ["lark-cli"]
def agent_cmd():
    if os.name != "nt": return ["cursor-agent"]
    root = os.path.join(os.environ["LOCALAPPDATA"], "cursor-agent", "versions")
    vs = [d for d in glob.glob(os.path.join(root, "*")) if os.path.isfile(os.path.join(d, "node.exe")) and os.path.isfile(os.path.join(d, "index.js"))]
    v = sorted(vs, key=os.path.getmtime)[-1]  # 跳过正在下载、不完整的新版本目录
    return [os.path.join(v, "node.exe"), os.path.join(v, "index.js")]
TOOLS = Tools(CFG, BASE, lark_exe, log, dry=DRY)

# ---------- 状态 ----------
DB = sqlite3.connect(os.path.join(STATE, "state.db"), check_same_thread=False); DBL = threading.Lock()
with DBL:
    DB.execute("create table if not exists seen(mid text primary key, ts real)")
    DB.execute("create table if not exists turns(sess text, ts real, role text, text text)")
    DB.execute("create index if not exists turns_sess on turns(sess, ts)"); DB.commit()
def seen_or_mark(mid):
    with DBL:
        try: DB.execute("insert into seen values(?,?)", (mid, time.time())); DB.commit(); return False
        except sqlite3.IntegrityError: return True
def session_key(ev):
    if ev.get("chat_type") == "p2p": return "p2p:" + (ev.get("chat_id") or ev.get("sender_id") or "?")
    return ev.get("thread_id") or ev.get("root_id") or ev.get("message_id")
def history(sess):
    m = CFG.get("memory", {}); n = int(m.get("max_turns", 6)); ttl = float(m.get("ttl_hours", 24)) * 3600
    if n <= 0 or not FEAT.get("memory", True): return []
    with DBL:
        rows = DB.execute("select role,text from turns where sess=? and ts>? order by ts desc limit ?", (sess, time.time() - ttl, n * 2)).fetchall()
    return rows[::-1]
def remember(sess, role, text):
    with DBL: DB.execute("insert into turns values(?,?,?,?)", (sess, time.time(), role, text[:2000])); DB.commit()
def forget(sess):
    with DBL: DB.execute("delete from turns where sess=?", (sess,)); DB.commit()

# ---------- 限流 + 并发 ----------
RL = CFG.get("rate_limit", {}); RLL = threading.Lock(); last_user = {}; hour_hits = collections.deque()
MAXC = int(CFG.get("max_concurrency", 2)); SEM = threading.BoundedSemaphore(MAXC); QUEUE_MAX = int(CFG.get("max_queue", 4))
_active = 0; _waiting = 0; _peak = 0; _AL = threading.Lock()
def rate_ok(uid):
    now = time.time()
    with RLL:
        while hour_hits and now - hour_hits[0] > 3600: hour_hits.popleft()
        if len(hour_hits) >= RL.get("max_per_hour", 30): return False, "hour"
        if now - last_user.get(uid, 0) < RL.get("min_gap_per_user", 20): return False, "user"
        last_user[uid] = now; hour_hits.append(now); return True, ""

# ---------- 判定 ----------
def is_for_me(ev):
    for m in ev.get("mentions") or []:
        ids = m.get("id"); vals = list(ids.values()) if isinstance(ids, dict) else [ids]
        vals += [m.get("open_id"), m.get("user_id")]
        if CFG["bot_open_id"] in [v for v in vals if v]: return True
    return False
def clean_q(t):
    t = re.sub(r"@_user_\d+", "", t or "")
    for n in CFG.get("bot_names", []): t = re.sub("@" + re.escape(n) + r"\S*", "", t)
    return t.strip()

# ---------- agent ----------
# 只是模型名字。谁的 Cursor 账号、剩多少额度，由这台电脑上执行过 cursor-agent login 的人决定。
DEFAULT_MODEL = "grok-4.7-high-fast"

class PartialText(str):
    """cursor-agent 卡死/超过防卡死上限被终止时，已经输出的那部分文本（可能为空）"""
    pass

class AgentStream:
    """解析 cursor-agent --output-format stream-json --stream-partial-output，还原出与 --output-format text 完全相同的最终文本。
    text 模式只输出「最后一次工具调用开始之后」的助手文本；result 事件里的 result 含工具调用前的开场白，不能直接用。
    partial 模式下每个增量一条 assistant 事件（带 timestamp_ms、无 model_call_id）；工具调用前（带 model_call_id）
    和结束时（无 timestamp_ms）各有一条汇总冲刷事件，要跳过，否则文本会重复。"""
    def __init__(self, on_stage=None):
        self.all = ""; self.cur = ""; self.prefix_len = 0; self.pending = None; self.seg = ""
        self.result = None; self.events = 0; self.tools = 0; self.on_stage = on_stage
    def _stage(self, txt):
        if self.on_stage and txt:
            try: self.on_stage(txt)
            except Exception: pass
    def _add(self, t): self.all += t; self.cur += t; self.seg += t
    def _resolve(self, boundary):
        if self.pending is None: return
        t = self.pending; self.pending = None
        if not boundary: self._add(t)
    def feed(self, line):
        line = (line or "").strip()
        if not line.startswith("{"): return
        try: e = json.loads(line)
        except Exception: return
        if not isinstance(e, dict): return
        self.events += 1; typ = e.get("type"); sub = e.get("subtype")
        if typ == "assistant":
            parts = (e.get("message") or {}).get("content") or []
            t = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if "model_call_id" in e or "timestamp_ms" not in e:  # 汇总冲刷
                self._resolve(True); self.seg = ""; return
            self._resolve(False)
            if self.seg and t == self.seg: self.pending = t  # 可能是冲刷，等下一条事件再定
            else: self._add(t)
            if self.tools: self._stage("正在整理答案…")
            return
        boundary = (typ == "tool_call" and sub == "started") or typ in ("retry", "interaction_query", "result")
        self._resolve(boundary)
        if boundary: self.seg = ""
        if typ == "tool_call" and sub == "started":
            self.tools += 1; self.cur = ""; self.prefix_len = len(self.all)
            self._stage(self.describe_tool(e.get("tool_call") or {}))
        elif typ == "retry" and sub == "starting" and not e.get("is_resume"):
            self.cur = ""; self.all = ""; self.prefix_len = 0  # 与 text 模式一致：非续传重试清空已输出文本
        elif typ == "thinking":
            if self.on_stage:
                try: self.on_stage(None, think=e.get("text") or "", end=(sub == "completed"))
                except Exception: pass
            if not self.cur: self._stage("正在思考…")
        elif typ == "result":
            self.result = e
    @staticmethod
    def describe_tool(tc):
        k = next((x for x in tc if str(x).endswith("ToolCall")), "")
        args = (tc.get(k) or {}).get("args") or {}
        if k == "readToolCall":
            p = str(args.get("path") or args.get("targetFile") or "")
            name = os.path.splitext(os.path.basename(p.replace("\\", "/")))[0]
            return ("正在读《%s》…" % name) if name else "正在读资料…"
        if k in ("globToolCall", "grepToolCall", "lsToolCall", "semSearchToolCall", "codebaseSearchToolCall"): return "正在查知识库…"
        if k in ("webSearchToolCall", "webFetchToolCall"): return "正在查网上资料…"
        return "正在查资料…"
    def final_text(self):
        self._resolve(True)
        if self.result is not None:
            r = self.result.get("result") or ""; pre = self.all[:self.prefix_len]
            if r.startswith(pre): return r[self.prefix_len:]
        return self.cur
    def partial_text(self):
        self._resolve(True); return self.cur

def kill_tree(p):
    try:
        if os.name == "nt": subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else: p.kill()
    except Exception as e: log.warning("kill_tree %s: %s", p.pid, e)

def stream_agent(cmd, on_stage=None):
    """流式跑 cursor-agent。正常流程不设作答时限；只有 stall_sec 秒没有任何新输出（卡死）或总时长超过 max_runtime_sec（防卡死上限）才终止。
    返回 (rc, final_text_or_None, stderr, stalled_reason, stream)"""
    import queue
    a = CFG.get("agent", {})
    max_rt = float(a.get("max_runtime_sec", 1800)); stall = float(a.get("stall_sec", 300))
    p = subprocess.Popen(cmd, cwd=KB, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    q = queue.Queue(); err = []
    def rd():
        try:
            for line in p.stdout: q.put(line)
        finally: q.put(None)
    threading.Thread(target=rd, daemon=True).start()
    threading.Thread(target=lambda: err.extend(p.stderr), daemon=True).start()
    st = AgentStream(on_stage); t0 = last = time.time(); why = ""
    while True:
        try: line = q.get(timeout=1)
        except Exception: line = ""
        now = time.time()
        if line is None: break
        if line: last = now; st.feed(line)
        if now - last > stall: why = "stall %.0fs no output" % (now - last)
        elif now - t0 > max_rt: why = "max_runtime %.0fs" % max_rt
        if why: kill_tree(p); break
    try: rc = p.wait(timeout=30)
    except Exception: rc = -1
    time.sleep(0.2)
    if why: return rc, None, "".join(err), why, st
    return rc, st.final_text(), "".join(err), "", st

def run_agent(prompt, on_stage=None):
    a = CFG.get("agent", {})
    if FAKE:
        if on_stage: on_stage("正在思考…")
        time.sleep(float(FAKE))
        if os.environ.get("AGENTBOT_FAKE_DUMP_PROMPT"): log.info("fake prompt: %s", json.dumps(prompt, ensure_ascii=False))  # 仅测试：检查 prompt 拼装
        m = re.search(r"<<FAKE_TOOL>>(\{.*?\})<<END>>", prompt)
        if m and "工具结果（" not in prompt: return "<<TOOL>>" + m.group(1)
        return "[fake] " + prompt.rsplit("提问：", 1)[-1][:200]
    base_cmd = json.loads(os.environ["AGENTBOT_AGENT_CMD"]) if os.environ.get("AGENTBOT_AGENT_CMD") else agent_cmd()
    model_id = (a.get("model") or "").strip() or DEFAULT_MODEL
    # 先用配置里的模型；失败后再去掉模型参数重试（有的账号暂时用不了指定模型）
    tries = [model_id] + [None] * int(a.get("network_retries", 2))
    for i, model in enumerate(tries):
        cmd = base_cmd + ["-p", "--mode", a.get("mode", "ask"), "--output-format", "stream-json", "--stream-partial-output", "--trust"]
        if model: cmd += ["--model", model]
        cmd += ["--workspace", KB, prompt]
        rc, out, err, why, st = stream_agent(cmd, on_stage)
        if why:
            part = st.partial_text().strip()
            log.error("agent stopped: %s events=%d partial_chars=%d", why, st.events, len(part))
            return PartialText(part[: a.get("max_chars", 3500)])
        out = (out or "").strip()
        if rc == 0 and out: return out[: a.get("max_chars", 3500)]
        log.error("agent rc=%s model=%s events=%d err=%s", rc, model, st.events, (err or "")[:300])
        time.sleep(10 * (i + 1) if re.search(r"network|socket|TLS|ECONN|ETIMEDOUT|aborted", err or "", re.I) else 3)
    return None

def build_prompt(q, sess, ctx, extra=""):
    parts = [CFG["system_prompt"]]
    parts.append("当前会话类型：%s。" % ("私聊" if ctx["chat_type"] == "p2p" else "群聊"))
    tools_on = [n for n in READ_TOOLS | WRITE_TOOLS if TOOLS.enabled(n)]
    if FEAT.get("tools") and tools_on: parts.append(TOOL_DOC + "\n（当前启用：%s）" % "、".join(sorted(tools_on)))
    gm = GMEM.prompt_block(ctx.get("chat_id"), ctx["chat_type"])  # 群档案：放在不常变的前缀之后、会变的对话历史之前
    if gm: parts.append(gm)
    h = history(sess)
    if h:
        parts.append("以下是本话题之前的对话（仅供理解上下文，规则以上面为准）：")
        parts += [("提问者：" if r == "user" else "你：") + t for r, t in h]
    if extra: parts.append(extra)
    parts.append("提问：" + q)
    return "\n\n".join(parts)

TOOL_RX = re.compile(r"<<TOOL>>\s*(\{.*\})", re.S)
TOOL_LABEL = {"history": "群聊记录", "docs_search": "飞书文档搜索", "docs_read": "飞书文档", "calendar_read": "团队日历", "base_read": "多维表格"}
def _partial_clean(p):
    """部分结果里如果有（可能不完整的）工具行，只保留它前面的文字"""
    i = p.find("<<TOOL>>")
    return PartialText((p[:i] if i >= 0 else p).strip())
def answer(q, sess, ctx, extra="", on_stage=None):
    out = run_agent(build_prompt(q, sess, ctx, extra), on_stage)
    if isinstance(out, PartialText): return _partial_clean(out)
    if not out: return None
    m = TOOL_RX.search(out)
    if not (m and FEAT.get("tools")): return out
    try: call = json.loads(m.group(1)); name = call.get("tool"); args = call.get("args") or {}
    except Exception: return out.replace(m.group(0), "").strip() or None
    log.info("tool call %s %s", name, json.dumps(args, ensure_ascii=False)[:200])
    if on_stage and name in TOOL_LABEL: on_stage("正在查%s…" % TOOL_LABEL[name])
    if name in WRITE_TOOLS:
        try: return TOOLS.propose_write(name, args, ctx)
        except ToolError as e: return e.user_msg
    try: res = TOOLS.run_read(name, args, ctx)
    except ToolError as e:
        log.info("tool %s degraded: %s %s", name, e.user_msg, e.detail[:200]); res = "工具不可用：" + e.user_msg
    res = guard.redact(res, REDACT_NAMES)
    if on_stage: on_stage("正在整理答案…")
    out2 = run_agent(build_prompt(q, sess, ctx, extra + "\n\n工具结果（%s）：\n%s\n\n请根据工具结果直接回答，不要再调用工具；如果工具不可用，就如实告诉提问者原因。" % (name, res[:9000])), on_stage)
    if isinstance(out2, PartialText): return _partial_clean(out2)
    if out2 and TOOL_RX.search(out2): out2 = TOOL_RX.sub("", out2).strip()
    return out2 or ("工具不可用：" + res if res.startswith("工具不可用") else None)

# ---------- 附件 ----------
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".tsv"}; IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
def attachment_context(msg_id, ctx):
    """返回 (extra_prompt, cleanup_fn)；无权限/未启用时抛 ToolError"""
    d, files, mtype = TOOLS.fetch_attachments(msg_id, ctx, TMP)
    lim = int(CFG.get("tools", {}).get("attachment", {}).get("max_mb", 20)) * 1024 * 1024
    parts, img_dir = [], None
    for f in files[:5]:
        ext = os.path.splitext(f)[1].lower(); name = os.path.basename(f)
        if os.path.getsize(f) > lim: parts.append("附件 %s 太大，已跳过。" % name); continue
        if ext in TEXT_EXT:
            parts.append("附件《%s》内容：\n%s" % (name, open(f, encoding="utf-8", errors="replace").read()[:8000]))
        elif ext == ".pdf":
            try:
                import pypdf; txt = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(f).pages[:20])
                parts.append("附件《%s》（PDF 文本）：\n%s" % (name, txt[:8000]))
            except Exception: parts.append("附件《%s》是 PDF，当前无法解析文本。" % name)
        elif ext in IMG_EXT and FEAT.get("vision", True):
            img_dir = img_dir or os.path.join(KB, ".attach", os.path.basename(d)); os.makedirs(img_dir, exist_ok=True)
            shutil.copy(f, img_dir); parts.append("用户发来一张图片，已放在工作区 .attach/%s/%s，请查看后回答。" % (os.path.basename(d), name))
        else: parts.append("附件《%s》的类型暂不支持识别。" % name)
    def cleanup():
        for p in (d, img_dir):
            if p: shutil.rmtree(p, ignore_errors=True)
    if not files:
        return ("（附件没有取到内容。）" if mtype in ("image", "file", "media") else ""), cleanup
    return "\n\n".join(parts), cleanup

# ---------- 回复 ----------
def reply(mid, text):
    text = guard.redact(text, REDACT_NAMES)
    if DRY: log.info("[dry-run] reply %s: %s", mid, text[:600]); return True
    fn = os.path.join(TMP, "r_%s.json" % hashlib.md5(mid.encode()).hexdigest()[:12])
    with open(fn, "w", encoding="utf-8") as f: json.dump({"text": text}, f, ensure_ascii=False)
    cmd = lark_exe() + ["im", "+messages-reply", "--profile", CFG["profile"], "--as", "bot", "--message-id", mid,
                        "--msg-type", "text", "--content", "@" + os.path.basename(fn), "--idempotency-key", ("re_" + mid)[:50]]
    if CFG.get("reply_in_thread", True): cmd.insert(cmd.index("--msg-type"), "--reply-in-thread")
    ok = False; out = ""
    for i in range(4):
        try: r = run_capture(cmd, cwd=TMP, timeout=90)
        except subprocess.TimeoutExpired: r = subprocess.CompletedProcess(cmd, -1, "", "timeout")
        out = (r.stdout or "") + (r.stderr or "")
        try: ok = bool(json.loads((r.stdout or "{}")[(r.stdout or "{}").find("{"):]).get("ok"))
        except Exception: ok = '"ok": true' in out
        if ok or not re.search(r"unreachable|EOF|timeout|connection", out, re.I): break
        log.warning("reply retry %d: %s", i + 1, out[:200]); time.sleep(5 * (i + 1))
    log.info("reply %s ok=%s %s", mid, ok, "" if ok else out[:300])
    try: os.remove(fn)
    except OSError: pass
    return ok

# ---------- 进度（表情回应 + 可编辑的状态消息）----------
def lark_api(argv, payload=None, timeout=30):
    """调 lark-cli（bot 身份），payload 写临时文件以 @file 传入。失败返回 (None, 错误文本)，不抛异常。"""
    fn = None
    try:
        if payload is not None:
            fn = os.path.join(TMP, "p_%s.json" % hashlib.md5((json.dumps(payload, ensure_ascii=False) + str(time.time()) + str(threading.get_ident())).encode()).hexdigest()[:12])
            with open(fn, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False)
            argv = [("@" + os.path.basename(fn)) if x == "@PAYLOAD" else x for x in argv]
        r = run_capture(lark_exe() + list(argv) + ["--profile", CFG["profile"], "--as", "bot"], cwd=TMP, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        try: j = json.loads((r.stdout or "")[(r.stdout or "").find("{"):])
        except Exception: j = {}
        return (j if j.get("ok") else None), ("" if j.get("ok") else out[-600:])
    except Exception as e:
        return None, str(e)
    finally:
        if fn:
            try: os.remove(fn)
            except OSError: pass
def _find_key(j, key):
    if isinstance(j, dict):
        if isinstance(j.get(key), str): return j[key]
        for v in j.values():
            r = _find_key(v, key)
            if r: return r
    elif isinstance(j, list):
        for v in j:
            r = _find_key(v, key)
            if r: return r
    return None
_SCOPE_RX = re.compile(r"99991672|99991679|scope|permission denied|access denied|无权限|权限", re.I)
PROG_OFF = {}  # 本进程内因缺权限关闭的能力 -> 缺的 scope
def _prog_fail(kind, scope, err):
    if _SCOPE_RX.search(err or ""):
        if kind not in PROG_OFF: log.warning("progress %s disabled: missing scope %s | %s", kind, scope, (err or "")[-300:])
        PROG_OFF[kind] = scope
    else: log.info("progress %s failed (skipped): %s", kind, (err or "")[-300:])

def fmt_secs(x):
    x = int(x)
    return "%d 秒" % x if x < 60 else "%d 分 %d 秒" % (x // 60, x % 60)

_PATHLIKE = re.compile(r"(?:[A-Za-z]:)?[\w.\-\u4e00-\u9fa5·（）()]*[\\/][^\s，。；、：）)》」\"'`]*|[\w\-\u4e00-\u9fa5·（）()]+\.(?:md|txt|json|csv|tsv|pdf|docx?|xlsx?|pptx?|log)\b")
def _title_of(m):
    t = m.group(0).replace("\\", "/").rstrip("/").split("/")[-1]
    stem = os.path.splitext(t)[0]
    return ("《%s》" % stem) if stem and stem not in (".", "..") else "资料"
def sanitize_progress(text):
    """进度/思考文字：路径和文件名只留文档标题，客户名脱敏；配置了 policy.redlines 时命中的行整行丢弃"""
    text = _PATHLIKE.sub(_title_of, text or "")
    text = re.sub(r"《《([^》]*)》》", r"《\1》", text)
    text = guard.redact(text, REDACT_NAMES)
    return "\n".join(l for l in text.splitlines() if not guard.policy_hit(l, POLICY_PROG)).strip()

class Progress:
    """收到问题：给原消息加「处理中」表情，并回复一条状态卡片（mode=card，默认收起的「思考过程」面板）或文本状态消息（mode=text）；
    作答期间按阶段/定时更新（含已用时间）。答完：
      card：答案照常以文本回复（格式和以前完全一致），卡片改成「已完成，用时 X」；
      text：状态消息直接替换成答案，替换失败就另发答案、状态改成「已完成，用时 X」。
    表情换成完成表情。任何一步失败都静默降级（卡片失败→文本状态→只有表情），不影响作答。"""
    def __init__(self, mid):
        self.c = dict({"enabled": False, "mode": "card", "reaction": "OnIt", "done_reaction": "DONE", "status_message": True,
                       "fast_interval_sec": 15, "fast_phase_sec": 120, "slow_interval_sec": 60, "min_gap_sec": 10,
                       "max_edits": 17, "card_interval_sec": 12, "card_max_updates": 150, "thinking_panel": True, "thinking_chars": 1200},
                      **CFG.get("progress", {}))
        self.mid = mid; self.t0 = time.time(); self.stage = "正在思考…"; self.shown = None; self.mode = None
        self.status_id = None; self.reaction_id = None; self.edits = 0; self.last_edit = 0; self.done = threading.Event(); self.lk = threading.Lock()
        self.elk = threading.Lock(); self._stage_changed = False; self.thinking = ""; self._think_new = False
    def on(self): return bool(self.c.get("enabled", False))  # 默认关闭：只有在配置里写了 progress.enabled=true 的智能体才启用
    def render(self, stage=None):
        st = sanitize_progress(stage or self.stage) or "正在处理…"  # 文档标题也做客户名脱敏
        return "⏳ %s（已用 %s）" % (st, fmt_secs(time.time() - self.t0))
    def card(self, head):
        els = [{"tag": "div", "text": {"tag": "plain_text", "content": head}}]
        if self.c.get("thinking_panel", True):
            with self.lk: raw = self.thinking
            th = sanitize_progress(raw)[-int(self.c["thinking_chars"]):] or "（暂无）"
            els.append({"tag": "collapsible_panel", "expanded": False,
                        "header": {"title": {"tag": "plain_text", "content": "思考过程"}},
                        "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": th}}]})
        return {"schema": "2.0", "config": {"update_multi": True, "summary": {"content": head[:60]}}, "body": {"elements": els}}
    def _send_status(self, mode, txt):
        if DRY:
            log.info("[dry-run] status(%s) %s: %s", mode, self.mid, txt)
            if mode == "card": log.info("[dry-run] card %s: %s", self.mid, json.dumps(self.card(txt), ensure_ascii=False)[:800])
            return "dry_status_" + self.mid
        cmd = ["im", "+messages-reply", "--message-id", self.mid, "--msg-type", "interactive" if mode == "card" else "text",
               "--content", "@PAYLOAD", "--idempotency-key", ("st_" + self.mid)[:50]]
        if CFG.get("reply_in_thread", True): cmd.insert(cmd.index("--msg-type"), "--reply-in-thread")
        j, err = lark_api(cmd, self.card(txt) if mode == "card" else {"text": txt}, timeout=60)
        if not j: _prog_fail("status_" + mode, "im:message:send_as_bot", err)
        return _find_key(j, "message_id") if j else None
    def start(self):
        if not self.on(): return
        emo = self.c.get("reaction")
        if emo and "reaction" not in PROG_OFF:
            if DRY: log.info("[dry-run] react %s %s", self.mid, emo); self.reaction_id = "dry_reaction"
            else:
                j, err = lark_api(["im", "reactions", "create", "--message-id", self.mid, "--data", "@PAYLOAD"], {"reaction_type": {"emoji_type": emo}})
                self.reaction_id = _find_key(j, "reaction_id") if j else None
                if not j: _prog_fail("reaction", "im:message.reactions:write_only（或 im:message）", err)
        if not self.c.get("status_message", True): return
        txt = self.render()
        modes = ["card", "text"] if self.c.get("mode", "card") == "card" else ["text"]
        for m in modes:
            if ("status_" + m) in PROG_OFF: continue
            self.status_id = self._send_status(m, txt)
            if self.status_id: self.mode = m; break
        if self.status_id:
            self.shown = txt; self.last_edit = time.time()
            log.info("status %s mode=%s id=%s", self.mid, self.mode, self.status_id)
            threading.Thread(target=self._ticker, daemon=True).start()
    def set_stage(self, stage):
        with self.lk:
            if stage and stage != self.stage: self.stage = stage; self._stage_changed = True
    def add_thinking(self, t, end=False):
        with self.lk:
            self.thinking = (self.thinking + (t or "") + ("\n" if end else ""))[-4000:]; self._think_new = True
    def _edit(self, text, final=False):
        """text 模式：编辑文本消息；card 模式：更新卡片（text 作为卡片头部状态行）"""
        if not self.status_id: return False
        kind = "patch" if self.mode == "card" else "edit"
        if kind in PROG_OFF and not final: return False
        if DRY:
            log.info("[dry-run] %s %s: %s", kind, self.status_id, text[:300])
            if self.mode == "card": log.info("[dry-run] card %s: %s", self.status_id, json.dumps(self.card(text), ensure_ascii=False)[:800])
            self.edits += 1; return True
        if self.mode == "card":
            j, err = lark_api(["im", "messages", "patch", "--message-id", self.status_id, "--data", "@PAYLOAD"],
                              {"content": json.dumps(self.card(text), ensure_ascii=False)}, timeout=60)
        else:
            j, err = lark_api(["im", "+messages-edit", "--message-id", self.status_id, "--msg-type", "text", "--content", "@PAYLOAD"], {"text": text}, timeout=60)
        if j: self.edits += 1; return True
        _prog_fail(kind, "im:message:update（或 im:message）", err); return False
    def _ticker(self):
        while not self.done.wait(1):
            now = time.time(); el = now - self.t0; card = self.mode == "card"
            kind = "patch" if card else "edit"
            # text 模式：飞书单条消息约 20 次编辑上限，留 2 次给最终答案；card 模式按卡片更新频控放宽
            if self.edits >= int(self.c["card_max_updates"] if card else self.c["max_edits"]) or kind in PROG_OFF: return
            interval = self.c["card_interval_sec"] if card else (self.c["fast_interval_sec"] if el < self.c["fast_phase_sec"] else self.c["slow_interval_sec"])
            with self.lk: changed = self._stage_changed or (card and self._think_new)
            gap = now - self.last_edit
            if (changed and gap >= self.c["min_gap_sec"]) or gap >= interval:
                with self.lk: self._stage_changed = False; self._think_new = False
                with self.elk:  # 与最终替换互斥，避免进度更新晚于答案落地把答案盖掉
                    txt = self.render()
                    if (txt != self.shown or card) and not self.done.is_set():
                        if self._edit(txt): self.shown = txt
                self.last_edit = time.time()
    def finish(self, answer_text):
        """返回 True 表示答案已经通过替换状态消息发出（仅 text 模式），调用方不必再回复"""
        self.done.set(); ok = False
        if not self.on(): return False
        if self.status_id and self.mode == "text":
            with self.elk: ok = self._edit(guard.redact(answer_text, REDACT_NAMES), final=True)
            log.info("status %s -> answer ok=%s edits=%d", self.status_id, ok, self.edits)
        self._swap_reaction()
        return ok
    def mark_done_after_reply(self, note=""):
        if self.status_id:
            with self.elk: self._edit("✅ 已完成，用时 %s%s" % (fmt_secs(time.time() - self.t0), note), final=True)
    def _swap_reaction(self):
        if self.reaction_id:
            if DRY: log.info("[dry-run] unreact %s %s", self.mid, self.reaction_id)
            else:
                j, err = lark_api(["im", "reactions", "delete", "--message-id", self.mid, "--reaction-id", self.reaction_id])
                if not j: log.info("progress unreact failed (skipped): %s", err[-200:])
        done = self.c.get("done_reaction")
        if done and self.reaction_id and "reaction" not in PROG_OFF:  # 能加处理中表情时才换完成表情
            if DRY: log.info("[dry-run] react %s %s", self.mid, done)
            else:
                j, err = lark_api(["im", "reactions", "create", "--message-id", self.mid, "--data", "@PAYLOAD"], {"reaction_type": {"emoji_type": done}})
                if not j: log.info("progress done-reaction failed (skipped): %s", err[-200:])

def probe_progress():
    """--check --probe：不发任何消息地确认表情/编辑/卡片权限。用 state.db 里最近一条用户消息做探针（只做必然失败但能区分权限的调用）。"""
    with DBL: row = DB.execute("select mid from seen order by ts desc limit 1").fetchone()
    res = {}
    if row:
        mid = row[0]
        j, err = lark_api(["im", "reactions", "delete", "--message-id", mid, "--reaction-id", "probe_invalid_reaction_id"])
        res["reaction"] = (not _SCOPE_RX.search(err or "")) and ("231011" in (err or "") or bool(j)), "im:message.reactions:write_only", err
        j, err = lark_api(["im", "+messages-edit", "--message-id", mid, "--msg-type", "text", "--content", "@PAYLOAD"], {"text": "probe"})
        res["edit"] = (not _SCOPE_RX.search(err or "")) and "230071" in (err or ""), "im:message:update", err
        j, err = lark_api(["im", "messages", "patch", "--message-id", mid, "--data", "@PAYLOAD"], {"content": "{}"})
        res["patch"] = (not _SCOPE_RX.search(err or "")) and not j, "im:message:update", err
    else: res["reaction"] = res["edit"] = res["patch"] = (False, "（state.db 里没有消息可做探针）", "")
    p = Progress("probe"); p.thinking = "探针：正在读 C:\\x\\kb\\示例.md"
    j, err = lark_api(["api", "POST", "/open-apis/cardkit/v1/cards", "--data", "@PAYLOAD"],
                      {"type": "card_json", "data": json.dumps(p.card(p.render()), ensure_ascii=False)})
    res["card"] = bool(j), "cardkit:card:write（卡片 JSON 校验）", err
    return res

CONFIRM_RX = re.compile(r"^(确认|取消)\s*(\d{6})$")
def handle(ev):
    global _active, _waiting, _peak
    mid = ev.get("message_id") or ev.get("id")
    if not mid: return
    if (ev.get("sender_type") or "") in ("app", "bot"): return
    ctype = ev.get("chat_type") or "group"
    if ctype == "p2p":
        if not FEAT.get("p2p"): return
        # 私聊范围：p2p.allow_open_ids 优先；为空时 p2p.open_to_all=true 对所有人开放，否则只放行写入白名单里的成员
        pc = CFG.get("p2p", {}); allow = pc.get("allow_open_ids")
        if not allow and not pc.get("open_to_all", False): allow = list((CFG.get("write", {}).get("allowed_users") or {}).keys())
        if allow and ev.get("sender_id") not in allow: return
    else:
        if ev.get("chat_id") not in CFG["chat_ids"] or not is_for_me(ev): return
    if seen_or_mark(mid): return
    uid = ev.get("sender_id") or "?"; sess = session_key(ev); mtype = ev.get("message_type") or "text"
    ctx = {"user": uid, "chat_id": ev.get("chat_id"), "chat_type": ctype, "message_id": mid}
    q = clean_q(ev.get("content") or "")
    log.info("Q %s %s/%s sess=%s from %s: %s", mid, ctype, mtype, sess, uid, q[:200])
    # 命令
    m = CONFIRM_RX.match(q)
    if m:
        reply(mid, TOOLS.confirm(m.group(2), ctx, cancel=(m.group(1) == "取消"))); return
    if q in ("/new", "/reset", "新话题"): forget(sess); reply(mid, "好的，已清空本话题的上下文。"); return
    mr = GMEM.handle(q, ctx)  # 记忆命令：/记忆、记一下/记住/备忘、/忘记 N、/忘记全部、确认清空（不调 agent）
    if mr is not None: reply(mid, mr); return
    if q in ("/help", "帮助") or (len(q) <= 30 and HELP_RX.search(q)):  # 问能力/权限：直接列能力和边界，不调 agent、不翻资料
        reply(mid, CFG["texts"].get("help", CFG["texts"]["empty"])); return
    hit = guard.policy_hit(q, POLICY_IN)  # 仅当配置了 policy.redlines 才可能命中
    if hit and hit.get("action", "refuse") == "refuse":
        reply(mid, hit.get("reply") or CFG["texts"].get("policy_refuse") or CFG["texts"].get("taboo") or "这个话题我不参与讨论。"); return
    ok, why = rate_ok(uid)
    if not ok:
        if why == "hour": reply(mid, CFG["texts"]["busy"])
        return
    # 附件：私聊直接发的图片/文件，或 @我 时回复/引用了一条图片/文件消息
    att_src = mid if mtype in ("image", "file", "post", "media") and ctype == "p2p" else (ev.get("reply_to") or ev.get("parent_id"))
    if not q and mtype in ("image", "file", "media"): q = "请看我发的附件，概括它的内容。"
    if not q: reply(mid, CFG["texts"]["empty"]); return
    with _AL:
        full = _waiting >= QUEUE_MAX
        if not full: _waiting += 1
    if full: log.warning("queue full, skip %s", mid); reply(mid, CFG["texts"]["busy"]); return
    extra, cleanup = "", None
    prog = Progress(mid)
    with _AL:
        if _active >= MAXC: prog.stage = "排队中，前面还有问题在处理…"
    try: prog.start()
    except Exception as e: log.warning("progress start error (ignored): %s", e)
    with SEM:
        with _AL: _waiting -= 1; _active += 1; _peak = max(_peak, _active); act = _active
        log.info("agent start %s active=%d peak=%d", mid, act, _peak); t0 = time.time(); prog.set_stage("正在思考…")
        try:
            if att_src and FEAT.get("attachments") and (mtype != "text" or ev.get("reply_to") or ev.get("parent_id")):
                try: extra, cleanup = attachment_context(att_src, ctx)
                except ToolError as e:
                    log.info("attachment degraded: %s %s", e.user_msg, e.detail[:200])
                    if mtype != "text": extra = "（附件读取失败：%s。请如实告诉提问者。）" % e.user_msg
            def hook(stage, think=None, end=False):
                if think is not None: prog.add_thinking(think, end)
                elif stage: prog.set_stage(stage)
            a = answer(q, sess, ctx, extra, on_stage=hook)
        except Exception as e:
            log.exception("handle error: %s", e); a = None
        finally:
            with _AL: _active -= 1
            if cleanup: cleanup()
        log.info("agent done %s elapsed=%.1fs ok=%s partial=%s", mid, time.time() - t0, bool(a), isinstance(a, PartialText))
    partial = isinstance(a, PartialText)
    if a and guard.policy_hit(a, POLICY_OUT):
        a2, _ = guard.drop_policy_paragraphs(a, POLICY_OUT); a = a2 or ("" if partial else (CFG["texts"].get("policy_refuse") or CFG["texts"].get("taboo") or "这个话题我不参与讨论。"))
    if partial:
        if a: a = str(a) + "\n\n" + CFG["texts"].get("partial_note", "（时间到了，先给部分结果）")
        else: a = CFG["texts"].get("stall", "这题比较复杂，没在时间内答完，可以拆成更小的问题再问我。")
    elif a: remember(sess, "user", q); remember(sess, "bot", a)
    final = a or CFG["texts"]["fail"]
    try: replaced = prog.finish(final)
    except Exception as e: log.warning("progress finish error (ignored): %s", e); replaced = False
    if replaced: log.info("reply %s ok=True via status edit", mid); return
    reply(mid, final)
    try: prog.mark_done_after_reply("（时间到了，已先给部分结果）" if partial else "")
    except Exception as e: log.warning("progress mark_done error (ignored): %s", e)

def heartbeat():
    while True:
        try: open(os.path.join(STATE, "heartbeat"), "w").write(str(int(time.time())))
        except OSError: pass
        time.sleep(60)

def self_check():
    ok = True
    def p(name, good, info=""):
        nonlocal ok; ok &= bool(good); print("%s %s %s" % ("OK  " if good else "FAIL", name, info))
    p("kb dir", os.path.isdir(KB), KB)
    try: p("cursor-agent", os.path.exists(agent_cmd()[-1]) or os.name != "nt", agent_cmd()[-1])
    except Exception as e: p("cursor-agent", False, str(e))
    p("lark-cli", shutil.which(lark_exe()[0]) or os.path.exists(lark_exe()[0]), lark_exe()[0])
    p("redact names", True, "%d 个（%s）" % (len(REDACT_NAMES), CFG.get("redact_file", "redact.txt")))
    w = CFG.get("write", {}); p("write whitelist", True, "users=%s" % list((w.get("allowed_users") or {}).values()))
    p("group memory", True, "enabled=%s budget=%d profiles=%s" % (GMEM.enabled, GMEM.budget, [c for c in CFG["chat_ids"] if GMEM.path(c) and os.path.isfile(GMEM.path(c))]))
    if "--probe" in ARGS:
        try:
            for k, (good, scope, err) in probe_progress().items():
                print("%s progress %s %s" % ("OK  " if good else "DEGR", k, "" if good else "缺 %s | %s" % (scope, (err or "")[-200:].replace("\n", " "))))
        except Exception as e: print("DEGR progress probe error %s" % e)
        ctx = {"user": "selfcheck", "chat_id": CFG["chat_ids"][0], "chat_type": "group"}
        for name, args in [("history", {}), ("calendar_read", {}), ("docs_search", {"query": "进度"})]:
            if not TOOLS.enabled(name): print("SKIP %s（未启用）" % name); continue
            try: TOOLS.run_read(name, args, ctx); print("OK   probe %s" % name)
            except ToolError as e: print("DEGR probe %s -> %s" % (name, e.user_msg))
    return ok

def main():
    if CHECK: sys.exit(0 if self_check() else 1)
    if not CFG.get("bot_open_id"): log.warning("dry-run：bot_open_id 为空")
    if LOCK_TEST is not None:
        acquire_lock(); log.info("lock-test holding %ss", LOCK_TEST or 30); time.sleep(float(LOCK_TEST or 30)); return
    if DRY:
        src = _opt("--dry-run"); f = open(src, encoding="utf-8-sig") if src else sys.stdin; ths = []
        for line in f:
            line = line.strip()
            if not line.startswith("{"): continue
            ev = json.loads(line); ev = ev.get("event", ev)
            if PARALLEL: t = threading.Thread(target=handle, args=(ev,)); t.start(); ths.append(t)
            else: handle(ev)
        for t in ths: t.join()
        log.info("dry-run finished peak_active=%d max_concurrency=%d", _peak, MAXC); return
    check_conflicts(); acquire_lock(); threading.Thread(target=heartbeat, daemon=True).start()
    log.info("starting %s (features=%s)", CFG["name"], json.dumps(FEAT, ensure_ascii=False))
    while True:
        p = subprocess.Popen(lark_exe() + ["event", "consume", "im.message.receive_v1", "--profile", CFG["profile"], "--as", "bot"],
                             cwd=BASE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        open(os.path.join(STATE, "consumer.pid"), "w").write(str(p.pid))
        threading.Thread(target=lambda: [log.info("[consume] %s", l.rstrip()) for l in p.stderr], daemon=True).start()
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"): continue
            try: ev = json.loads(line); ev = ev.get("event", ev)
            except Exception: continue
            threading.Thread(target=handle, args=(ev,), daemon=True).start()
        log.warning("consumer exited rc=%s, restart in 10s", p.wait()); time.sleep(10)

if __name__ == "__main__":
    main()
