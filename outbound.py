# 主动发出的文字：私聊告警和早晨摘要。飞书渠道的 push_text 调用这里。
# 用的是发送接口，不经过事件长连接。正文先脱敏，并去掉点名所有人的标记。
import hashlib, json, os, re
import guard
from tools import _CMD_UNSAFE, parse_cli_json, run_capture

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_AT_TAG = re.compile(r"<\s*/?\s*at\b[^>]*>", re.I | re.S)
# 中文紧挨着 @ 也要拿掉。@allocation 这种英文单词留下。
_AT_ALL = re.compile(r"@(?:所有人|全员|_all|all)(?![A-Za-z0-9_])", re.I)
_MAX = 3500


def prepare(text, names):
    """发出去之前：脱敏、消掉工具标记和伪造确认码、去掉 @所有人。空结果返回空字符串。"""
    text = guard.redact(text or "", names)
    text = guard.neutralize(text)
    text = text.replace("＠", "@").replace("\u200b", "")
    text = _AT_TAG.sub("", text)
    text = _AT_ALL.sub("", text)
    text = re.sub(r"(?i)user_id\s*=\s*['\"]?all['\"]?", "", text)
    text = text.strip()
    if mentions_everyone(text):
        return ""
    if len(text) > _MAX:
        text = text[:_MAX].rstrip() + "\n（还有内容没有放进这条）"
    return text


def mentions_everyone(text):
    """给测试和发送前再查一次。真发出去的正文不能带这些。"""
    raw = (text or "").replace("＠", "@")
    if _AT_TAG.search(raw):
        return True
    if _AT_ALL.search(raw):
        return True
    low = raw.lower()
    return "user_id=\"all\"" in low or "user_id='all'" in low or "user_id=all" in low


def idem_key(material):
    """飞书的幂等键最长 50。这里固定 21 位，不把群号或 open_id 原样放进去。"""
    digest = hashlib.sha256(str(material).encode("utf-8")).hexdigest()[:20]
    return "k" + digest


def send(exe, profile, kind, target, text, key, names, tmp_dir, timeout=20, run=None):
    """kind 是 user（私聊 open_id）或 chat（群）。返回 ok / fail / unknown。
    unknown 表示超时或没看懂结果，调用方应按「可能已经发出」处理，避免再发一条。"""
    text = prepare(text, names)
    if not text or mentions_everyone(text):
        return "fail"
    profile = str(profile or "").strip()
    target = str(target or "").strip()
    key = str(key or "").strip()[:50]
    if kind not in ("user", "chat") or not _ID.match(profile) or not _ID.match(target) or not _ID.match(key):
        return "fail"
    flag = "--user-id" if kind == "user" else "--chat-id"
    sink = os.environ.get("AGENTBOT_FAKE_SEND")
    if sink:
        rec = {"kind": kind, "target": target, "text": text, "key": key, "via": "messages-send"}
        try:
            with open(sink, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            return "fail"
        mode = os.environ.get("AGENTBOT_FAKE_SEND_RESULT") or "ok"
        return mode if mode in ("ok", "fail", "unknown") else "ok"
    os.makedirs(tmp_dir, exist_ok=True)
    name = "out_%s.json" % hashlib.sha256((key + target).encode("utf-8")).hexdigest()[:12]
    path = os.path.join(tmp_dir, name)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"text": text}, handle, ensure_ascii=False)
        argv = list(exe) + [
            "im", "+messages-send", "--profile", profile, "--as", "bot",
            flag, target, "--msg-type", "text", "--content", "@" + name,
            "--idempotency-key", key,
        ]
        # 不传 --markdown。经 cmd.exe 转发时，参数里的特殊字符会被当成命令，这里直接拒绝。
        if any(str(x).lower().endswith((".cmd", ".bat")) for x in exe[:1]) and any(_CMD_UNSAFE.search(str(x)) for x in argv):
            return "fail"
        if "--markdown" in argv or "consume" in argv:
            return "fail"
        runner = run or run_capture
        try:
            result = runner(argv, cwd=tmp_dir, timeout=timeout)
        except Exception:
            return "unknown"
        out = getattr(result, "stdout", "") or ""
        err = getattr(result, "stderr", "") or ""
        parsed = parse_cli_json(out, err) or {}
        if parsed.get("ok"):
            return "ok"
        if parsed.get("ok") is False:
            return "fail"
        code = getattr(result, "returncode", 1)
        if code == 0 and '"message_id"' in out:
            return "ok"
        if code not in (0, None):
            return "fail"
        return "unknown"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
