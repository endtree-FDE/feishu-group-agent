# 常驻 cursor-agent（ACP / Agent Client Protocol）。只用标准库，stdio 上收发一行一个 JSON-RPC 2.0。
# 协议以这些页面为准（实现时读过，不是凭记忆）：
#   https://cursor.com/docs/cli/acp
#   https://cursor.com/docs/cli/reference/parameters
#   https://agentclientprotocol.com/protocol/initialization
#   https://agentclientprotocol.com/protocol/session-setup
#   https://agentclientprotocol.com/protocol/prompt-turn
#   https://agentclientprotocol.com/protocol/tool-calls
#   https://agentclientprotocol.com/protocol/session-modes
#   https://agentclientprotocol.com/protocol/schema
# Cursor 官方示例的 protocolVersion 是 1。v2 草稿（/protocol/v2/）不在这条路径上。
import ipaddress, json, os, re, socket, subprocess, threading, time, urllib.request
from urllib.parse import unquote, urljoin, urlparse
import guard

PROFILE_MARK = "以下是本群的长期档案"
# 和 skills.PRELOAD_MARK 是同一句。预放正文会变，快照必须在这句之前结束。
PRELOAD_MARK = "本次预放技能"
# 档案块后面可能紧挨的下一段。用来整段替换，避免档案正文里的空行把块拆碎。
_PROFILE_NEXT = (
    "\n\n以下是本话题之前的对话",
    "\n\n本次预放技能",
    "\n\n提问：",
    "\n\n工具结果",
    "\n\n（附件",
    "\n\n用户发来",
    "\n\n附件《",
)


class AcpFail(Exception):
    """这一题走不了常驻进程。restart=False 时不要杀掉还在答的其他题。"""

    def __init__(self, msg, restart=True):
        super().__init__(msg)
        self.restart = restart


def kill_proc(p):
    """停掉子进程。Windows 用 taskkill /T，和 agentbot.kill_tree 同一做法；别处不用 Unix 专有接口。"""
    if not p or p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            p.kill()
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" or "text" in content:
            return content.get("text") or ""
        inner = content.get("content")
        return _content_text(inner) if inner is not None else ""
    if isinstance(content, list):
        return "".join(_content_text(x) for x in content)
    return ""


def _locate_profile(prompt):
    """返回档案块在 prompt 里的 [start, end)，没有则 None。start 指向「以下是本群的长期档案」。"""
    i = prompt.find(PROFILE_MARK)
    if i < 0:
        return None
    end = len(prompt)
    for b in _PROFILE_NEXT:
        j = prompt.find(b, i + len(PROFILE_MARK))
        if j != -1 and j < end:
            end = j
    return i, end


def apply_snapshot(prompt, snapshot):
    """会话已经有快照时，把这次拼出来的档案换成快照。snapshot 为空表示会话开始时没有档案。"""
    loc = _locate_profile(prompt)
    if snapshot:
        if loc is None:
            for b in _PROFILE_NEXT:
                j = prompt.find(b)
                if j != -1:
                    return prompt[:j] + "\n\n" + snapshot + prompt[j:]
            return prompt + "\n\n" + snapshot
        start, end = loc
        return prompt[:start] + snapshot + prompt[end:]
    if loc is None:
        return prompt
    start, end = loc
    cut = start - 2 if start >= 2 and prompt[start - 2:start] == "\n\n" else start
    return prompt[:cut] + prompt[end:]


def _config_id(opt):
    """配置项的标识。请求里的 configId 用返回里的 configId，没有再退回 id。都不填就不调用。"""
    for key in ("configId", "id"):
        val = opt.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _select_values(options):
    """扁平列表或分组里的可选值。没有 value 的组只往下展开。"""
    found = []
    if not isinstance(options, list):
        return found
    for item in options:
        if not isinstance(item, dict):
            continue
        val = item.get("value")
        if isinstance(val, str) and val.strip():
            found.append(item)
            continue
        nested = item.get("options")
        if isinstance(nested, list):
            found.extend(_select_values(nested))
    return found


