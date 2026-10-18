# 工具层：只允许白名单内的 lark-cli 命令，统一 --as bot；写操作需确认码；全部写审计日志；缺权限优雅降级。
import json, os, re, sqlite3, subprocess, time, random, threading, datetime, glob, shutil
import guard, skills

def run_capture(cmd, cwd=None, timeout=60):
    """代替 subprocess.run：超时杀整棵进程树（Windows 上只杀父进程时孙进程仍占着管道，communicate 会一直卡住）"""
    p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace")
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt": subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else: p.kill()
        try: out, err = p.communicate(timeout=15)
        except Exception: out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)

_CMD_UNSAFE = re.compile(r'["&|<>^%!\r\n]')

class ToolError(Exception):
    def __init__(self, user_msg, detail=""):
        super().__init__(user_msg); self.user_msg = user_msg; self.detail = detail

SCOPE_HINT = {
    "history": "im:message.group_msg（群消息读取）", "docs_read": "docx:document:readonly",
    "docs_search": "search:docs:read", "calendar_read": "calendar:calendar.event:read",
    "base_read": "base:record:read", "attachment": "im:message:readonly / im:resource",
    "docs_create": "docx:document:create", "docs_append": "docx:document:write_only",
    "calendar_create": "calendar:calendar.event:create", "calendar_create_batch": "calendar:calendar.event:create",
    "calendar_update": "calendar:calendar.event:update", "calendar_delete": "calendar:calendar.event:delete",
    "base_create": "base:record:create", "merge_forward": "im:message:readonly",
}
READ_TOOLS = {"history", "docs_read", "docs_search", "calendar_read", "base_read"}
WRITE_TOOLS = {"docs_create", "docs_append", "calendar_create", "calendar_create_batch", "calendar_update", "calendar_delete", "base_create"}
_CAL_FOLLOW = {"calendar_create_batch", "calendar_update", "calendar_delete"}
_NOTIFY_RX = re.compile(r"@\s*全员|@\s*所有人|通知全员|通知所有人|提醒全员|告知全员|艾特全员")
# 正文超过这个长度时，保留开头、中段和结尾，方便分段概括。配置没写时用这个默认值。
DEFAULT_BODY_CHARS = 20000
_NOT_FOUND_CODES = {3380002, 3380003, 1770002, 1770003, 1254043, 1254044}
_PERM_CODES = {1770032, 91403, 131006, 99991663, 99991668, 99991664}
_NAME_ATTR = re.compile(r'(?:file_name|name)\s*=\s*"([^"]+)"', re.I)
_KEY_ATTR = re.compile(r'(?:file_key|image_key|key)\s*=\s*"([^"]+)"', re.I)
# 旧机器人配置里的名字。读的时候当成现在的名字；两边都写了就用新的，并在 --check 里提示。
_LEGACY_TOOLS = (("doc_search", "docs_search"), ("doc_read", "docs_read"), ("doc_create", "docs_create"), ("doc_append", "docs_append"))
_LEGACY_RESOURCES = (("doc_folder_token", "folder_token"), ("doc_append_tokens", "docs_append_tokens"))

def apply_legacy_names(cfg):
    hints = []
    def take(bucket, pairs, where):
        if not isinstance(bucket, dict): return
        for old, new in pairs:
            if old not in bucket: continue
            if new not in bucket:
                bucket[new] = bucket.pop(old)
                hints.append("%s.%s 是旧名字，已按 %s 读取。请改成 %s" % (where, old, new, new))
            else:
                hints.append("%s.%s 是旧名字，已忽略（在用 %s）。请删掉 %s" % (where, old, new, old))
    take(cfg.get("tools"), _LEGACY_TOOLS, "tools")
    take((cfg.get("write") or {}).get("resources"), _LEGACY_RESOURCES, "write.resources")
    return hints

TOOL_DOC = """可用工具（飞书读写每次最多调用一个；需要时只输出一行：<<TOOL>>{"tool":"名称","args":{...}}，不要输出别的）：
- history {"keyword":"可选关键词"}：读取本群最近几天的消息，包含话题里的回复（仅群聊可用）
- docs_search {"query":"关键词"}：搜索已分享给机器人的飞书文档
- docs_read {"doc":"云文档链接或 token","keyword":"可选"}：读取飞书文档内容。file_ 或 img_ 开头的是聊天附件，不是云文档，不要传给这个工具
- calendar_read {"start":"ISO8601","end":"ISO8601"}：查看团队日历日程
- base_read {"keyword":"可选"}：查询团队多维表格记录
- docs_create {"title":"标题","markdown":"正文"}：在团队专用文件夹新建文档（需发起人确认）
- docs_append {"doc":"文档 token","markdown":"追加内容"}：在白名单文档末尾追加（需确认）
- calendar_create {"summary":"标题","start":"ISO8601","end":"ISO8601","description":"可选","notify_all":false}：在团队日历建一个日程（需确认，不邀请任何人）
- calendar_create_batch {"events":[{"summary":"标题","start":"ISO8601","end":"ISO8601","description":"可选"}],"notify_all":false}：一次提议多个日程，一个确认码确认全部（需确认，不邀请任何人）
- calendar_update {"event_id":"日程 id","summary":"可选","start":"可选","end":"可选","description":"可选","notify_all":false}：修改已有日程（需确认，不改参会人）
- calendar_delete {"event_id":"日程 id","summary":"可选，用于确认时给人看","notify_all":false}：删除已有日程（需确认，不通知参会人）
- base_create {"fields":{"字段名":"值"}}：在团队多维表格新增一条记录（需确认）
上面这些只是飞书读写。联网搜索和打开网页是你自带的能力，不在这张表里，需要时直接用，不要拒绝。
多个日程用 calendar_create_batch，仍然只输出一行。notify_all 只在对方明确说要 @全员 或通知所有人时才设为 true，不要自己写 at 标签，也不要邀请任何人。
没有必要就不要调用工具，直接回答。
重要：你处于只读模式，不能改本地文件，但飞书的新建/追加/建日程/改日程/删日程/加记录不需要你自己执行——只要输出上面那一行 <<TOOL>>，机器人会代为执行（写操作先让发起人回复确认码）。用户要求写飞书时，直接输出对应工具行，绝不要回答“切换到可编辑模式”或“无法创建”。"""
MEMORY_TOOL_DOC = """还可以提议写入群记忆（需白名单发起人确认，仍只输出一行）：
- memory_propose {"text":"主题：内容"}：用户明确说「记住」「以后都」这类偏好时使用。不要把没经确认的内容当成已经记住。"""

