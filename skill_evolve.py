# 技能自动改进：根据调用记录和复盘提议新版本。默认关。
# 只有回归分数不下降，并且主人用原来的确认方式点头，才替换 SKILL.md。
# 不执行技能里的脚本，不另开收消息进程。回滚也要再确认一次。
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
import guard, skills

_IMPROVE = re.compile(r"^(?:改进技能|看看技能改进|技能改进)\s*$")
_ROLLBACK = re.compile(r"^回滚技能\s+([a-z0-9]+(?:-[a-z0-9]+)*)\s*$")
_TOOL = re.compile(r"^[a-z_]{1,40}$")
_CASE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FEISHU_ID = re.compile(r"(?i)\b(?:ou|on|oc|om|cli)_[A-Za-z0-9]{4,}\b")
_UNIX = re.compile(r"(?:/home/|/Users/|/root/)[^\s，。；、：）)》」\"'`]*")
_PASSED = re.compile(r"(\d+)/(\d+) passed")
_NOTE = "## 自动补记"


def _skills(cfg):
    raw = (cfg or {}).get("skills")
    return raw if isinstance(raw, dict) else {}


def _raw(cfg):
    raw = _skills(cfg).get("evolve")
    return raw if isinstance(raw, dict) else {}


def enabled(cfg):
    """缺省关。技能层和自动改进都打开才跑。"""
    return bool(_skills(cfg).get("enabled")) and bool(_raw(cfg).get("enabled"))


def settings(cfg):
    ids = []
    for item in _raw(cfg).get("cases") or []:
        text = str(item or "").strip()
        if _CASE.match(text) and text not in ids:
            ids.append(text)
    return {"enabled": enabled(cfg), "cases": ids}


def looks_like(text):
    q = (text or "").strip()
    return bool(_IMPROVE.match(q) or _ROLLBACK.match(q))


def root_of(cfg, base):
    raw = _skills(cfg).get("dir") or "skills"
    if os.path.isabs(str(raw)):
        return os.path.abspath(str(raw))
    return os.path.abspath(os.path.join(base or "", str(raw)))


def _meta_path(base):
    return os.path.join(base, "state", "skill_meta.json")


def _signal_path(base):
    return os.path.join(base, "state", "skill_signals.json")