def _model_set_params(created, model):
    """需要调用 session/set_config_option 时返回 (configId, value)，否则 None。

    只在 session/new 的结果里确实有带可选值的模型项，并且当前值与配置不一致时才返回。
    value 用可选值里的 value，不用配置里的原文去猜。对不上就跳过，避免 Invalid params。
    """
    model = (model or "").strip()
    if not model or not isinstance(created, dict):
        return None
    for opt in created.get("configOptions") or []:
        if not isinstance(opt, dict):
            continue
        cid = _config_id(opt)
        if not cid:
            continue
        if opt.get("category") != "model" and cid != "model":
            continue
        if opt.get("type") not in (None, "", "select"):
            continue
        values = _select_values(opt.get("options"))
        if not values:
            continue
        chosen = None
        for item in values:
            val = item.get("value")
            name = item.get("name")
            if isinstance(val, str) and val.strip() == model:
                chosen = val
                break
            if isinstance(name, str) and name.strip() == model and isinstance(val, str) and val.strip():
                chosen = val
                break
        if not chosen:
            continue
        current = opt.get("currentValue")
        cur = current.strip() if isinstance(current, str) else current
        if cur == chosen.strip() or cur == model:
            return None
        return cid, chosen
    return None


def _describe_tool(upd):
    """把 ACP 工具更新说成进度卡片已经在用的那几句。"""
    kind = str(upd.get("kind") or "")
    name = str(upd.get("name") or "")
    raw = upd.get("rawInput") if isinstance(upd.get("rawInput"), dict) else {}
    path = ""
    locs = upd.get("locations") or []
    if locs and isinstance(locs[0], dict):
        path = str(locs[0].get("path") or "")
    if not path:
        path = str(raw.get("path") or raw.get("targetFile") or "")
    low = (kind + " " + name).lower()
    if kind == "search" or any(x in low for x in ("grep", "glob", "search")):
        return "正在查知识库…"
    if kind == "fetch" or "web" in low:
        return "正在查网上资料…"
    if kind == "think":
        return "正在思考…"
    if kind == "read" or "read" in low or path:
        base = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0] if path else ""
        return ("正在读《%s》…" % base) if base else "正在读资料…"
    return "正在查资料…"


# 主机名会解析两次。任一结果落在非公网、两次结果不一致、或解析失败，都拒绝。
# 八进制、十六进制、省略写法（127.1）和写坏的数字主机名也拒绝，不交给后面的程序自己解释。
_HTTP_URL = re.compile(r"https?://[^\s<>\"'\\]+", re.I)
_IP_LITERAL = re.compile(r"^[0-9a-fA-FxX.:]+$")
# 测试可以换成假的跳转探测。返回下一跳网址；没有跳转或网络失败时返回空字符串。
_redirect_probe = None


def _clip(text, n=80):
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    return text[:n] + "..."


def _flag(cfg, key, default):
    if not isinstance(cfg, dict) or key not in cfg:
        return default
    v = cfg[key]
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _pos_int(cfg, key, default):
    if not isinstance(cfg, dict) or key not in cfg or cfg[key] is None or cfg[key] == "":
        return default
    try:
        n = int(cfg[key])
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else 0


def _ip_is_blocked(value):
    try:
        addr = value if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ipaddress.ip_address(str(value))
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_multicast or addr.is_reserved or addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
        return True
    # 共享地址（100.64/10 等）is_global 为假，也不当公网。
    if not getattr(addr, "is_global", True):
        return True
    return False


def _parse_dotted(parts):
    """把 1～4 段的十进制、八进制（0177）或十六进制（0x7f）拼成 IPv4。拼不起来返回 None。"""
    nums = []
    for p in parts:
        if re.fullmatch(r"0x[0-9a-fA-F]+", p):
            n = int(p, 16)
        elif re.fullmatch(r"0[0-7]+", p):
            n = int(p, 8)
        elif p.isdigit():
            n = int(p, 10)
        else:
            return None
        if n < 0: return None
        nums.append(n)
    if len(nums) == 1:
        n = nums[0]
        if n > 0xFFFFFFFF: return None
        return ipaddress.IPv4Address(n)
    if len(nums) == 2:
        a, b = nums
        if a > 255 or b > 0xFFFFFF: return None
        return ipaddress.IPv4Address((a << 24) | b)
    if len(nums) == 3:
        a, b, c = nums
        if a > 255 or b > 255 or c > 0xFFFF: return None
        return ipaddress.IPv4Address((a << 24) | (b << 16) | c)
    if len(nums) == 4:
        if any(n > 255 for n in nums): return None
        return ipaddress.IPv4Address("%d.%d.%d.%d" % tuple(nums))
    return None


