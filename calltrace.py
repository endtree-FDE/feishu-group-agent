# 每轮回答的调用链。只写本机 logs/calls.jsonl，不外传。
# 借鉴的是「一次请求一条轨迹、每步记耗时和 token」，不接外部采集，也不另起收消息进程。
# 旧配置没写 observability.calls 时默认记。写成 false，或 enabled 为 false，才不记。
import hashlib, json, os, re, threading, time
import guard

_SKILL = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOOL = re.compile(r"^[a-z_]{1,40}$")
_RECENT = re.compile(r"^(?:最近调用|最近的调用|调用记录)\s*$")
_WHY = re.compile(r"^(?:这条怎么答的|这条是怎么答的|上一条怎么答的|怎么答的)\s*$")
_FEISHU_ID = re.compile(r"(?i)\b(?:ou|on|oc|om|cli)_[A-Za-z0-9]{4,}\b")
_UNIX = re.compile(r"(?:/home/|/Users/|/root/)[^\s，。；、：）)》」\"'`]*")
_TLS = threading.local()
_DEFAULT_DAYS = 30
_TEXT_LIMIT = 180
_STEP_LIMIT = 30


def looks_like(text):
    q = (text or "").strip()
    return bool(_RECENT.match(q) or _WHY.match(q))


def settings(cfg):
    """缺省即开启。天数写错、小于 1 或大于十年，按 30 天。"""
    obs = (cfg or {}).get("observability") if isinstance(cfg, dict) else None
    if not isinstance(obs, dict) or "calls" not in obs:
        return {"enabled": True, "retain_days": _DEFAULT_DAYS}
    raw = obs.get("calls")
    if raw is False:
        return {"enabled": False, "retain_days": _DEFAULT_DAYS}
    if raw is True or raw is None or not isinstance(raw, dict):
        return {"enabled": True, "retain_days": _DEFAULT_DAYS}
    enabled = True if "enabled" not in raw else bool(raw.get("enabled"))
    days = raw.get("retain_days", _DEFAULT_DAYS)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = _DEFAULT_DAYS
    if days < 1 or days > 3650:
        days = _DEFAULT_DAYS
    return {"enabled": enabled, "retain_days": days}


def enabled(cfg):
    return settings(cfg)["enabled"]


def reset_tokens():
    _TLS.pin = 0
    _TLS.pout = 0


def add_tokens(prompt, completion):
    """模型接口报上来的用量。只累加整数，给当前这轮的模型步骤用。"""
    try:
        pin = int(prompt or 0)
        pout = int(completion or 0)
    except (TypeError, ValueError):
        return
    if pin < 0:
        pin = 0
    if pout < 0:
        pout = 0
    _TLS.pin = int(getattr(_TLS, "pin", 0) or 0) + pin
    _TLS.pout = int(getattr(_TLS, "pout", 0) or 0) + pout


def take_tokens():
    pin = int(getattr(_TLS, "pin", 0) or 0)
    pout = int(getattr(_TLS, "pout", 0) or 0)
    _TLS.pin = 0
    _TLS.pout = 0
    _TLS.last = (pin, pout)
    return pin, pout


def last_tokens():
    """最近一次取走的用量。没有报上来时是 (0, 0)，调用方再按字数粗算。"""
    got = getattr(_TLS, "last", None)
    if not got:
        return 0, 0
    return int(got[0] or 0), int(got[1] or 0)