def _pending_path(base):
    return os.path.join(base, "state", "skill_pending.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _version_in(text):
    data, _body, err = skills.parse_skill(text or "")
    if err or not isinstance(data, dict):
        return 0
    try:
        number = int(str(data.get("version") or "").strip() or "0")
    except (TypeError, ValueError):
        return 0
    if number < 1 or number > 100000:
        return 0
    return number


def skill_file(cfg, base, name):
    if not skills._name_ok(name):
        return ""
    folder = os.path.join(root_of(cfg, base), name)
    path = os.path.join(folder, "SKILL.md")
    if not skills._contained(root_of(cfg, base), path):
        return ""
    if not os.path.isfile(path):
        return ""
    return path


def describe(cfg, base, name):
    """现有技能没有 version 时按第 1 版。基线哈希记在本机，不改 SKILL.md。"""
    path = skill_file(cfg, base, name)
    if not path:
        return None
    try:
        raw = open(path, encoding="utf-8-sig").read()
    except OSError:
        return None
    meta = _read_json(_meta_path(base))
    item = (meta.get("skills") or {}).get(name) if isinstance(meta.get("skills"), dict) else None
    version = _version_in(raw)
    if version < 1:
        try:
            version = int((item or {}).get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        if version < 1:
            version = 1
    return {"name": name, "path": path, "text": raw, "version": version, "sha256": _sha(raw), "saved": item or {}}


def _remember(base, name, version, text):
    meta = _read_json(_meta_path(base))
    book = meta.get("skills") if isinstance(meta.get("skills"), dict) else {}
    item = book.get(name) if isinstance(book.get(name), dict) else {}
    history = [row for row in (item.get("history") or []) if isinstance(row, dict)]
    history.append({"version": int(version), "sha256": _sha(text), "text": text})
    history = history[-3:]
    book[name] = {"version": int(version), "sha256": _sha(text), "history": history}
    meta["skills"] = book
    _write_json(_meta_path(base), meta)


def _redact_note(text, names):
    cleaned = guard.neutralize(guard.redact(text or "", names, placeholder="某客户"))
    cleaned = _FEISHU_ID.sub("[编号已隐藏]", cleaned)
    cleaned = _UNIX.sub("[本地路径已隐藏]", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 2 or guard.embeds_secret(cleaned, names):
        return ""
    return "复盘里写过：" + cleaned[:60]


def observe(cfg, base, row, names=(), retros=()):
    """先记下信号，不改技能文件，也不跑回归。关着时什么都不写。"""
    if not enabled(cfg) or not isinstance(row, dict):
        return []
    used = []
    for name in row.get("skills") or []:
        if skills._name_ok(name) and name not in used:
            used.append(name)
    if not used:
        return []
    bad = []
    for step in row.get("steps") or []:
        if not isinstance(step, dict) or step.get("kind") != "tool" or step.get("ok", True):
            continue
        tool = str(step.get("name") or "")
        if _TOOL.match(tool) and tool not in bad:
            bad.append(tool)
    notes = ["工具 %s 失败过。先核对参数，不要重复同一条错路。" % tool for tool in bad[:4]]
    for text in list(retros or [])[:2]:
        note = _redact_note(text, names)
        if note and note not in notes:
            notes.append(note)
    if not notes and not row.get("ok", True):
        notes.append("这一轮没答成。先核对步骤再重试。")
    if not notes:
        return []
    path = _signal_path(base)
    data = _read_json(path)
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    added = []
    for name in used[:4]:
        got = items.get(name) if isinstance(items.get(name), dict) else {}
        lines = [str(x) for x in (got.get("notes") or []) if str(x).strip()]
        for note in notes:
            if note not in lines:
                lines.append(note)
        items[name] = {"notes": lines[:8], "at": time.time()}
        added.append(name)
    _write_json(path, {"items": items})
    return added


def _signals(base, name):
    data = _read_json(_signal_path(base))
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    got = items.get(name) if isinstance(items.get(name), dict) else {}
    return [str(x) for x in (got.get("notes") or []) if str(x).strip()]


def _clear_signal(base, name):
    path = _signal_path(base)
    data = _read_json(path)
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    if name in items:
        items.pop(name, None)
        _write_json(path, {"items": items})


def _pending(base):
    data = _read_json(_pending_path(base))
    return data if isinstance(data, dict) else {}


def _hold(base, name, now):
    _write_json(_pending_path(base), {"skill": name, "at": float(now)})


def _release(base, name):
    got = _pending(base)
    if got.get("skill") == name:
        _write_json(_pending_path(base), {})


def _busy(base, now):
    got = _pending(base)
    if not got.get("skill"):
        return ""
    try:
        at = float(got.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    if now - at > 3600:
        return ""
    return str(got.get("skill") or "")


def _compose(text, notes, version):
    data, _body, err = skills.parse_skill(text)
    if err or not data:
        return ""
    fresh = []
    for note in notes:
        line = "- " + note
        if line not in text:
            fresh.append(line)
    if not fresh:
        return ""
    stamped = _stamp(text, version)
    if _NOTE not in stamped:
        stamped = stamped.rstrip() + "\n\n" + _NOTE + "\n"
    else:
        stamped = stamped.rstrip() + "\n"
    stamped += "\n".join(fresh) + "\n"
    data, body, err = skills.parse_skill(stamped)
    if err or not body or not str(data.get("name") or "").strip():
        return ""
    if skills._classify(str(data.get("name") or ""), data, body):
        return ""
    return stamped


def _stamp(text, version):
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    found = False
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
        if re.match(r"^version:\s*\d+\s*$", lines[index]):
            lines[index] = "version: %d" % int(version)
            found = True
    if end is None:
        return text
    if not found:
        lines.insert(end, "version: %d" % int(version))
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _better(before, after):
    if not before or not after:
        return False
    try:
        left, left_n = int(before[0]), int(before[1])
        right, right_n = int(after[0]), int(after[1])
    except (TypeError, ValueError, IndexError):
        return False
    if left_n <= 0 or right_n <= 0:
        return False
    return (right / float(right_n)) > (left / float(left_n))


def score_skills(skills_dir, ids=(), tag="ci", timeout=600):
    """跑 evals/run.py。返回 (通过数, 总数)。没跑完返回 None。"""
    root = os.path.dirname(os.path.abspath(__file__))
    example = os.path.join(root, "agents", "example.json")
    try:
        src = json.load(open(example, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    work = tempfile.mkdtemp(prefix="skill_score_")
    try:
        src["base_dir"] = os.path.join(work, "base")
        src["model_provider"] = "fake"
        block = src.get("skills") if isinstance(src.get("skills"), dict) else {}
        block["dir"] = skills_dir
        block["enabled"] = True
        src["skills"] = block
        path = os.path.join(work, "bot.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(src, handle, ensure_ascii=False)
        out = os.path.join(work, "out")
        argv = [sys.executable, os.path.join(root, "evals", "run.py"),
                "--config", path, "--provider", "fake", "--out", out]
        if ids:
            for item in ids:
                argv.extend(["--id", item])
        else:
            argv.extend(["--tag", tag or "ci"])
        env = os.environ.copy()
        # 父进程可能是 PYTHONUTF8=0。子进程自己把管道改成 UTF-8，这里再声明一次，避免分数行乱码。
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env)
        found = _PASSED.findall((proc.stdout or "") + "\n" + (proc.stderr or ""))
        if not found:
            return None
        return int(found[-1][0]), int(found[-1][1])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _runner_for(cfg, base, name):
    spec = settings(cfg)
    folder = root_of(cfg, base)

    def run(text):
        dest = tempfile.mkdtemp(prefix="skill_eval_")
        try:
            target = os.path.join(dest, "skills")
            if os.path.isdir(folder):
                shutil.copytree(folder, target)
            else:
                os.makedirs(target, exist_ok=True)
            slot = os.path.join(target, name)
            os.makedirs(slot, exist_ok=True)
            with open(os.path.join(slot, "SKILL.md"), "w", encoding="utf-8") as handle:
                handle.write(text)
            return score_skills(target, spec["cases"], "ci", 120 if spec["cases"] else 600)
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    return run


def _candidate_path(base, name):
    if not skills._name_ok(name):
        return ""
    folder = os.path.abspath(os.path.join(base, "state", "skill_candidates"))
    path = os.path.abspath(os.path.join(folder, name + ".md"))
    try:
        if os.path.commonpath([folder, path]) != folder:
            return ""
    except ValueError:
        return ""
    return path


def _save_candidate(base, name, text):
    path = _candidate_path(base, name)
    if not path:
        return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def _load_candidate(base, name, digest):
    path = _candidate_path(base, name)
    if not path or not os.path.isfile(path):
        return ""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    if _sha(text) != digest:
        return ""
    return text


def _offer_replace(cfg, base, ctx, names, now, info, new_text):
    import feature_switch
    path = _save_candidate(base, info["name"], new_text)
    if not path:
        return {"text": "改进稿没能写下，没有替换。", "card": None, "restart": False}
    _hold(base, info["name"], now)
    digest = _sha(new_text)
    version = info["version"] + 1

    def render(code, plan):
        return "\n".join([
            "技能「%s」可以换成第 %d 版。" % (info["name"], version),
            "对照过回归测试，分数更高。还没替换文件。",
            "请只回复这一句：" if plan["mode"] == "code" else "请确认。",
            "确认 %s" % code,
            "不想换了就回复：取消 %s" % code,
        ])

    return feature_switch._offer(
        cfg, base, ctx, "skill_evolve", "skill_evolve", True, "替换技能", names, now, render,
        payload_extra={"skill": info["name"], "sha256": digest, "version": version, "op": "replace"})


def review(cfg, base, ctx, names=(), runner=None, now=None):
    """挑一个有信号的技能。分数没有更高，或没跑完，都不换。"""
    now = time.time() if now is None else float(now)
    if not enabled(cfg):
        return {"text": "技能自动改进现在关着。私聊发「设置」可以打开。", "card": None, "restart": False}
    waiting = _busy(base, now)
    if waiting:
        return {"text": "技能「%s」还在等确认，没有另提一版。" % waiting, "card": None, "restart": False}
    data = _read_json(_signal_path(base))
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    for name in sorted(items):
        notes = _signals(base, name)
        info = describe(cfg, base, name)
        if not info or not notes:
            continue
        new_text = _compose(info["text"], notes, info["version"] + 1)
        if not new_text:
            continue
        score = runner or _runner_for(cfg, base, name)
        before = score(info["text"])
        after = score(new_text)
        if not _better(before, after):
            return {"text": "技能「%s」对照过回归测试，分数没有更高或没跑完，没有替换。" % name,
                    "card": None, "restart": False}
        return _offer_replace(cfg, base, ctx, names, now, info, new_text)
    return {"text": "现在没有可改进的技能。", "card": None, "restart": False}


def _offer_rollback(cfg, base, ctx, names, now, info):
    import feature_switch
    history = [row for row in (info["saved"].get("history") or []) if isinstance(row, dict) and row.get("text")]
    previous = None
    for row in reversed(history):
        if int(row.get("version") or 0) < info["version"] and row.get("text"):
            previous = row
            break
    if previous is None and history and _sha(history[-1].get("text") or "") != info["sha256"]:
        previous = history[-1]
    if previous is None:
        return {"text": "技能「%s」没有上一版，没有改。" % info["name"], "card": None, "restart": False}
    text = str(previous.get("text") or "")
    data, body, err = skills.parse_skill(text)
    if err or not body or skills._classify(info["name"], data, body):
        return {"text": "上一版格式不对，没有改。", "card": None, "restart": False}
    path = _save_candidate(base, info["name"], text)
    if not path:
        return {"text": "上一版没能写下，没有改。", "card": None, "restart": False}
    _hold(base, info["name"], now)
    version = int(previous.get("version") or max(1, info["version"] - 1))

    def render(code, plan):
        return "\n".join([
            "技能「%s」可以回到第 %d 版。" % (info["name"], version),
            "还没改文件。",
            "请只回复这一句：" if plan["mode"] == "code" else "请确认。",
            "确认 %s" % code,
            "不想回滚了就回复：取消 %s" % code,
        ])

    return feature_switch._offer(
        cfg, base, ctx, "skill_rollback", "skill_rollback", False, "回滚技能", names, now, render,
        payload_extra={"skill": info["name"], "sha256": _sha(text), "version": version, "op": "rollback"})


def handle_text(cfg, base, text, ctx, names=(), now=None, owner=False, runner=None):
    """不是改进或回滚命令时返回 None。"""
    q = (text or "").strip()
    if not looks_like(q):
        return None
    import feature_switch
    ctx = ctx or {}
    now = time.time() if now is None else float(now)
    if ctx.get("chat_type") != "p2p":
        return {"text": feature_switch.GROUP_REDIRECT, "card": None, "restart": False}
    if not owner:
        return {"text": feature_switch.NOT_OWNER, "card": None, "restart": False}
    if not feature_switch._ctx_ok(ctx):
        return {"text": "不能改：缺少发送人或会话。请再到私聊里发一次。", "card": None, "restart": False}
    matched = _ROLLBACK.match(q)
    if matched:
        if not enabled(cfg):
            return {"text": "技能自动改进现在关着，所以不能回滚。", "card": None, "restart": False}
        info = describe(cfg, base, matched.group(1))
        if not info:
            return {"text": "没有这个技能，没有改。", "card": None, "restart": False}
        return _offer_rollback(cfg, base, ctx, names, now, info)
    return review(cfg, base, ctx, names, runner, now)


def _write_skill(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def accept(cfg, base, rec, cancel, names, apply):
    """确认之后才替换或回滚。取消、演练、哈希对不上都不改文件。"""
    name = str((rec or {}).get("skill") or "")
    if cancel:
        _release(base, name)
        return {"text": "已取消。技能文件没有改。", "card": None, "restart": False}
    if not apply:
        _release(base, name)
        return {"text": "确认码对了。当前这次只是演练，技能文件没有改。", "card": None, "restart": False}
    info = describe(cfg, base, name)
    digest = str((rec or {}).get("sha256") or "")
    text = _load_candidate(base, name, digest)
    if not info or not text:
        _release(base, name)
        return {"text": "改进稿对不上，没有改。", "card": None, "restart": False}
    data, body, err = skills.parse_skill(text)
    if err or not body or str(data.get("name") or "") != name or skills._classify(name, data, body):
        _release(base, name)
        return {"text": "这一版格式不对，没有改。", "card": None, "restart": False}
    if guard.embeds_secret(text, names):
        _release(base, name)
        return {"text": "这一版里有不能留下的信息，没有改。", "card": None, "restart": False}
    try:
        version = int((rec or {}).get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < 1:
        _release(base, name)
        return {"text": "版本号不对，没有改。", "card": None, "restart": False}
    if info["text"] not in [row.get("text") for row in (info["saved"].get("history") or []) if isinstance(row, dict)]:
        _remember(base, name, info["version"], info["text"])
    try:
        _write_skill(info["path"], text)
    except OSError:
        _release(base, name)
        return {"text": "没有写进技能文件。", "card": None, "restart": False}
    _remember(base, name, version, text)
    _clear_signal(base, name)
    _release(base, name)
    try:
        os.remove(_candidate_path(base, name))
    except OSError:
        pass
    verb = "回到" if (rec or {}).get("op") == "rollback" else "换成"
    return {"text": "技能「%s」已%s第 %d 版。下一轮读取会用这份正文。要回到更早的一版，私聊发「回滚技能 %s」。" % (name, verb, version, name),
            "card": None, "restart": False, "reload_skills": True}