def _parse_ip(host):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # 十进制或十六进制整数，例如 2130706433、0x7f000001 都是 127.0.0.1。
    if re.fullmatch(r"0x[0-9a-fA-F]+", host):
        n = int(host, 16)
        if n <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(n)
        return None
    if host.isdigit() and len(host) <= 10:
        n = int(host)
        if n <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(n)
        return None
    if "." in host and _IP_LITERAL.match(host):
        return _parse_dotted(host.split("."))
    return None


def _lookup_host(host):
    """解析主机名。失败或超时返回空列表，调用方按「没解析出来」处理。"""
    box = {}

    def run():
        try:
            box["infos"] = socket.getaddrinfo(host, None)
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(1.5)
    if t.is_alive() or box.get("err") is not None:
        return []
    return box.get("infos") or []


def _addrs_of(infos):
    out = set()
    for item in infos or ():
        try:
            addr = item[4][0]
        except Exception:
            continue
        if isinstance(addr, str) and "%" in addr:
            addr = addr.split("%", 1)[0]
        out.add(str(addr))
    return out


def _infos_blocked(infos):
    addrs = _addrs_of(infos)
    if not addrs:
        return True
    return any(_ip_is_blocked(a) for a in addrs)


def _host_is_blocked(host):
    host = unquote(str(host or "")).strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if "%" in host:
        host = host.split("%", 1)[0]
    if not host or host == ".":
        return True
    if any(c in host for c in "/\\@?# \t\r\n\x00"):
        return True
    if host in ("localhost", "metadata.google.internal") or host.endswith(".localhost") or host.endswith(".local"):
        return True
    ip = _parse_ip(host)
    if ip is not None:
        return _ip_is_blocked(ip)
    # 看起来像 IP 但没能解析（例如八进制里混了 8、9）时拒绝，避免后面的程序用另一种规则解释。
    if _IP_LITERAL.match(host) and any(c.isdigit() for c in host):
        return True
    try:
        first = _lookup_host(host)
    except Exception:
        return True
    if not first or _infos_blocked(first):
        return True
    try:
        second = _lookup_host(host)
    except Exception:
        return True
    if not second or _infos_blocked(second):
        return True
    # 两次解析不一致，按 DNS 重绑定处理。
    if _addrs_of(first) != _addrs_of(second):
        return True
    return False


def _first_http_url(text):
    m = _HTTP_URL.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(").,;]}>\"'")


def _public_http_url(url):
    raw = (url or "").strip()
    if not raw or any(c in raw for c in "\\\x00\r\n\t") or len(raw) > 1024:
        return False
    parts = urlparse(raw)
    if (parts.scheme or "").lower() not in ("http", "https"):
        return False
    # 网址里带账号密码，既可能把密钥送出去，也可能用来混淆主机名。
    if parts.username or parts.password:
        return False
    host = parts.hostname
    if not host:
        return False
    if not _host_is_blocked(host):
        return True
    return False


def _url_exfil(url, names=()):
    """网址或搜索词里夹带客户名、手机号、邮箱、密钥时返回 True。不管这台主机是不是公网。"""
    return guard.embeds_secret(unquote(url or ""), names)


class _StopRedirect(Exception):
    def __init__(self, url):
        self.url = url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _StopRedirect(newurl)


def _probe_redirect(url):
    """只看第一跳的 Location，不跟着走。失败当没有跳转（这一跳本身已经检查过）。"""
    if _redirect_probe is not None:
        return _redirect_probe(url)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(url, timeout=2)
        return ""
    except _StopRedirect as e:
        return e.url or ""
    except Exception:
        return ""


def _chain_public(url, names=()):
    """初始网址和最多 3 次跳转都必须是公网，且不能夹带敏感信息。"""
    seen = set()
    cur = url
    for _ in range(4):
        if not cur or cur in seen:
            return False
        seen.add(cur)
        if not _public_http_url(cur) or _url_exfil(cur, names):
            return False
        try:
            nxt = _probe_redirect(cur)
        except Exception:
            return True
        if not nxt:
            return True
        cur = urljoin(cur, nxt)
    return False


