# 工具层：只允许白名单内的 lark-cli 命令，统一 --as bot；写操作需确认码；全部写审计日志；缺权限优雅降级。
import json, os, re, subprocess, time, random, threading, datetime, glob, shutil
import skills

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
    "calendar_create": "calendar:calendar.event:create", "base_create": "base:record:create",
}
READ_TOOLS = {"history", "docs_read", "docs_search", "calendar_read", "base_read"}
WRITE_TOOLS = {"docs_create", "docs_append", "calendar_create", "base_create"}
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

TOOL_DOC = """可用工具（每次最多调用一个；需要时只输出一行：<<TOOL>>{"tool":"名称","args":{...}}，不要输出别的）：
- history {"keyword":"可选关键词"}：读取本群最近 7 天消息（仅群聊可用）
- docs_search {"query":"关键词"}：搜索已分享给机器人的飞书文档
- docs_read {"doc":"文档链接或 token","keyword":"可选"}：读取飞书文档内容
- calendar_read {"start":"ISO8601","end":"ISO8601"}：查看团队日历日程
- base_read {"keyword":"可选"}：查询团队多维表格记录
- docs_create {"title":"标题","markdown":"正文"}：在团队专用文件夹新建文档（需发起人确认）
- docs_append {"doc":"文档 token","markdown":"追加内容"}：在白名单文档末尾追加（需确认）
- calendar_create {"summary":"标题","start":"ISO8601","end":"ISO8601","description":"可选"}：在团队日历建日程（需确认）
- base_create {"fields":{"字段名":"值"}}：在团队多维表格新增一条记录（需确认）
没有必要就不要调用工具，直接回答。
重要：你处于只读模式，不能改本地文件，但飞书的新建/追加/建日程/加记录不需要你自己执行——只要输出上面那一行 <<TOOL>>，机器人会代为执行（写操作先让发起人回复确认码）。用户要求写飞书时，直接输出对应工具行，绝不要回答“切换到可编辑模式”或“无法创建”。"""

