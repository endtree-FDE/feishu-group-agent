# cursor-agent：每题一个进程（oneshot），或 ACP 常驻进程。从 agentbot 移过来，行为保持原样。
# 登录和额度仍是这台电脑上 cursor-agent login 的那个账号。这里不写账号。
import atexit, glob, json, os, re, subprocess, threading, time

import acp_client
from providers.base import PartialText, Provider

DEFAULT_MODEL = "grok-4.7-high-fast"


def agent_cmd():
    if os.name != "nt":
        return ["cursor-agent"]
    root = os.path.join(os.environ["LOCALAPPDATA"], "cursor-agent", "versions")
    vs = [d for d in glob.glob(os.path.join(root, "*")) if os.path.isfile(os.path.join(d, "node.exe")) and os.path.isfile(os.path.join(d, "index.js"))]
    v = sorted(vs, key=os.path.getmtime)[-1]  # 跳过正在下载、不完整的新版本目录
    return [os.path.join(v, "node.exe"), os.path.join(v, "index.js")]


def agent_base_cmd():
    return json.loads(os.environ["AGENTBOT_AGENT_CMD"]) if os.environ.get("AGENTBOT_AGENT_CMD") else agent_cmd()


def agent_transport(a=None):
    """oneshot：每题一个 -p 进程（默认，和以前一样）。acp：常驻进程。旧配置里的 ask/plan 仍是一次性。"""
    m = str((a or {}).get("mode") or "").strip().lower()
    return "acp" if m == "acp" else "oneshot"


def oneshot_cli_mode(a):
    """交给 cursor-agent --mode 的只读/计划模式。oneshot 和 acp 这两个新值都固定用 ask。"""
    m = str((a or {}).get("mode") or "ask").strip().lower()
    return m if m in ("ask", "plan") else "ask"


def kill_tree(p, log):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            p.kill()
    except Exception as e:
        log.warning("kill_tree %s: %s", p.pid, e)


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
    def _add(self, t):
        self.all += t; self.cur += t; self.seg += t
        if t and self.on_stage:
            try: self.on_stage(None, text=t)
            except Exception: pass
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


class CursorProvider(Provider):
    name = "cursor"

    def __init__(self, cfg, log, kb):
        self.cfg = cfg
        self.log = log
        self.kb = kb
        self._pool = None
        self._pool_lock = threading.Lock()

    def _agent(self):
        return self.cfg.get("agent") or {}

    def build_acp_cmd(self):
        """cursor-agent --model … --mode ask acp。--model / --mode / --trust 是全局参数，写在子命令 acp 前面。"""
        a = self._agent()
        cmd = list(agent_base_cmd())
        model = (a.get("model") or "").strip() or DEFAULT_MODEL
        cmd += ["--model", model, "--mode", "ask", "--trust", "--workspace", self.kb, "acp"]
        return cmd

    def pool(self):
        # 调用方可能并发。锁内建好并登记退出清理后才发布，避免起两个常驻进程。
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    a = dict(self._agent())
                    if not (a.get("model") or "").strip():
                        a["model"] = DEFAULT_MODEL
                    pool = acp_client.Pool(self.build_acp_cmd, a, self.kb, self.log)
                    atexit.register(pool.close)
                    self._pool = pool
        return self._pool

    def drop(self, sess):
        if self._pool is not None:
            self._pool.drop(sess)

    def close(self):
        if self._pool is not None:
            self._pool.close()

    def stream_agent(self, cmd, on_stage=None):
        """流式跑 cursor-agent。正常流程不设作答时限；只有 stall_sec 秒没有任何新输出（卡死）或总时长超过 max_runtime_sec（防卡死上限）才终止。
        返回 (rc, final_text_or_None, stderr, stalled_reason, stream)"""
        import queue
        a = self._agent()
        max_rt = float(a.get("max_runtime_sec", 1800)); stall = float(a.get("stall_sec", 300))
        p = subprocess.Popen(cmd, cwd=self.kb, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
            if why: kill_tree(p, self.log); break
        try: rc = p.wait(timeout=30)
        except Exception: rc = -1
        time.sleep(0.2)
        if why: return rc, None, "".join(err), why, st
        return rc, st.final_text(), "".join(err), "", st

    def oneshot(self, prompt, on_stage=None):
        a = self._agent()
        base_cmd = agent_base_cmd()
        model_id = (a.get("model") or "").strip() or DEFAULT_MODEL
        # 先用配置里的模型；失败后再去掉模型参数重试（有的账号暂时用不了指定模型）
        tries = [model_id] + [None] * int(a.get("network_retries", 2))
        cli_mode = oneshot_cli_mode(a)
        for i, model in enumerate(tries):
            cmd = base_cmd + ["-p", "--mode", cli_mode, "--output-format", "stream-json", "--stream-partial-output", "--trust"]
            if model: cmd += ["--model", model]
            cmd += ["--workspace", self.kb, prompt]
            rc, out, err, why, st = self.stream_agent(cmd, on_stage)
            if why:
                part = st.partial_text().strip()
                self.log.error("agent stopped: %s events=%d partial_chars=%d", why, st.events, len(part))
                return PartialText(part[: a.get("max_chars", 3500)])
            out = (out or "").strip()
            if rc == 0 and out: return out[: a.get("max_chars", 3500)]
            self.log.error("agent rc=%s model=%s events=%d err=%s", rc, model, st.events, (err or "")[:300])
            time.sleep(10 * (i + 1) if re.search(r"network|socket|TLS|ECONN|ETIMEDOUT|aborted", err or "", re.I) else 3)
        return None

    def complete(self, prompt, sess=None, on_stage=None):
        a = self._agent()
        if agent_transport(a) == "acp":
            try:
                out = self.pool().ask(sess or "default", prompt, on_stage)
                return (out or "")[: a.get("max_chars", 3500)] or None
            except acp_client.AcpFail as e:
                self.log.error("acp fallback: %s", e)
        return self.oneshot(prompt, on_stage)

    def check_items(self):
        items = []
        try:
            cmd = agent_cmd()
            items.append(("cursor-agent", os.path.exists(cmd[-1]) or os.name != "nt", cmd[-1]))
        except Exception as e:
            items.append(("cursor-agent", False, str(e)))
        if agent_transport(self._agent()) == "acp":
            try:
                items.append(("acp", True, acp_client.probe(self.build_acp_cmd(), self.kb, self.log)))
            except Exception as e:
                items.append(("acp", False, str(e)))
        return items