def _tool_text(tool, key):
    return str((tool or {}).get(key) or "").strip()


def _web_search_identity(tool):
    kind = _tool_text(tool, "kind").lower()
    tid = _tool_text(tool, "toolCallId").lower()
    title = _tool_text(tool, "title").lower()
    return kind == "search" and (tid.startswith("web_search") or title.startswith("web search"))


def _web_fetch_identity(tool):
    kind = _tool_text(tool, "kind").lower()
    tid = _tool_text(tool, "toolCallId").lower()
    title = _tool_text(tool, "title").lower()
    return kind == "fetch" and (tid.startswith("web_fetch") or title.startswith("fetch http"))


def _fetch_urls(tool):
    found = []
    seen = set()

    def add(text):
        u = _first_http_url(text if isinstance(text, str) else "")
        if u and u not in seen:
            seen.add(u)
            found.append(u)

    add(_tool_text(tool, "title"))
    raw = tool.get("rawInput") if isinstance(tool, dict) else None
    if isinstance(raw, dict):
        for key in ("url", "uri", "href"):
            add(raw.get(key))
    locs = tool.get("locations") if isinstance(tool, dict) else None
    if isinstance(locs, list):
        for loc in locs:
            if isinstance(loc, dict):
                for key in ("url", "uri"):
                    add(loc.get(key))
    return found


def _is_public_web_tool(tool):
    """上网搜索，或打开公网 http(s)。改文件、执行命令、内网网页都返回 False。"""
    if not isinstance(tool, dict):
        return False
    if _web_search_identity(tool):
        return True
    if not _web_fetch_identity(tool):
        return False
    urls = _fetch_urls(tool)
    return bool(urls) and all(_public_http_url(u) for u in urls)


def _allow_once_option(opts):
    """只认 kind 为 allow_once 的项。optionId 写成 allow-always 的跳过。"""
    for o in opts or []:
        if not isinstance(o, dict) or o.get("kind") != "allow_once":
            continue
        oid = str(o.get("optionId") or "")
        if oid.replace("_", "-") == "allow-always":
            continue
        return oid or "allow-once"
    return ""


def _reject_option(opts):
    """选 reject_once，其次 reject_always。没有拒绝项就返回空，调用方回 cancelled。"""
    for kind in ("reject_once", "reject_always"):
        for o in opts or []:
            if not isinstance(o, dict):
                continue
            oid = o.get("optionId") or ""
            if o.get("kind") == kind or str(oid).replace("_", "-") == kind.replace("_", "-"):
                return oid or kind.replace("_", "-")
    return ""


class _Turn:
    """把 session/update 收成一段最终文字，同时回调 on_stage（和 AgentStream 同一形状）。"""

    def __init__(self, on_stage):
        self.on_stage = on_stage
        self.cur = ""
        self.tools = 0
        self.seen = set()
        self.staged_answer = False
        self.saw_thought = False

    def _emit(self, stage=None, think=None, end=False, text=None):
        if not self.on_stage:
            return
        try:
            if text is not None:
                self.on_stage(None, text=text)
            elif think is not None:
                self.on_stage(None, think=think, end=end)
            elif stage:
                self.on_stage(stage)
        except TypeError:
            try:
                if think is not None:
                    self.on_stage(None, think=think, end=end)
                elif stage:
                    self.on_stage(stage)
            except Exception:
                pass
        except Exception:
            pass

    def update(self, upd):
        if not isinstance(upd, dict):
            return
        kind = upd.get("sessionUpdate")
        if kind == "agent_thought_chunk":
            t = _content_text(upd.get("content"))
            if t:
                self.saw_thought = True
                self._emit(think=t)
                if not self.cur:
                    self._emit("正在思考…")
        elif kind == "agent_message_chunk":
            t = _content_text(upd.get("content"))
            if not t:
                return
            self.cur += t
            self._emit(text=t)
            if self.tools and not self.staged_answer:
                self.staged_answer = True
                self._emit("正在整理答案…")
        elif kind in ("tool_call", "tool_call_update"):
            tid = str(upd.get("toolCallId") or "")
            first = tid not in self.seen if tid else kind == "tool_call"
            if tid:
                self.seen.add(tid)
            if first:
                self.tools += 1
                if kind == "tool_call" or upd.get("status") in (None, "pending", "in_progress"):
                    self.cur = ""
                    self.staged_answer = False
                self._emit(_describe_tool(upd))
        elif kind == "plan" and not self.cur:
            self._emit("正在思考…")

    def finish(self):
        if self.saw_thought:
            self._emit(think="", end=True)
        return self.cur.strip()