def _ref(value):
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _clip(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= _TEXT_LIMIT:
        return text
    return text[:_TEXT_LIMIT].rstrip() + "…"


def clean_text(text, names, hits=None):
    """存盘前脱敏。客户名换成「某客户」，密钥、手机号、路径和编号都不留原文。"""
    cleaned, n_hit = guard.redact_counted(text or "", names, placeholder="某客户")
    if hits is not None:
        hits[0] += n_hit
    cleaned = _FEISHU_ID.sub("[编号已隐藏]", cleaned)
    cleaned = _UNIX.sub("[本地路径已隐藏]", cleaned)
    cleaned = guard.neutralize(cleaned)
    return _clip(cleaned)


def _skill_name(name):
    text = str(name or "").strip()
    if _SKILL.match(text) and len(text) <= 64:
        return text
    return ""


def _tool_name(name):
    text = str(name or "").strip()
    if _TOOL.match(text):
        return text
    return ""


class Book:
    def __init__(self, log_dir, names=(), cfg=None):
        self.log_dir = log_dir
        self.path = os.path.join(log_dir, "calls.jsonl")
        self.names = names
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self._lock = threading.Lock()
        self._open = {}

    def begin(self, mid, text, chat="group", parent="", traffic="", now=None):
        # 账本关着也先放在内存。这一轮结束时如果不记，就不落盘。
        # 复盘要读刚结束的步骤，不能因为没写文件就丢了这一轮。
        now = time.time() if now is None else float(now)
        hits = [0]
        received = clean_text(text, self.names, hits)
        rec = {
            "mid": str(mid or ""),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "epoch": now,
            "chat": "p2p" if chat == "p2p" else "group",
            "ref": _ref(mid),
            "parent": _ref(parent),
            "traffic": traffic if traffic in ("live", "test", "replay") else "",
            "skills": [],
            "steps": [{
                "kind": "receive", "elapsed_ms": 0, "tokens_in": 0, "tokens_out": 0, "text": received,
            }],
            "hits": hits[0],
        }
        with self._lock:
            self._open[str(mid or "")] = rec

    def skill(self, mid, name):
        name = _skill_name(name)
        if not name:
            return
        with self._lock:
            rec = self._open.get(str(mid or ""))
            if not rec:
                return
            if name in rec["skills"]:
                return
            rec["skills"].append(name)
            rec["steps"].append({"kind": "skill", "name": name, "elapsed_ms": 0, "tokens_in": 0, "tokens_out": 0})
            rec["steps"] = rec["steps"][:_STEP_LIMIT]

    def tool(self, mid, name, ok, elapsed, result="", skill=""):
        name = _tool_name(name)
        if not name:
            return
        if skill:
            self.skill(mid, skill)
        hits = [0]
        shown = clean_text(result, self.names, hits)
        with self._lock:
            rec = self._open.get(str(mid or ""))
            if not rec:
                return
            rec["hits"] += hits[0]
            rec["steps"].append({
                "kind": "tool", "name": name, "ok": bool(ok),
                "elapsed_ms": _ms(elapsed), "tokens_in": 0, "tokens_out": 0, "result": shown,
            })
            rec["steps"] = rec["steps"][:_STEP_LIMIT]

    def model(self, mid, elapsed, tokens_in, tokens_out, result=""):
        hits = [0]
        shown = clean_text(result, self.names, hits)
        with self._lock:
            rec = self._open.get(str(mid or ""))
            if not rec:
                return
            rec["hits"] += hits[0]
            rec["steps"].append({
                "kind": "model", "ok": True, "elapsed_ms": _ms(elapsed),
                "tokens_in": _count(tokens_in), "tokens_out": _count(tokens_out), "result": shown,
            })
            rec["steps"] = rec["steps"][:_STEP_LIMIT]

    def finish(self, mid, ok, elapsed, result="", traffic="", now=None):
        now = time.time() if now is None else float(now)
        with self._lock:
            rec = self._open.pop(str(mid or ""), None)
        if not rec:
            return None
        hits = [rec.get("hits") or 0]
        outcome = clean_text(result, self.names, hits)
        pin = sum(_count(step.get("tokens_in")) for step in rec["steps"])
        pout = sum(_count(step.get("tokens_out")) for step in rec["steps"])
        row = {
            "kind": "call",
            "ts": rec["ts"],
            "epoch": rec["epoch"],
            "chat": rec["chat"],
            "ref": rec["ref"],
            "parent": rec["parent"],
            "ok": bool(ok),
            "elapsed_ms": _ms(elapsed),
            "traffic": traffic if traffic in ("live", "test", "replay") else rec.get("traffic") or "",
            "skills": list(rec["skills"]),
            "tokens_in": pin,
            "tokens_out": pout,
            "steps": [{k: v for k, v in step.items() if k != "mid"} for step in rec["steps"]],
            "result": outcome,
        }
        if hits[0] > 0:
            row["redact_hits"] = hits[0]
        if enabled(self.cfg):
            self._append(row, now)
        return row

    def _append(self, row, now):
        days = settings(self.cfg)["retain_days"]
        cutoff = float(now) - days * 86400
        os.makedirs(self.log_dir, exist_ok=True)
        kept = []
        with self._lock:
            if os.path.isfile(self.path):
                try:
                    with open(self.path, encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                old = json.loads(line)
                            except ValueError:
                                continue
                            try:
                                epoch = float(old.get("epoch") or 0)
                            except (TypeError, ValueError):
                                epoch = 0
                            if epoch >= cutoff:
                                kept.append(old)
                except OSError:
                    kept = []
            try:
                epoch = float(row.get("epoch") or 0)
            except (TypeError, ValueError):
                epoch = 0
            if epoch >= cutoff:
                kept.append(row)
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    for item in kept:
                        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                os.replace(tmp, self.path)
            except OSError:
                return

    def load(self):
        rows = []
        if not os.path.isfile(self.path):
            return rows
        try:
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and item.get("kind") == "call":
                        rows.append(item)
        except OSError:
            return []
        return rows


def _ms(seconds):
    """调用方传秒。负的或写错记 0。"""
    try:
        n = int(round(float(seconds) * 1000))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _count(value):
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def render_call(rec, brief=False):
    when = str(rec.get("ts") or "")
    where = "私聊" if rec.get("chat") == "p2p" else "群聊"
    state = "答成了" if rec.get("ok") else "没答成"
    mark = ""
    if rec.get("traffic") == "test":
        mark = "演练 "
    elif rec.get("traffic") == "replay":
        mark = "补答 "
    elapsed = _count(rec.get("elapsed_ms")) / 1000.0
    skills = "、".join(rec.get("skills") or []) or "无"
    tools = []
    for step in rec.get("steps") or []:
        if step.get("kind") == "tool" and step.get("name"):
            tools.append(step["name"] if step.get("ok", True) else step["name"] + "失败")
    lines = ["%s%s %s %s %.1f秒 输入%d 输出%d" % (
        mark, when, where, state, elapsed, _count(rec.get("tokens_in")), _count(rec.get("tokens_out")))]
    lines.append("技能：%s" % skills)
    lines.append("工具：%s" % ("、".join(tools) or "无"))
    if brief:
        received = ""
        for step in rec.get("steps") or []:
            if step.get("kind") == "receive":
                received = step.get("text") or ""
        if received:
            lines.append("收到：%s" % received)
        if rec.get("result"):
            lines.append("结果：%s" % rec.get("result"))
        return "\n".join(lines)
    for step in rec.get("steps") or []:
        kind = step.get("kind")
        if kind == "receive":
            lines.append("收到：%s" % (step.get("text") or "（空）"))
        elif kind == "tool":
            lines.append("工具 %s %s %.3f秒：%s" % (
                step.get("name") or "工具", "成功" if step.get("ok") else "失败",
                _count(step.get("elapsed_ms")) / 1000.0, step.get("result") or "（无结果）"))
        elif kind == "model":
            lines.append("模型 %.3f秒 输入%d 输出%d：%s" % (
                _count(step.get("elapsed_ms")) / 1000.0,
                _count(step.get("tokens_in")), _count(step.get("tokens_out")),
                step.get("result") or "（无结果）"))
    lines.append("结果：%s" % (rec.get("result") or "（无）"))
    return "\n".join(lines)


def _book(base, names, cfg):
    return Book(os.path.join(base, "logs"), names, cfg)


def recent_text(base, names, cfg, limit=5):
    rows = _book(base, names, cfg).load()
    if not rows:
        return "这台电脑上还没有调用记录。"
    picked = list(reversed(rows[-max(1, int(limit)):]))
    lines = ["最近 %d 条调用（只在本机，已脱敏）：" % len(picked)]
    for index, rec in enumerate(picked, 1):
        lines.append("")
        lines.append("%d. %s" % (index, render_call(rec, brief=True).replace("\n", "\n   ")))
    return "\n".join(lines)


def explain_text(base, names, cfg, parent=""):
    rows = _book(base, names, cfg).load()
    if not rows:
        return "这台电脑上还没有调用记录。"
    want = _ref(parent)
    found = None
    if want:
        for rec in reversed(rows):
            if rec.get("ref") == want or rec.get("parent") == want:
                found = rec
                break
    if found is None:
        found = rows[-1]
    return "这条是这样答的（只在本机，已脱敏）：\n" + render_call(found, brief=False)


def handle_text(cfg, base, text, ctx, names=(), owner=False):
    """是查看命令就返回回复，否则返回 None。群里和不认识的人不贴明细。"""
    q = (text or "").strip()
    if not looks_like(q):
        return None
    ctx = ctx or {}
    if ctx.get("chat_type") != "p2p":
        return "调用记录只在私聊里给主人看。请主人私聊我，发「最近调用」或「这条怎么答的」。群里不贴明细。"
    if not owner:
        return "只有主人可以查看调用记录。"
    if not enabled(cfg):
        return "调用记录现在关着，所以没有可看的内容。"
    if _WHY.match(q):
        return explain_text(base, names, cfg, (ctx or {}).get("parent_id") or "")
    return recent_text(base, names, cfg)


def summarize(path, start, end):
    """给用量周报的合计。演练和补答不算。没有记录就返回 None，周报不多出这一段。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return None
    n = fail = pin = pout = 0
    tools, skills = {}, {}
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return None
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("kind") != "call":
                continue
            if row.get("traffic") in ("test", "replay"):
                continue
            try:
                epoch = float(row.get("epoch") or 0)
            except (TypeError, ValueError):
                continue
            if epoch < start or epoch >= end:
                continue
            n += 1
            if not row.get("ok"):
                fail += 1
            pin += _count(row.get("tokens_in"))
            pout += _count(row.get("tokens_out"))
            for name in row.get("skills") or []:
                name = _skill_name(name)
                if name:
                    skills[name] = skills.get(name, 0) + 1
            for step in row.get("steps") or []:
                if step.get("kind") != "tool":
                    continue
                name = _tool_name(step.get("name"))
                if name:
                    tools[name] = tools.get(name, 0) + 1
    if n <= 0:
        return None
    return {"n": n, "fail": fail, "tokens_in": pin, "tokens_out": pout, "tools": tools, "skills": skills}


def summary_lines(summary):
    if not summary or not summary.get("n"):
        return []
    tool_bits = "、".join("%s %d" % (name, summary["tools"][name]) for name in sorted(summary.get("tools") or {})) or "无"
    skill_bits = "、".join("%s %d" % (name, summary["skills"][name]) for name in sorted(summary.get("skills") or {})) or "无"
    return [
        "",
        "调用记录（本机合计，已脱敏）",
        "本周 %d 条，没答成 %d 条。输入 token %d，输出 token %d。" % (
            summary["n"], summary["fail"], summary["tokens_in"], summary["tokens_out"]),
        "用过的工具：%s" % tool_bits,
        "用过的技能：%s" % skill_bits,
        "明细不在周报里。主人可以私聊发「最近调用」。",
    ]