class Tools:
    def __init__(self, cfg, base, lark_exe, log, dry=False):
        self.cfg, self.base, self.lark_exe, self.log, self.dry = cfg, base, lark_exe, log, dry
        self.legacy_hints = apply_legacy_names(cfg)
        self.t = cfg.get("tools", {}); self.w = cfg.get("write", {})
        self.audit_dir = os.path.join(base, "audit"); os.makedirs(self.audit_dir, exist_ok=True)
        self.pending = {}; self.pl = threading.Lock(); self.al = threading.Lock()
        self.fake = None  # 测试注入：callable(argv)->dict
        self.skills = skills.SkillSet(cfg, base)  # 技能目录；enabled 默认 false

    # ---------- 基础 ----------
    def enabled(self, name): return bool(self.t.get(name, {}).get("enabled", False))
    def audit(self, **kw):
        kw["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
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
                try: j = json.loads(out[out.find("{"):]) if "{" in out else {}
                except Exception: j = {}
                if not j: j = {"ok": False, "error": {"message": (out[:200] + " | " + err[-300:])}}
                if j.get("ok") or not re.search(r"EOF|timeout|unreachable|connection|reset", json.dumps(j.get("error"), ensure_ascii=False), re.I): break
                self.log.warning("lark %s retry %d: %s", " ".join(argv[:2]), attempt + 1, str(j.get("error"))[:200]); time.sleep(3 * (attempt + 1))
            if not j.get("ok"): self.log.warning("lark %s failed: %s", " ".join(argv[:2]), json.dumps(j.get("error"), ensure_ascii=False)[:400])
        if not j.get("ok"):
            e = j.get("error") or {}
            msg = str(e.get("message", ""))
            if e.get("subtype") in ("app_scope_not_applied", "missing_scope") or e.get("code") in (99991672, 99991679) or "scope" in msg.lower():
                raise ToolError("这项权限未开（需要管理员在开放平台开通 %s 并发版）" % SCOPE_HINT.get(tool, tool), msg)
            if e.get("code") in (1770032, 91403, 131006) or "permission" in msg.lower() or "forbidden" in msg.lower() or "no access" in msg.lower():
                raise ToolError("机器人没有访问这份资源的权限（需要把它分享给应用「%s」）" % self.cfg.get("bot_names", ["机器人"])[0], msg)
            raise ToolError("飞书接口调用失败，请稍后再试", msg)
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
        kw = (a.get("keyword") or "").strip(); lines = []
        for m in msgs:
            c = (m.get("content") or "").replace("\n", " ")
            if kw and kw not in c: continue
            s = m.get("sender") or {}
            lines.append("%s %s：%s" % (m.get("create_time", ""), s.get("name") or s.get("sender_type", ""), c[:200]))
        return "本群最近 %d 天消息（%d 条%s）：\n" % (days, len(lines), "，关键词=" + kw if kw else "") + "\n".join(lines[:150])

    def _docs_search(self, a, ctx):
        q = (a.get("query") or "")[:30]
        j = self.lark("docs_search", ["drive", "+search", "--query", q, "--page-size", "10"])
        items = (j.get("data") or {}).get("items") or (j.get("data") or {}).get("docs") or []
        return "文档搜索结果：\n" + json.dumps(items, ensure_ascii=False)[:4000]

    def _docs_read(self, a, ctx):
        doc = str(a.get("doc") or "").strip()
        if not doc: raise ToolError("没有给出文档链接")
        allow = self.t["docs_read"].get("allow_tokens")  # 为空表示：机器人能看到的都能读
        if allow and not any(t in doc for t in allow): raise ToolError("这份文档不在可读白名单里")
        argv = ["docs", "+fetch", "--doc", doc, "--doc-format", "markdown"]
        if a.get("keyword"): argv += ["--scope", "keyword", "--keyword", str(a["keyword"])[:50]]
        j = self.lark("docs_read", argv)
        d = j.get("data") or {}
        text = d.get("markdown") or d.get("content") or json.dumps(d, ensure_ascii=False)
        return "文档内容（节选）：\n" + text[: int(self.t["docs_read"].get("max_chars", 8000))]

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
        argv, summary = self._build_write(name, args or {})
        code = "%06d" % random.SystemRandom().randint(0, 999999)
        with self.pl:
            self._gc()
            self.pending[code] = {"tool": name, "argv": argv, "summary": summary, "user": ctx["user"],
                                  "chat_id": ctx["chat_id"], "ts": time.time()}
        self.audit(kind="write_proposed", tool=name, user=ctx["user"], chat=ctx["chat_id"], code=code, summary=summary)
        ttl = int(self.w.get("confirm_ttl_sec", 300)) // 60
        return "将要执行：%s\n请发起人在 %d 分钟内 @我 回复「确认 %s」执行，或「取消 %s」。" % (summary, ttl, code, code)

    def _build_write(self, name, a):
        r = self.w.get("resources", {})
        if name == "docs_create":
            folder = r.get("folder_token")
            if not folder: raise ToolError("还没配置专用文档文件夹")
            title = str(a.get("title") or "未命名")[:80]
            return (["docs", "+create", "--parent-token", folder, "--title", title, "--doc-format", "markdown",
                     "--content", str(a.get("markdown") or "")[:20000]], "在专用文件夹新建文档《%s》" % title)
        if name == "docs_append":
            doc = str(a.get("doc") or ""); allow = r.get("docs_append_tokens") or []
            if not allow: raise ToolError("还没配置允许追加的文档")
            tok = next((t for t in allow if t in doc), None)
            if not tok: raise ToolError("这份文档不在可写白名单里")
            return (["docs", "+update", "--doc", tok, "--command", "append", "--doc-format", "markdown",
                     "--content", str(a.get("markdown") or "")[:20000]], "在文档 %s 末尾追加 %d 字" % (tok, len(str(a.get("markdown") or ""))))
        if name == "calendar_create":
            cid = r.get("calendar_id")
            if not cid: raise ToolError("还没配置团队专用日历")
            for k in ("summary", "start", "end"):
                if not a.get(k): raise ToolError("日程缺少 %s" % k)
            argv = ["calendar", "+create", "--calendar-id", cid, "--summary", str(a["summary"])[:100], "--start", str(a["start"]), "--end", str(a["end"])]
            if a.get("description"): argv += ["--description", str(a["description"])[:2000]]
            return argv, "在团队日历创建日程「%s」%s ~ %s（不邀请任何人）" % (a["summary"], a["start"], a["end"])
        if name == "base_create":
            if not r.get("base_token") or not r.get("table_id"): raise ToolError("还没配置可写的多维表格")
            fields = a.get("fields") or {}
            if not isinstance(fields, dict) or not fields: raise ToolError("没有要写入的字段")
            for sf in self.w.get("base_select_fields", []):
                if isinstance(fields.get(sf), str): fields[sf] = [fields[sf]]
            tf = self.w.get("base_time_field", "时间")
            if tf and tf not in fields: fields[tf] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            return (["base", "+record-batch-create", "--base-token", r["base_token"], "--table-id", r["table_id"],
                     "--json", json.dumps({"create_records": [fields]}, ensure_ascii=False)], "在团队表格新增 1 条记录：%s" % json.dumps(fields, ensure_ascii=False)[:200])
        raise ToolError("未知写入工具")

    def _gc(self):
        ttl = int(self.w.get("confirm_ttl_sec", 300))
        for c in [c for c, p in self.pending.items() if time.time() - p["ts"] > ttl]: self.pending.pop(c, None)

    def confirm(self, code, ctx, cancel=False):
        with self.pl:
            self._gc(); p = self.pending.get(code)
            if not p: return "确认码无效或已过期。"
            if p["user"] != ctx["user"] or p["chat_id"] != ctx["chat_id"]: return "只有发起人本人在原会话里才能确认。"
            self.pending.pop(code)
        if cancel:
            self.audit(kind="write_cancelled", tool=p["tool"], user=ctx["user"], code=code); return "已取消。"
        try:
            j = self.lark(p["tool"], p["argv"])
            self.audit(kind="write_done", tool=p["tool"], user=ctx["user"], chat=ctx["chat_id"], code=code, summary=p["summary"],
                       result=_brief(j.get("data")))
            d = j.get("data") or {}
            link = d.get("url") or d.get("doc" + "_url") or (d.get("document") or {}).get("url") or d.get("app_link") or ""
            return "已完成：%s%s" % (p["summary"], ("\n" + link) if link else "")
        except ToolError as e:
            self.audit(kind="write_failed", tool=p["tool"], user=ctx["user"], code=code, err=e.user_msg, detail=e.detail[:300])
            return "执行失败：" + e.user_msg

    # ---------- 附件（图片/文件） ----------
    def fetch_attachments(self, message_id, ctx, tmp_root):
        if not self.enabled("attachment"): raise ToolError("图片和文件识别还没在配置里启用")
        d = os.path.join(tmp_root, "att_" + re.sub(r"\W", "", message_id)[-20:]); os.makedirs(d, exist_ok=True)
        j = self.lark("attachment", ["im", "+messages-mget", "--message-ids", message_id, "--download-resources", "--no-reactions"], cwd=d, timeout=120)
        files = [p for p in glob.glob(os.path.join(d, "lark-im-resources", "**", "*"), recursive=True) if os.path.isfile(p)]
        self.audit(kind="read", tool="attachment", user=ctx["user"], chat=ctx["chat_id"], ok=True, n=len(files))
        msgs = (j.get("data") or {}).get("messages") or []
        return d, files, (msgs[0].get("msg_type") if msgs else "")

def _brief(x):
    s = json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x
    return s[:300]