class _Sess:
    def __init__(self, sid, snapshot):
        self.sid = sid
        self.snapshot = snapshot
        self.last = time.time()
        self.inflight = 0
        self.lock = threading.Lock()


class Pool:
    """一个常驻 ACP 进程。按话题复用 session，数量和空闲时间有上限。"""

    def __init__(self, cmd_fn, agent_cfg, cwd, log):
        self.cmd_fn = cmd_fn
        self.cfg = agent_cfg or {}
        self.cwd = cwd
        self.log = log
        self.max_sessions = max(1, int(self.cfg.get("acp_max_sessions", 8) or 8))
        self.idle_sec = float(self.cfg.get("acp_idle_sec", 1800) if self.cfg.get("acp_idle_sec", 1800) is not None else 1800)
        self.stall_sec = float(self.cfg.get("stall_sec", 300) or 300)
        self.max_runtime = float(self.cfg.get("max_runtime_sec", 1800) or 1800)
        self.model = (self.cfg.get("model") or "").strip()
        self.allow_web = _flag(self.cfg, "acp_allow_web", True)
        self.web_max = _pos_int(self.cfg, "acp_web_max_per_turn", 15)
        self._redact_names = None
        self.web_used = {}
        self.state_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.create_lock = threading.Lock()
        self.proc = None
        self.gen = 0
        self.ready_flag = False
        self.req_id = 0
        self.pending = {}
        self.handlers = {}
        self.sessions = {}
        self.last_io = time.time()
        self._restarting = False
        self._restart_thread = None
        self._err_tail = []

    def _secret_names(self):
        if self._redact_names is None:
            self._redact_names = guard.load_names(os.path.join(self.cwd or "", "redact.txt"))
        return self._redact_names

    def _alive(self):
        return bool(self.ready_flag and self.proc and self.proc.poll() is None)

    def _write(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self.write_lock:
            proc = self.proc
            if not proc or proc.poll() is not None:
                raise AcpFail("process exited")
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except Exception as e:
                raise AcpFail("process exited: %s" % e)

    def _fail_pending(self, reason, gen=None):
        with self.pending_lock:
            if gen is not None and gen != self.gen:
                return
            items = list(self.pending.items())
            self.pending.clear()
        for _i, (ev, box) in items:
            box["error"] = {"message": reason}
            ev.set()

    def _read_out(self, proc, gen):
        try:
            while gen == self.gen:
                raw = proc.stdout.readline()
                if not raw:
                    break
                if gen != self.gen:
                    break
                self.last_io = time.time()
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._handle_line(line, gen)
        except Exception as e:
            if gen == self.gen:
                self.log.warning("acp read: %s", e)
        finally:
            self._fail_pending("process exited", gen)

    def _read_err(self, proc, gen):
        try:
            while gen == self.gen:
                raw = proc.stderr.readline()
                if not raw:
                    break
                if gen != self.gen:
                    break
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    self._err_tail = (self._err_tail + [text])[-20:]
        except Exception:
            pass

    def _handle_line(self, line, gen):
        try:
            msg = json.loads(line)
        except Exception:
            self.log.info("acp skip non-json")
            return
        if isinstance(msg, list):
            for m in msg:
                if isinstance(m, dict):
                    self._handle_msg(m, gen)
            return
        if isinstance(msg, dict):
            self._handle_msg(msg, gen)

    def _handle_msg(self, msg, gen):
        if gen != self.gen:
            return
        method = msg.get("method")
        if method:
            if "id" in msg and msg.get("id") is not None:
                self._on_request(msg)
            elif method == "session/update":
                self._on_update(msg.get("params") or {})
            return
        if "id" not in msg:
            return
        with self.pending_lock:
            item = self.pending.pop(msg.get("id"), None)
        if not item:
            return
        ev, box = item
        if msg.get("error") is not None:
            box["error"] = msg.get("error")
        else:
            box["result"] = msg.get("result")
        ev.set()

    def _on_update(self, params):
        upd = params.get("update")
        if not isinstance(upd, dict):
            if params.get("sessionUpdate"):
                upd = params
            else:
                return
        sid = params.get("sessionId")
        with self.state_lock:
            cb = self.handlers.get(sid)
        if cb:
            cb(upd)

    def _on_request(self, msg):
        method = msg.get("method")
        if method == "session/request_permission":
            try:
                self._reply_permission(msg)
            except Exception as e:
                self.log.warning("acp permission error: %s", e)
                try:
                    self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                except Exception:
                    pass
            return
        if method == "cursor/ask_question":
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})
            return
        if method == "cursor/create_plan":
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": {"outcome": "rejected", "reason": "read-only"}}})
            return
        # 没回答的请求会把对方卡住。不认识的一律回方法不存在，写操作不会被放行。
        self._write({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "method not supported"}})

    def _reply_permission(self, msg):
        """上网搜索和公网网页可以选 allow_once。其他一律拒绝，绝不选 allow_always。"""
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        tool = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        kind = str(tool.get("kind") or "")
        tid = str(tool.get("toolCallId") or "")
        title = str(tool.get("title") or "")
        opts = params.get("options") if isinstance(params.get("options"), list) else []
        allowed = False
        chosen = ""
        why = ""
        web = _is_public_web_tool(tool)
        names = self._secret_names()
        if web and _web_search_identity(tool) and _url_exfil(title, names):
            web = False
            why = "exfil"
        elif web and _web_fetch_identity(tool) and any(_url_exfil(u, names) for u in _fetch_urls(tool)):
            web = False
            why = "exfil"
        elif web and _web_fetch_identity(tool) and not all(_chain_public(u, names) for u in _fetch_urls(tool)):
            web = False
            why = "redirect"
        if web and not self.allow_web:
            why = "off"
        elif web:
            oid = _allow_once_option(opts)
            if not oid:
                why = "no-allow-once"
            else:
                sid = str(params.get("sessionId") or "")
                with self.state_lock:
                    used = int(self.web_used.get(sid, 0) or 0)
                    if used >= self.web_max:
                        why = "limit"
                    else:
                        self.web_used[sid] = used + 1
                        allowed = True
                        chosen = oid
                        why = "web"
        elif why:
            pass
        elif _web_fetch_identity(tool):
            why = "private"
        else:
            why = "kind"
        if not allowed:
            chosen = _reject_option(opts)
            if chosen:
                result = {"outcome": {"outcome": "selected", "optionId": chosen}}
            else:
                result = {"outcome": {"outcome": "cancelled"}}
                chosen = "cancelled"
        else:
            result = {"outcome": {"outcome": "selected", "optionId": chosen}}
        self.log.info(
            "acp permission %s kind=%s toolCallId=%s title=%s option=%s why=%s",
            "allowed" if allowed else "rejected",
            kind or "-",
            tid or "-",
            _clip(guard.redact(title, names)),
            chosen,
            why,
        )
        self._write({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _rpc(self, method, params, timeout):
        with self.pending_lock:
            self.req_id += 1
            i = self.req_id
            ev = threading.Event()
            box = {}
            self.pending[i] = (ev, box)
        try:
            self._write({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})
        except AcpFail:
            with self.pending_lock:
                self.pending.pop(i, None)
            raise
        deadline = time.time() + timeout
        while not ev.wait(0.2):
            proc = self.proc
            if proc is None or proc.poll() is not None:
                err = "process exited rc=%s" % (None if proc is None else proc.returncode)
                tail = " | ".join(self._err_tail[-3:])
                raise AcpFail(err + ((" | " + tail) if tail else ""))
            if time.time() > deadline:
                raise AcpFail("timeout %s" % method)
        if box.get("error") is not None:
            err = box["error"]
            text = err.get("message") if isinstance(err, dict) else str(err)
            raise AcpFail("protocol %s: %s" % (method, text))
        return box.get("result")

    def _spawn(self):
        """调用方须持有 start_lock。旧进程先标记作废，读线程不会把新请求清掉。"""
        old = self.proc
        self.gen += 1
        gen = self.gen
        self.ready_flag = False
        self.proc = None
        self.handlers.clear()
        self._fail_pending("restart")
        if old:
            kill_proc(old)
        cmd = list(self.cmd_fn())
        self.log.info("acp spawn %s", " ".join(str(x) for x in cmd))
        proc = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.proc = proc
        self.last_io = time.time()
        self._err_tail = []
        threading.Thread(target=self._read_out, args=(proc, gen), daemon=True).start()
        threading.Thread(target=self._read_err, args=(proc, gen), daemon=True).start()
        return proc

    def _handshake(self):
        result = self._rpc("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "feishu-group-agent", "title": "飞书群助手", "version": "1.0.0"},
        }, timeout=90)
        ver = result.get("protocolVersion") if isinstance(result, dict) else None
        if ver != 1:
            raise AcpFail("protocolVersion %s" % ver)
        self._rpc("authenticate", {"methodId": "cursor_login"}, timeout=30)
        self.ready_flag = True
        self.log.info("acp ready protocolVersion=%s", ver)
        return ver

    def _start_locked(self):
        if self._alive():
            return
        self.sessions.clear()
        try:
            self._spawn()
            self._handshake()
        except Exception:
            self.ready_flag = False
            proc = self.proc
            self.proc = None
            if proc:
                kill_proc(proc)
            self.gen += 1
            raise

    def start(self):
        with self.start_lock:
            self._start_locked()

    def close(self):
        with self.start_lock:
            self.gen += 1
            self.ready_flag = False
            self.handlers.clear()
            self.sessions.clear()
            self._fail_pending("closed")
            proc = self.proc
            self.proc = None
            if proc:
                kill_proc(proc)

    def _ensure(self):
        thread = None
        with self.state_lock:
            if self._alive():
                return
            if self._restarting:
                thread = self._restart_thread
        if thread:
            thread.join(40)
        with self.start_lock:
            if self._alive():
                return
            self._start_locked()

    def _schedule_restart(self):
        with self.state_lock:
            if self._restarting:
                return
            self._restarting = True
            self.ready_flag = False
            self.sessions.clear()
            self.handlers.clear()
            old = self.proc
            self.proc = None
            # 先作废旧读线程，再拉起新进程，避免把新握手的请求清掉
            self.gen += 1
            t = threading.Thread(target=self._restart_body, daemon=True)
            self._restart_thread = t
        self._fail_pending("restart")
        if old:
            kill_proc(old)
        self.log.info("acp restart")
        t.start()

    def _restart_body(self):
        try:
            with self.start_lock:
                self._start_locked()
        except Exception as e:
            self.log.warning("acp restart failed: %s", e)
        finally:
            with self.state_lock:
                self._restarting = False

    def drop(self, key):
        with self.state_lock:
            had = self.sessions.pop(key, None)
        if had:
            self.log.info("acp session drop %s", key)

    def _expire(self):
        now = time.time()
        dead = [k for k, s in self.sessions.items() if not s.inflight and now - s.last > self.idle_sec]
        for k in dead:
            self.sessions.pop(k, None)
            self.log.info("acp session drop idle %s", k)

    def _make_room(self, key):
        self._expire()
        if key in self.sessions:
            return
        while len(self.sessions) >= self.max_sessions:
            victims = [k for k, s in self.sessions.items() if not s.inflight and k != key]
            if not victims:
                raise AcpFail("acp sessions full", restart=False)
            victim = min(victims, key=lambda k: self.sessions[k].last)
            self.sessions.pop(victim, None)
            self.log.info("acp session evict %s", victim)

    def _configure(self, sid, created):
        modes = (created or {}).get("modes") if isinstance(created, dict) else None
        current = (modes or {}).get("currentModeId") if isinstance(modes, dict) else None
        avail = []
        if isinstance(modes, dict):
            avail = [m.get("id") for m in (modes.get("availableModes") or []) if isinstance(m, dict)]
        if current != "ask" and (not avail or "ask" in avail):
            try:
                self._rpc("session/set_mode", {"sessionId": sid, "modeId": "ask"}, timeout=20)
            except AcpFail as e:
                self.log.warning("acp set_mode: %s", e)
        params = _model_set_params(created, self.model)
        if not params:
            return
        cid, value = params
        try:
            self._rpc("session/set_config_option", {
                "sessionId": sid, "configId": cid, "value": value,
            }, timeout=20)
        except AcpFail as e:
            # 模型已经由启动参数 --model 钉住。设配置项失败不影响这一题。
            self.log.debug("acp set_model: %s", e)

    def _get_or_create(self, key, prompt):
        """返回 (session, created, prompt)。新建时冻结档案；复用时换回快照。"""
        with self.create_lock:
            with self.state_lock:
                self._expire()
                sess = self.sessions.get(key)
            if sess:
                updated = apply_snapshot(prompt, sess.snapshot)
                if updated != prompt:
                    self.log.info("acp snapshot kept %s", key)
                self.log.info("acp session reuse %s id=%s", key, sess.sid)
                return sess, False, updated
            with self.state_lock:
                self._make_room(key)
            created = self._rpc("session/new", {"cwd": os.path.abspath(self.cwd), "mcpServers": []}, timeout=60)
            sid = created.get("sessionId") if isinstance(created, dict) else None
            if not sid:
                raise AcpFail("protocol session/new: no sessionId")
            self._configure(sid, created)
            loc = _locate_profile(prompt)
            snap = prompt[loc[0]:loc[1]] if loc else ""
            sess = _Sess(sid, snap)
            with self.state_lock:
                self.sessions[key] = sess
            self.log.info("acp session new %s id=%s", key, sid)
            self.log.info("acp snapshot frozen %s chars=%d", key, len(snap))
            return sess, True, prompt

    def _prompt(self, sid, prompt, on_stage):
        turn = _Turn(on_stage)
        with self.state_lock:
            self.handlers[sid] = turn.update
            self.web_used[sid] = 0
        self.last_io = time.time()
        t0 = time.time()
        with self.pending_lock:
            self.req_id += 1
            i = self.req_id
            ev = threading.Event()
            box = {}
            self.pending[i] = (ev, box)
        try:
            self._write({"jsonrpc": "2.0", "id": i, "method": "session/prompt", "params": {
                "sessionId": sid, "prompt": [{"type": "text", "text": prompt}],
            }})
            while not ev.wait(0.2):
                proc = self.proc
                if proc is None or proc.poll() is not None:
                    raise AcpFail("process exited rc=%s" % (None if proc is None else proc.returncode))
                silent = time.time() - self.last_io
                if silent > self.stall_sec:
                    raise AcpFail("stall %.0fs no output" % silent)
                if time.time() - t0 > self.max_runtime:
                    raise AcpFail("max_runtime %.0fs" % self.max_runtime)
        finally:
            with self.state_lock:
                self.handlers.pop(sid, None)
        if box.get("error") is not None:
            err = box["error"]
            text = err.get("message") if isinstance(err, dict) else str(err)
            raise AcpFail("protocol session/prompt: %s" % text)
        text = turn.finish()
        if not text:
            stop = box.get("result") if isinstance(box.get("result"), dict) else {}
            raise AcpFail("empty response stop=%s" % (stop or {}).get("stopReason"))
        return text

    def ask(self, key, prompt, on_stage=None):
        try:
            self._ensure()
            sess, _created, prompt = self._get_or_create(key or "default", prompt)
            with sess.lock:
                sess.inflight += 1
                sess.last = time.time()
                try:
                    out = self._prompt(sess.sid, prompt, on_stage)
                finally:
                    sess.inflight -= 1
                    sess.last = time.time()
            return out
        except AcpFail as e:
            if e.restart:
                self._schedule_restart()
            raise


def probe(cmd, cwd, log):
    """--check：启动一次，做 initialize 和 authenticate，然后关掉。成功返回说明，失败抛 AcpFail。"""
    pool = Pool(lambda: list(cmd), {}, cwd, log)
    try:
        pool.start()
        return "protocolVersion=1"
    finally:
        pool.close()