class Tools:
    def __init__(self, cfg, base, lark_exe, log, dry=False):
        self.cfg, self.base, self.lark_exe, self.log, self.dry = cfg, base, lark_exe, log, dry
        self.legacy_hints = apply_legacy_names(cfg)
        self.t = cfg.get("tools", {}); self.w = cfg.get("write", {})
        self.audit_dir = os.path.join(base, "audit"); os.makedirs(self.audit_dir, exist_ok=True)
        self.pending = {}; self.pl = threading.Lock(); self.al = threading.Lock()
        self._db = None
        self._load_pending()
        self.fake = None  # 测试注入：callable(argv)->dict
        self.skills = skills.SkillSet(cfg, base)  # 技能目录；enabled 默认 false
        self.names = guard.load_names(os.path.join(base, cfg.get("redact_file", "redact.txt")))

    # ---------- 基础 ----------
    def enabled(self, name):
        spec = self.t.get(name)
        if isinstance(spec, dict) and "enabled" in spec: return bool(spec.get("enabled"))
        # 旧配置只写了 calendar_create。改、删、批量跟着它开；写了 enabled 就以写的为准。
        if name in _CAL_FOLLOW and name not in self.t:
            return bool((self.t.get("calendar_create") or {}).get("enabled"))
        return bool((spec or {}).get("enabled", False)) if isinstance(spec, dict) else False
    def audit(self, **kw):
        kw["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        for k in ("args", "summary", "detail", "err", "result"):
            if isinstance(kw.get(k), str): kw[k] = guard.redact(kw[k], self.names)
        with self.al, open(os.path.join(self.audit_dir, datetime.date.today().strftime("%Y%m") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def lark(self, tool, argv, cwd=None, timeout=60):
        argv = list(argv) + ["--profile", self.cfg["profile"], "--as", "bot"]
        if self.lark_exe()[0].lower().endswith((".cmd", ".bat")) and any(_CMD_UNSAFE.search(str(x)) for x in argv):
            # 经 cmd.exe 转发时这些字符会被当成命令解析（命令注入），宁可拒绝
            raise ToolError("参数里含有不安全字符，已拒绝执行", "unsafe char for .cmd launcher")
        fk = os.environ.get("AGENTBOT_FAKE_LARK")  # 测试：JSON 文件 {"im +chat-messages-list": {...响应...}}
        if fk and not self.fake:
            m = json.load(open(fk, encoding="utf-8")); key = " ".join(argv[:2])
            self.fake = lambda av: m.get(" ".join(av[:2]), {"ok": True, "data": {}})
        if self.fake:
            j = self.fake(argv); self.log.info("[fake-lark] %s -> ok=%s", " ".join(argv[:2]), j.get("ok"))
        elif self.dry and (tool in WRITE_TOOLS and os.environ.get("AGENTBOT_DRY_LIVE_WRITES") != "1" or tool not in WRITE_TOOLS and not self.cfg.get("dry_run_live_reads")):
            self.log.info("[tools-dry-run] lark-cli %s", " ".join(argv)); return {"ok": True, "data": {"dry_run": True}}
        else:
            for attempt in range(3):
                try:
                    r = run_capture(self.lark_exe() + argv, cwd=cwd or self.base, timeout=timeout)
                    out = (r.stdout or "").strip(); err = r.stderr or ""
                except subprocess.TimeoutExpired:
                    out, err = "", "timeout"
                j = parse_cli_json(out, err)
                if not j: j = {"ok": False, "error": {"message": ((out or "")[:200] + " | " + (err or "")[-300:]).strip(" |")}}
                if j.get("ok") or not re.search(r"EOF|timeout|unreachable|connection|reset", json.dumps(j.get("error"), ensure_ascii=False), re.I): break
                self.log.warning("lark %s retry %d: %s", " ".join(argv[:2]), attempt + 1, str(j.get("error"))[:200]); time.sleep(3 * (attempt + 1))
            if not j.get("ok"): self.log.warning("lark %s failed: %s", " ".join(argv[:2]), json.dumps(j.get("error"), ensure_ascii=False)[:400])
        if not j.get("ok"):
            detail = json.dumps(j.get("error") or j, ensure_ascii=False)[:500]
            raise ToolError(explain_lark_error(tool, j, (self.cfg.get("bot_names") or ["机器人"])[0]), detail)
        return j

    # ---------- 读 ----------
    def run_read(self, name, args, ctx):
        # skill_read 只在技能层打开时才算已知工具；关掉时和以前一样是「未知工具」
        if name == "skill_read" and self.skills.enabled:
            call = self._skill_read
        else:
            if name not in READ_TOOLS: raise ToolError("未知工具")
            if not self.enabled(name): raise ToolError("这项能力还没在配置里启用")
            call = getattr(self, "_" + name)
        try: res = call(args or {}, ctx)
        except ToolError as e:
            self.audit(kind="read", tool=name, user=ctx["user"], chat=ctx["chat_id"], ok=False, err=e.user_msg); raise
        self.audit(kind="read", tool=name, user=ctx["user"], chat=ctx["chat_id"], ok=True, args=_brief(args))
        return res

    def _skill_read(self, a, ctx):
        """只读技能正文。名字必须在启动时扫到的目录里，不拼接用户给的路径。"""
        try: return self.skills.read((a or {}).get("name"))
        except skills.SkillError as e: raise ToolError(str(e))

    def _history(self, a, ctx):
        if ctx.get("chat_type") != "group": raise ToolError("私聊里不提供群历史查询")
        days = int(self.t["history"].get("days", 7))
        start = (datetime.datetime.now().astimezone() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
        j = self.lark("history", ["im", "+chat-messages-list", "--chat-id", ctx["chat_id"], "--start", start,
                                  "--page-size", "50", "--page-all", "--page-limit", str(self.t["history"].get("max_pages", 2)), "--no-reactions"])
        msgs = (j.get("data") or {}).get("messages") or []
        kw = (a.get("keyword") or "").strip(); kw_l = kw.lower(); lines = []
        for m in msgs:
            blob = _history_blob(m)
            if kw_l and kw_l not in blob.lower(): continue
            lines.append(_history_line(m))
            for r in (m.get("thread_replies") or m.get("replies") or []):
                if isinstance(r, dict): lines.append("  回复 " + _history_line(r))
        return "本群最近 %d 天消息（%d 条%s）：\n" % (days, len(lines), "，关键词=" + kw if kw else "") + "\n".join(lines[:150])

    def _docs_search(self, a, ctx):
        q = (a.get("query") or "")[:30]
        j = self.lark("docs_search", ["drive", "+search", "--query", q, "--page-size", "10"])
        items = (j.get("data") or {}).get("items") or (j.get("data") or {}).get("docs") or []
        return "文档搜索结果：\n" + json.dumps(items, ensure_ascii=False)[:4000]

    def _docs_read(self, a, ctx):
        doc = str(a.get("doc") or "").strip()
        if not doc: raise ToolError("没有给出文档链接")
        if is_chat_file_token(doc):
            raise ToolError("这是聊天附件，不是云文档。请直接根据附件内容回答，不要把 file_key 当成文档 token")
        allow = self.t["docs_read"].get("allow_tokens")  # 为空表示：机器人能看到的都能读
        if allow and not _allow_token(doc, allow): raise ToolError("这份文档不在可读白名单里")
        argv = ["docs", "+fetch", "--doc", doc, "--doc-format", "markdown"]
        if a.get("keyword"): argv += ["--scope", "keyword", "--keyword", str(a["keyword"])[:50]]
        j = self.lark("docs_read", argv)
        d = j.get("data") or {}
        text = d.get("markdown") or d.get("content") or json.dumps(d, ensure_ascii=False)
        return "文档内容（节选）：\n" + clip_body(text, _cfg_int(self.t["docs_read"].get("max_chars"), DEFAULT_BODY_CHARS))

    def _calendar_read(self, a, ctx):
        argv = ["calendar", "+agenda"]
        cid = self.t["calendar_read"].get("calendar_id") or self.w.get("resources", {}).get("calendar_id")
        if cid: argv += ["--calendar-id", cid]
        for k in ("start", "end"):
            if a.get(k): argv += ["--" + k, str(a[k])]
        j = self.lark("calendar_read", argv)
        return "日程：\n" + json.dumps(j.get("data"), ensure_ascii=False)[:4000]

    def _base_read(self, a, ctx):
        b = self.t["base_read"]
        if not b.get("base_token") or not b.get("table_id"): raise ToolError("还没配置可查询的多维表格")
        argv = ["base", "+record-search", "--base-token", b["base_token"], "--table-id", b["table_id"],
                "--keyword", str(a["keyword"])[:50], "--limit", "50"] if a.get("keyword") and b.get("search_fields") else \
               ["base", "+record-list", "--base-token", b["base_token"], "--table-id", b["table_id"], "--limit", "50", "--format", "json"]
        if a.get("keyword") and b.get("search_fields"):
            for f in b["search_fields"]: argv += ["--search-field", f]
        j = self.lark("base_read", argv)
        return "表格记录：\n" + json.dumps(j.get("data"), ensure_ascii=False)[:5000]

    # ---------- 写：先登记待确认，确认后执行 ----------
    def propose_write(self, name, args, ctx):
        if name not in WRITE_TOOLS: raise ToolError("未知工具")
        if not self.w.get("enabled"): raise ToolError("写入功能还没启用")
        if not self.enabled(name): raise ToolError("这项写入能力还没在配置里启用")
        if ctx["user"] not in (self.w.get("allowed_users") or {}):
            self.audit(kind="write_denied", tool=name, user=ctx["user"], chat=ctx["chat_id"], args=_brief(args))
            raise ToolError("你不在写入白名单里，这个操作只能由管理员发起")
        raw = args or {}
        argv, summary, steps = self._build_write(name, raw)
        notify = _want_notify(name, raw, ctx.get("question") or "")
        code = "%06d" % random.SystemRandom().randint(0, 999999)
        rec = {"tool": name, "argv": argv, "steps": steps, "summary": summary, "user": ctx["user"],
               "chat_id": ctx["chat_id"], "ts": time.time(), "notify_all": notify}
        with self.pl:
            self._gc()
            self.pending[code] = rec
            self._save_pending(code, rec)
        self.audit(kind="write_proposed", tool=name, user=ctx["user"], chat=ctx["chat_id"], code=code, summary=summary)
        ttl = int(self.w.get("confirm_ttl_sec", 300)) // 60
        return "将要执行：%s\n请发起人在 %d 分钟内 @我 回复「确认 %s」执行，或「取消 %s」。" % (summary, ttl, code, code)

    def propose_memory(self, args, ctx):
        """模型提议记一条。落盘仍要白名单发起人用确认码点头。"""
        if not getattr(self, "memory_enabled", False): raise ToolError("长期记忆还没开启")
        if ctx.get("chat_type") == "p2p": raise ToolError("私聊里不能记进群记忆")
        if ctx["user"] not in (self.w.get("allowed_users") or {}):
            self.audit(kind="write_denied", tool="memory_propose", user=ctx["user"], chat=ctx["chat_id"], args=_brief(args))
            raise ToolError("你不在写入白名单里，这个操作只能由管理员发起")
        text = str((args or {}).get("text") or "").strip()
        limit = _cfg_int((self.cfg.get("memory") or {}).get("max_fact_chars"), 300)
        if len(text) < 2: raise ToolError("要记的内容太短")
        if limit > 0 and len(text) > limit: raise ToolError("太长了，请控制在 %d 字以内" % limit)
        code = "%06d" % random.SystemRandom().randint(0, 999999)
        summary = "写入群记忆：%s" % text[:80]
        rec = {"tool": "memory_propose", "argv": [], "steps": [], "summary": summary, "memory_text": text,
               "user": ctx["user"], "chat_id": ctx["chat_id"], "ts": time.time(), "notify_all": False}
        with self.pl:
            self._gc()
            self.pending[code] = rec
            self._save_pending(code, rec)
        self.audit(kind="write_proposed", tool="memory_propose", user=ctx["user"], chat=ctx["chat_id"], code=code, summary=summary)
        ttl = int(self.w.get("confirm_ttl_sec", 300)) // 60
        return "将要记入本群记忆：%s\n请发起人在 %d 分钟内 @我 回复「确认 %s」执行，或「取消 %s」。没确认之前，不要把它当成已经记住。" % (text[:200], ttl, code, code)

    def _build_write(self, name, a):
        """返回 (首条命令, 给人看的说明, 步骤列表)。每一步是 {"tool","argv"}。旧的单步记录仍只靠 argv。"""
        a = a or {}
        r = self.w.get("resources", {})
        if name == "docs_create":
            folder = r.get("folder_token")
            if not folder: raise ToolError("还没配置专用文档文件夹")
            title = str(a.get("title") or "未命名")[:80]
            argv = ["docs", "+create", "--parent-token", folder, "--title", title, "--doc-format", "markdown",
                    "--content", str(a.get("markdown") or "")[:20000]]
            return argv, "在专用文件夹新建文档《%s》" % title, [{"tool": name, "argv": argv}]
        if name == "docs_append":
            doc = str(a.get("doc") or ""); allow = r.get("docs_append_tokens") or []
            if not allow: raise ToolError("还没配置允许追加的文档")
            tok = _allow_token(doc, allow)
            if not tok: raise ToolError("这份文档不在可写白名单里")
            argv = ["docs", "+update", "--doc", tok, "--command", "append", "--doc-format", "markdown",
                    "--content", str(a.get("markdown") or "")[:20000]]
            return argv, "在文档 %s 末尾追加 %d 字" % (tok, len(str(a.get("markdown") or ""))), [{"tool": name, "argv": argv}]
        if name in ("calendar_create", "calendar_create_batch", "calendar_update", "calendar_delete"):
            return self._build_calendar(name, _strip_people(a), r.get("calendar_id"))
        if name == "base_create":
            if not r.get("base_token") or not r.get("table_id"): raise ToolError("还没配置可写的多维表格")
            fields = a.get("fields") or {}
            if not isinstance(fields, dict) or not fields: raise ToolError("没有要写入的字段")
            for sf in self.w.get("base_select_fields", []):
                if isinstance(fields.get(sf), str): fields[sf] = [fields[sf]]
            tf = self.w.get("base_time_field", "时间")
            if tf and tf not in fields: fields[tf] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            argv = ["base", "+record-batch-create", "--base-token", r["base_token"], "--table-id", r["table_id"],
                    "--json", json.dumps({"create_records": [fields]}, ensure_ascii=False)]
            return argv, "在团队表格新增 1 条记录：%s" % json.dumps(fields, ensure_ascii=False)[:200], [{"tool": name, "argv": argv}]
        raise ToolError("未知写入工具")

    def _build_calendar(self, name, a, cid):
        if not cid: raise ToolError("还没配置团队专用日历")
        if name == "calendar_create":
            argv, line = self._one_event(a, cid)
            return argv, "在团队日历创建日程%s（不邀请任何人）" % line, [{"tool": "calendar_create", "argv": argv}]
        if name == "calendar_create_batch":
            events = a.get("events") if isinstance(a.get("events"), list) else None
            if not events: raise ToolError("没有要创建的日程")
            cap = _cfg_int(self.w.get("max_batch"), 10)
            if cap < 1: cap = 1
            if len(events) > cap: raise ToolError("一次最多创建 %d 个日程" % cap)
            steps, lines = [], []
            for i, ev in enumerate(events, 1):
                if not isinstance(ev, dict): raise ToolError("第 %d 个日程格式不对" % i)
                argv, line = self._one_event(ev, cid)
                steps.append({"tool": "calendar_create", "argv": argv})
                lines.append("%d. %s" % (i, line))
            summary = "在团队日历一次创建 %d 个日程（不邀请任何人）：\n%s" % (len(lines), "\n".join(lines))
            return steps[0]["argv"], summary, steps
        eid = _safe_id(a.get("event_id"), "日程编号")
        if name == "calendar_delete":
            label = str(a.get("summary") or eid)[:100]
            argv = ["calendar", "+delete", "--calendar-id", cid, "--event-id", eid, "--notify=false"]
            return argv, "删除团队日历上的日程「%s」（不通知参会人，不改其他日程）" % label, [{"tool": name, "argv": argv}]
        argv = ["calendar", "+update", "--calendar-id", cid, "--event-id", eid, "--notify=false"]
        changed = False
        if a.get("summary"):
            argv += ["--summary", str(a["summary"])[:100]]; changed = True
        if a.get("description"):
            argv += ["--description", str(a["description"])[:2000]]; changed = True
        if a.get("start"):
            argv += ["--start", str(a["start"])[:40]]; changed = True
        if a.get("end"):
            argv += ["--end", str(a["end"])[:40]]; changed = True
        if not changed: raise ToolError("没有要修改的内容")
        return argv, "修改团队日历上的日程 %s（不改参会人，不邀请任何人）" % eid, [{"tool": name, "argv": argv}]

    def _one_event(self, a, cid):
        a = _strip_people(a or {})
        for k in ("summary", "start", "end"):
            if not str(a.get(k) or "").strip(): raise ToolError("日程缺少 %s" % k)
        argv = ["calendar", "+create", "--calendar-id", cid, "--summary", str(a["summary"])[:100],
                "--start", str(a["start"])[:40], "--end", str(a["end"])[:40]]
        if a.get("description"): argv += ["--description", str(a["description"])[:2000]]
        if any("attendee" in str(x).lower() for x in argv): raise ToolError("不能邀请参会人")
        return argv, "「%s」%s ~ %s" % (str(a["summary"])[:100], a["start"], a["end"])

    def _state_db(self):
        if self._db is not None: return self._db
        d = os.path.join(self.base, "state"); os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(os.path.join(d, "state.db"), check_same_thread=False)
        conn.execute("create table if not exists pending_writes(code text primary key, ts real, payload text)")
        self._db = conn
        return conn

    def _load_pending(self):
        """重启后恢复还没过期的确认码。过期的直接丢掉。"""
        ttl = int(self.w.get("confirm_ttl_sec", 300)); now = time.time()
        try: rows = list(self._state_db().execute("select code, ts, payload from pending_writes"))
        except Exception: return
        with self.pl:
            for code, ts, payload in rows:
                if now - float(ts or 0) > ttl:
                    self._drop_pending(code); continue
                try: self.pending[code] = json.loads(payload)
                except Exception: self._drop_pending(code)

    def _save_pending(self, code, rec):
        try:
            self._state_db().execute("insert or replace into pending_writes(code, ts, payload) values(?,?,?)",
                                     (code, rec.get("ts") or time.time(), json.dumps(rec, ensure_ascii=False)))
            self._state_db().commit()
        except Exception as e: self.log.warning("pending save failed: %s", e)

    def _drop_pending(self, code):
        try:
            self._state_db().execute("delete from pending_writes where code=?", (code,))
            self._state_db().commit()
        except Exception as e: self.log.warning("pending drop failed: %s", e)

    def _gc(self):
        ttl = int(self.w.get("confirm_ttl_sec", 300))
        for c in [c for c, p in self.pending.items() if time.time() - p["ts"] > ttl]:
            self.pending.pop(c, None); self._drop_pending(c)

    def confirm(self, code, ctx, cancel=False):
        with self.pl:
            self._gc(); p = self.pending.get(code)
            if not p: return "确认码无效或已过期。"
            if p["user"] != ctx["user"] or p["chat_id"] != ctx["chat_id"]: return "只有发起人本人在原会话里才能确认。"
            self.pending.pop(code); self._drop_pending(code)
        if cancel:
            self.audit(kind="write_cancelled", tool=p["tool"], user=ctx["user"], code=code); return "已取消。"
        if p.get("tool") == "memory_propose":
            hook = getattr(self, "memory_hook", None)
            if not hook:
                self.audit(kind="write_failed", tool="memory_propose", user=ctx["user"], code=code, err="no hook")
                return "记忆功能还没接上。"
            try: msg = hook(p.get("memory_text") or "", ctx)
            except Exception as e:
                self.audit(kind="write_failed", tool="memory_propose", user=ctx["user"], code=code, err=str(e)[:200])
                return "执行失败：" + str(e)[:120]
            kind = "write_done" if str(msg).startswith("已记") else "write_failed"
            self.audit(kind=kind, tool="memory_propose", user=ctx["user"], chat=ctx["chat_id"], code=code, summary=p.get("summary"), result=_brief(msg))
            return msg
        steps = p.get("steps") or [{"tool": p.get("tool"), "argv": p.get("argv") or []}]
        links, done = [], 0
        try:
            for st in steps:
                j = self.lark(st["tool"], st["argv"]); done += 1
                d = j.get("data") or {}
                link = d.get("url") or d.get("doc" + "_url") or (d.get("document") or {}).get("url") or d.get("app_link") or ""
                if link: links.append(link)
        except ToolError as e:
            self.audit(kind="write_failed", tool=p["tool"], user=ctx["user"], code=code, err=e.user_msg, detail=e.detail[:300])
            if done: return "已完成 %d/%d 步。后面的失败了：%s" % (done, len(steps), e.user_msg)
            return "执行失败：" + e.user_msg
        self.audit(kind="write_done", tool=p["tool"], user=ctx["user"], chat=ctx["chat_id"], code=code, summary=p["summary"],
                   result=_brief({"n": done, "links": links[:5]}))
        msg = "已完成：%s%s" % (p["summary"], ("\n" + "\n".join(links)) if links else "")
        if p.get("notify_all"): msg = '<at user_id="all"></at>\n' + msg
        return msg

    # ---------- 附件（图片/文件） ----------
    def fetch_attachments(self, message_id, ctx, tmp_root):
        if not self.enabled("attachment"): raise ToolError("图片和文件识别还没在配置里启用")
        d = os.path.join(tmp_root, "att_" + re.sub(r"\W", "", message_id)[-20:]); os.makedirs(d, exist_ok=True)
        j = self.lark("attachment", ["im", "+messages-mget", "--message-ids", message_id, "--download-resources", "--no-reactions"], cwd=d, timeout=120)
        files = []
        root_r = os.path.realpath(d)
        for p in glob.glob(os.path.join(d, "lark-im-resources", "**", "*"), recursive=True):
            if not os.path.isfile(p): continue
            # 下载目录之外的路径（符号链接、..）不读，避免附件名把别的文件带进提示词。
            try:
                if os.path.commonpath([root_r, os.path.realpath(p)]) != root_r: continue
            except ValueError:
                continue
            files.append(p)
        msgs = (j.get("data") or {}).get("messages") or []
        msg = msgs[0] if msgs else {}
        files = apply_original_names(files, msg)
        self.audit(kind="read", tool="attachment", user=ctx["user"], chat=ctx["chat_id"], ok=True, n=len(files))
        return d, files, (msg.get("msg_type") or msg.get("message_type") or ""), msg

    def expand_merge(self, message_id, ctx):
        """展开一条合并转发。只展开这一层；内层转发只留一句，不再请求。"""
        mid = str(message_id or "")
        if not re.match(r"^[A-Za-z0-9_]{1,80}$", mid): raise ToolError("消息编号不对")
        j = self.lark("merge_forward", ["api", "GET", "/open-apis/im/v1/messages/" + mid])
        items = _merge_items((j or {}).get("data") or {})
        if not items: return "（这条合并转发的内容按平台权限看不到明细。）"
        lines = []
        for it in items[:30]:
            it = _normalize_msg(it)
            mt = str(it.get("msg_type") or it.get("message_type") or "").lower()
            line = _history_line(it)
            if mt in ("merge_forward", "merge_forward_msg"): line += "（内层转发不再展开）"
            lines.append(line)
        more = ("\n（还有 %d 条没有展开）" % (len(items) - 30)) if len(items) > 30 else ""
        self.audit(kind="read", tool="merge_forward", user=ctx.get("user"), chat=ctx.get("chat_id"), ok=True, n=len(lines))
        return "合并转发里的消息：\n" + "\n".join(lines) + more

def _want_notify(name, args, question):
    """只有日程类、模型开了开关、并且对方原话明确要求通知所有人，才在成功回复里 @全员。"""
    if name not in ("calendar_create", "calendar_create_batch", "calendar_update", "calendar_delete"): return False
    if not isinstance(args, dict) or not args.get("notify_all"): return False
    return bool(_NOTIFY_RX.search(str(question or "")))

def _strip_people(a):
    """丢掉参会人、邀请和通知开关。日程命令不允许带这些人。"""
    if not isinstance(a, dict): return {}
    out = {}
    for k, v in a.items():
        lk = str(k).lower().replace("-", "_")
        if "attendee" in lk or lk in ("notify", "notify_all"): continue
        out[k] = v
    if isinstance(a.get("events"), list):
        out["events"] = [_strip_people(x) if isinstance(x, dict) else x for x in a["events"]]
    return out

def _safe_id(raw, label):
    s = str(raw or "").strip()
    if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,119}$", s): raise ToolError(label + "不对")
    return s

def _normalize_msg(msg):
    if not isinstance(msg, dict): return {}
    if msg.get("content") is None and isinstance(msg.get("body"), dict):
        msg = dict(msg); msg["content"] = msg["body"].get("content")
    return msg

def _merge_items(data):
    if not isinstance(data, dict): return []
    for k in ("items", "messages"):
        v = data.get(k)
        if isinstance(v, list): return [x for x in v if isinstance(x, dict)]
    inner = data.get("data")
    if isinstance(inner, dict) and inner is not data: return _merge_items(inner)
    if data.get("body") or data.get("msg_type") or data.get("message_type") or data.get("content") is not None:
        return [data]
    return []

_SKIP_WALK = {"text", "title", "content", "tag", "image_key", "file_key", "file_token", "style", "href", "user_id", "open_id"}

def _collect_text(obj, out, depth=0):
    if depth > 12 or obj is None: return
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 2 and s[0] in "{[":
            try: parsed = json.loads(s)
            except Exception: parsed = None
            if isinstance(parsed, (dict, list)):
                _collect_text(parsed, out, depth + 1); return
        if s: out.append(s)
        return
    if isinstance(obj, list):
        for x in obj: _collect_text(x, out, depth + 1)
        return
    if not isinstance(obj, dict): return
    tag = str(obj.get("tag") or "")
    if tag == "img": out.append("[图片]"); return
    if tag in ("file", "media"): out.append("[文件]"); return
    for k in ("text", "title"):
        v = obj.get(k)
        if isinstance(v, (str, dict, list)): _collect_text(v, out, depth + 1)
    content = obj.get("content")
    if isinstance(content, (str, dict, list)): _collect_text(content, out, depth + 1)
    for k, v in obj.items():
        if k in _SKIP_WALK: continue
        if isinstance(v, (dict, list)): _collect_text(v, out, depth + 1)

def _join_parts(parts):
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", str(p)).strip()
        if not p or (out and out[-1] == p): continue
        out.append(p)
    return "\n".join(out)

def message_text(msg):
    """一条消息里给人读的文字。图片、文件、合并转发留占位，不把 file_key 当正文。"""
    msg = _normalize_msg(msg)
    parts = []
    _collect_text(msg.get("content"), parts, 0)
    text = _join_parts(parts)
    mtype = str(msg.get("msg_type") or msg.get("message_type") or "").lower()
    extras = []
    if mtype in ("image", "media") and "[图片]" not in text: extras.append("[图片]")
    if mtype == "file" and "[文件]" not in text: extras.append("[文件]")
    if mtype == "post" and not text: extras.append("[富文本]")
    if mtype in ("merge_forward", "merge_forward_msg") and "[合并转发]" not in text: extras.append("[合并转发]")
    if mtype in ("interactive", "card") and not text: extras.append("[卡片]")
    if extras: text = (text + " " + " ".join(extras)).strip()
    return text

def _sender_name(m):
    s = m.get("sender") if isinstance(m, dict) else None
    if isinstance(s, dict): return str(s.get("name") or s.get("id") or "")
    if isinstance(s, str): return s
    return str((m or {}).get("sender_id") or "") if isinstance(m, dict) else ""

def _history_line(m):
    if not isinstance(m, dict): return str(m or "")
    m = _normalize_msg(m)
    ts = str(m.get("create_time") or m.get("create_time_ms") or "")
    return "%s %s：%s" % (ts, _sender_name(m), message_text(m))

def _history_blob(m):
    if not isinstance(m, dict): return ""
    bits = [message_text(m)]
    for r in (m.get("thread_replies") or m.get("replies") or []):
        if isinstance(r, dict): bits.append(message_text(r))
    return "\n".join(x for x in bits if x)

def _allow_token(doc, allow):
    """白名单要整段对上，不能只是子串。doxTEST 不能放行 doxTESTxxx。链接里独立的一段可以。"""
    raw = str(doc or "").strip()
    if not raw: return None
    for t in allow or ():
        t = str(t or "").strip()
        if not t: continue
        if raw == t or re.search(r"(^|[/=])" + re.escape(t) + r"(?=$|[/?#&])", raw): return t
    return None

def _brief(x):
    s = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
    return s[:300]

def _cfg_int(raw, default):
    try: n = int(raw)
    except (TypeError, ValueError): return default
    return n

def _json_from_text(text):
    """从一段可能夹着日志的输出里取出 lark-cli 的 JSON。嵌套的 error 对象不要盖住外层。"""
    if not text: return None
    s = str(text)
    dec = json.JSONDecoder()
    found = None
    i = 0
    while True:
        j = s.find("{", i)
        if j < 0: break
        try:
            obj, _end = dec.raw_decode(s, j)
        except Exception:
            i = j + 1
            continue
        if isinstance(obj, dict) and ("ok" in obj or "error" in obj):
            return obj
        if found is None and isinstance(obj, dict) and obj.get("code") is not None:
            found = obj
        i = j + 1
    return found

def parse_cli_json(stdout, stderr):
    """lark-cli 成功时 JSON 在 stdout；失败时经常只写在 stderr。两边都试。"""
    j_out = _json_from_text(stdout)
    j_err = _json_from_text(stderr)
    if j_out and j_out.get("ok"): return j_out
    if j_err and ("ok" in j_err or j_err.get("error") or j_err.get("code") is not None):
        if not j_out or (j_err.get("error") and not j_out.get("error") and not j_out.get("ok")):
            return j_err
    return j_out or j_err

def _err_code(j):
    e = j.get("error") if isinstance(j, dict) else None
    if not isinstance(e, dict): e = {}
    code = e.get("code", j.get("code") if isinstance(j, dict) else None)
    try:
        if code is not None and str(code).lstrip("-").isdigit(): return int(code)
    except (TypeError, ValueError): pass
    return code

def explain_lark_error(tool, j, bot_name="机器人"):
    """把飞书错误码说成具体原因。权限、不存在、不是文档，不要都说成接口不可用。"""
    e = (j or {}).get("error") if isinstance(j, dict) else None
    if not isinstance(e, dict): e = {}
    code = _err_code(j or {})
    msg = str(e.get("message") or (j or {}).get("msg") or "")
    low = msg.lower()
    if e.get("subtype") in ("app_scope_not_applied", "missing_scope") or code in (99991672, 99991679) or "scope" in low:
        return "这项权限未开（需要管理员在开放平台开通 %s 并发版）" % SCOPE_HINT.get(tool, tool)
    if code == 3380002 or "不是文档" in msg or "not a document" in low or "not a doc" in low:
        return "没有找到这份文档（不存在，或这个编号不是云文档）"
    if code in _NOT_FOUND_CODES or re.search(r"not found|not exist|does not exist|doesn't exist|不存在|已被删除", low + msg):
        return "没有找到这份资源（不存在或已被删除）"
    if code in _PERM_CODES or "permission" in low or "forbidden" in low or "no access" in low or "无权限" in msg or "没有权限" in msg:
        return "机器人没有访问这份资源的权限（需要把它分享给应用「%s」）" % (bot_name or "机器人")
    short = re.sub(r"\s+", " ", msg).strip()
    if short and short.lower() not in ("null", "none"):
        return "飞书接口调用失败：" + short[:120]
    return "飞书接口调用失败，请稍后再试"

def is_chat_file_token(doc):
    """聊天附件的 file_key / image_key。云文档 token 不是这种前缀。"""
    raw = str(doc or "").strip()
    if not raw: return False
    if re.match(r"^(file_|img_)", raw, re.I): return True
    return bool(re.search(r"(?:^|[/=])(?:file_|img_)[A-Za-z0-9]", raw, re.I))

def clip_body(text, limit):
    """正文没超过上限就原样返回。超过则留开头、中段和结尾，并注明总长度。limit<=0 表示不截。"""
    text = text or ""
    limit = _cfg_int(limit, DEFAULT_BODY_CHARS)
    if limit <= 0 or len(text) <= limit: return text
    note = "（全文约 %d 字，超过上限 %d 字，下面是开头、中段和结尾，请分段概括）" % (len(text), limit)
    overhead = len(note) + len("【开头】\n\n【中段】\n\n【结尾】\n")
    if limit <= overhead + 12:
        return text[:max(0, limit)] + "\n" + note
    seg = max(4, (limit - overhead) // 3)
    mid = len(text) // 2
    half = seg // 2
    middle = text[max(0, mid - half): max(0, mid - half) + seg]
    return "\n".join([note, "【开头】", text[:seg], "【中段】", middle, "【结尾】", text[-seg:]])

def safe_download_name(name):
    """只用文件名本身，去掉路径和不可见字符，避免附件名把别的文件带进来。"""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "", name).strip().strip(".")
    if not name or name in (".", ".."): return None
    if len(name) > 80:
        base, ext = os.path.splitext(name)
        name = base[:70] + ext[:10]
    return name

def _walk_names(obj, pairs):
    if isinstance(obj, dict):
        name = obj.get("file_name") or obj.get("filename") or obj.get("name")
        key = obj.get("file_key") or obj.get("image_key") or ""
        if isinstance(name, str) and name.strip():
            safe = safe_download_name(name)
            if safe: pairs.append((str(key or ""), safe))
        for v in obj.values(): _walk_names(v, pairs)
    elif isinstance(obj, list):
        for v in obj: _walk_names(v, pairs)
    elif isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "[{":
            try:
                _walk_names(json.loads(s), pairs); return
            except Exception:
                pass
        names = [safe_download_name(n) for n in _NAME_ATTR.findall(s)]
        names = [n for n in names if n]
        keys = _KEY_ATTR.findall(s)
        if names and keys and len(names) == len(keys):
            pairs.extend((k, n) for k, n in zip(keys, names))
        else:
            pairs.extend(("", n) for n in names)

def attachment_names(msg):
    pairs = []
    if isinstance(msg, dict): _walk_names(msg.get("content"), pairs)
    seen = set(); out = []
    for key, name in pairs:
        mark = (key, name)
        if mark in seen: continue
        seen.add(mark); out.append(mark)
    return out

def apply_original_names(files, msg):
    """下载文件名常常是 file_key。改成消息里的原文件名，模型和提示词都不再看到 file_key。"""
    pairs = attachment_names(msg)
    if not files or not pairs: return files
    used = set(); renamed = []
    for p in files:
        base = os.path.basename(p)
        pick = None
        for key, name in pairs:
            if name in used: continue
            if key and key in base:
                pick = name; break
        if pick is None and len([x for x in files]) == 1 and len(pairs) == 1 and pairs[0][1] not in used:
            pick = pairs[0][1]
        if not pick:
            renamed.append(p); continue
        used.add(pick)
        dest = os.path.join(os.path.dirname(p), pick)
        if os.path.abspath(dest) != os.path.abspath(p):
            stem, ext = os.path.splitext(pick); n = 1
            while os.path.exists(dest):
                dest = os.path.join(os.path.dirname(p), "%s_%d%s" % (stem, n, ext)); n += 1
            try: os.replace(p, dest); p = dest
            except OSError: pass
        renamed.append(p)
    return renamed

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

def _xml_root(blob):
    import xml.etree.ElementTree as ET
    return ET.fromstring(blob)

def extract_docx(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = _xml_root(xml)
    paras = []
    for p in root.iter(_W + "p"):
        chunks = []
        for node in p.iter():
            if node.tag == _W + "t" and node.text: chunks.append(node.text)
            elif node.tag == _W + "tab": chunks.append("\t")
            elif node.tag == _W + "br": chunks.append("\n")
        line = "".join(chunks).strip()
        if line: paras.append(line)
    return "\n".join(paras)

def extract_pptx(path):
    import zipfile
    texts = []
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for i, name in enumerate(slides, 1):
            root = _xml_root(z.read(name))
            bits = [t.text for t in root.iter(_A + "t") if t.text]
            if bits: texts.append("【第 %d 页】\n%s" % (i, "\n".join(bits)))
    return "\n\n".join(texts)

def extract_xlsx(path):
    """有 openpyxl 就读成表格文本。没安装返回 None，调用方给出明确提示。"""
    try: import openpyxl
    except ImportError: return None
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 200:
                    rows.append("…（只列出前 200 行）"); break
                cells = ["" if c is None else str(c) for c in row]
                if any(x.strip() for x in cells): rows.append("\t".join(cells))
            if rows: out.append("【%s】\n%s" % (ws.title, "\n".join(rows)))
        return "\n\n".join(out)
    finally:
        wb.close()

def office_text(path):
    """返回 (kind, text)。text 为 None 表示 xlsx 但没有 openpyxl。kind 为 err 表示文件打不开。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx": return "docx", extract_docx(path)
        if ext == ".pptx": return "pptx", extract_pptx(path)
        if ext == ".xlsx": return "xlsx", extract_xlsx(path)
    except Exception:
        return "err", ""
    return "", ""
