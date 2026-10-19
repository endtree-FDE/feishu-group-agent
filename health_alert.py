# 长连接中断或进程没有心跳时通知主人。默认关。
# 发送用 outbound（HTTP），不依赖事件长连接。看门狗在进程已经不更新心跳时也会走同一条。
# 没超过阈值不发；发过之后按间隔提醒；恢复只发一次。状态写在本机，重启也不会连着刷。
import base64, json, os, time
import guard, outbound

_DEFAULT_AFTER = 180
_DEFAULT_REPEAT = 1800


def _num(value, default, minimum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < minimum:
        return default
    return number


def settings(cfg):
    """没写 alerts 或 enabled 不是 true 时，和以前一样：不发、不写告警日志。"""
    raw = (cfg or {}).get("alerts")
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return {"enabled": False, "owners": [], "after": _DEFAULT_AFTER, "repeat": _DEFAULT_REPEAT,
                "local_log": True, "windows_toast": False}
    owners = []
    for item in raw.get("owner_open_ids") or []:
        text = str(item or "").strip()
        if text and not text.startswith("REPLACE_") and text not in owners and outbound._ID.match(text):
            owners.append(text)
    local = raw.get("local_log")
    return {
        "enabled": True,
        "owners": owners,
        "after": _num(raw.get("unhealthy_after_sec", _DEFAULT_AFTER), _DEFAULT_AFTER, 1),
        "repeat": _num(raw.get("repeat_sec", _DEFAULT_REPEAT), _DEFAULT_REPEAT, 1),
        "local_log": True if local is None else bool(local),
        "windows_toast": bool(raw.get("windows_toast")),
    }


def step(state, now, ok, threshold, repeat, unhealthy_since=None, cause="link"):
    """纯函数。ok 为真表示当前健康。返回 (新状态, 动作)。动作是 None、down、remind、recover。"""
    state = dict(state or {})
    phase = state.get("phase") or "ok"
    if phase not in ("ok", "pending", "alerted"):
        phase = "ok"
    try:
        last = float(state.get("last_alert") or 0)
    except (TypeError, ValueError):
        last = 0
    try:
        since = float(state.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    now = float(now)
    kept = state.get("cause") or ""
    if ok:
        if phase == "alerted":
            # since 仍是中断开始，方便算出中断了多久。下一轮健康时再把它清掉。
            return {"phase": "ok", "since": since or now, "last_alert": now, "cause": kept or cause}, "recover"
        return {"phase": "ok", "since": now, "last_alert": last, "cause": ""}, None
    if phase == "ok":
        try:
            origin = float(unhealthy_since) if unhealthy_since is not None else now
        except (TypeError, ValueError):
            origin = now
        if origin <= 0 or origin > now:
            origin = now
        since = origin
        phase = "pending"
        kept = cause or "link"
    elapsed = now - since
    if elapsed >= float(threshold) and (phase != "alerted" or now - last >= float(repeat)):
        action = "remind" if phase == "alerted" else "down"
        return {"phase": "alerted", "since": since, "last_alert": now, "cause": kept or cause}, action
    return {"phase": phase, "since": since, "last_alert": last, "cause": kept}, None


def render(action, bot_name, since, now, cause):
    name = (bot_name or "机器人").strip() or "机器人"
    try:
        mins = max(1, int((float(now) - float(since)) / 60))
    except (TypeError, ValueError):
        mins = 1
    if action == "recover":
        title = "【%s】已经恢复" % name
        body = "中断了大约 %d 分钟，现在可以收消息了。" % mins
    elif action == "remind":
        title = "【%s】仍未恢复" % name
        body = "已经大约 %d 分钟没有恢复。本机 logs/alerts.log 里有记录。" % mins
    elif cause == "heartbeat":
        title = "【%s】进程没有心跳" % name
        body = "已经大约 %d 分钟没有更新心跳。看门狗会尝试重新启动。这期间群里的 @ 可能暂时没人接。恢复后我会再发一条。" % mins
    else:
        title = "【%s】长连接已中断" % name
        body = "飞书事件长连接已经中断大约 %d 分钟。这条是用另外的发送接口发出的，不依赖那条长连接。恢复后我会再发一条。" % mins
    return title + "\n" + body


def _state_path(base):
    return os.path.join(base, "state", "health_alert.json")


def _log_path(base):
    return os.path.join(base, "logs", "alerts.log")


class _Lock:
    def __init__(self, path, timeout=5):
        self.path = path + ".lock"
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, str(time.time()).encode("ascii"))
                return self
            except FileExistsError:
                try:
                    stale = time.time() - os.path.getmtime(self.path) > 30
                except OSError:
                    stale = True
                if stale:
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() > deadline:
                    raise TimeoutError("health alert lock")
                time.sleep(0.05)

    def __exit__(self, *_args):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _append_log(base, line, names):
    path = _log_path(base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = guard.redact(line, names).replace("\n", " ")
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > 1_000_000:
        try:
            with open(path, "rb") as handle:
                handle.seek(max(0, size - 200_000))
                blob = handle.read()
            with open(path, "wb") as handle:
                handle.write(blob)
        except OSError:
            pass
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("%s %s\n" % (stamp, text))


def toast_command(title, body):
    """Windows 提示用的命令。正文放在 base64 里，避免引号或 & 被 PowerShell 当成代码。"""
    blob = base64.b64encode(json.dumps({"t": title, "b": body}, ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = (
        "$raw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s'));"
        "$o = $raw | ConvertFrom-Json;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Warning;"
        "$n.Visible = $true;"
        "$n.ShowBalloonTip(8000, [string]$o.t, [string]$o.b, [System.Windows.Forms.ToolTipIcon]::Warning);"
        "Start-Sleep -Seconds 2;"
        "$n.Dispose()"
    ) % blob
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]


def maybe_toast(enabled, title, body, run=None):
    if not enabled or os.name != "nt":
        return False
    argv = toast_command(title, body)
    runner = run
    if runner is None:
        import subprocess
        try:
            subprocess.run(argv, capture_output=True, timeout=12, check=False)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        runner(argv)
        return True
    except Exception:
        return False


def _bot_name(cfg):
    names = (cfg or {}).get("bot_names") or []
    if names and isinstance(names[0], str) and names[0].strip():
        return names[0].strip()[:40]
    return "机器人"


def _emit(cfg, base, names, state, action, sender, log):
    spec = settings(cfg)
    text = outbound.prepare(render(action, _bot_name(cfg), state.get("since"), state.get("last_alert"), state.get("cause")), names)
    if not text:
        return
    sent = 0
    owners = spec["owners"]
    if sender and owners:
        for owner in owners:
            key = outbound.idem_key("%s|%s|%s|%s" % (action, int(float(state.get("since") or 0)), int(float(state.get("last_alert") or 0)), owner))
            try:
                result = sender("user", owner, text, key)
            except Exception:
                result = "fail"
            if result == "ok":
                sent += 1
    if spec["local_log"]:
        try:
            _append_log(base, "%s feishu=%d/%d %s" % (action, sent, len(owners), text), names)
        except OSError as exc:
            if log:
                log.warning("alert log failed: %s", exc)
    if spec["windows_toast"] and sent < len(owners):
        title, _, body = text.partition("\n")
        maybe_toast(True, title or text, body or text)
    if log:
        log.info("health alert %s feishu=%d/%d", action, sent, len(owners))


def tick(cfg, base, names, link_up, link_since, now=None, sender=None, log=None):
    """进程还活着时由心跳调用。link_up 为假表示长连接没连上。"""
    spec = settings(cfg)
    if not spec["enabled"]:
        return None
    now = time.time() if now is None else float(now)
    path = _state_path(base)
    try:
        with _Lock(path):
            state = _load(path)
            new, action = step(state, now, bool(link_up), spec["after"], spec["repeat"], link_since, "link")
            if new != state:
                _save(path, new)
    except TimeoutError:
        if log:
            log.warning("health alert skipped: lock")
        return None
    if action:
        _emit(cfg, base, names, new, action, sender, log)
    return action


def _heartbeat_ts(base):
    try:
        raw = open(os.path.join(base, "state", "heartbeat"), encoding="utf-8").read().strip()
        return int(raw)
    except (OSError, ValueError):
        return 0


def external_check(cfg, base, names, now=None, sender=None, log=None):
    """看门狗在准备重启时调用。心跳还新鲜就什么都不做，交给进程自己的心跳线程。
    没有心跳文件（还没启动过）也不发，避免第一次安装就告警。"""
    spec = settings(cfg)
    if not spec["enabled"]:
        return 0
    now = time.time() if now is None else float(now)
    hb = _heartbeat_ts(base)
    if hb <= 0 or now - hb <= spec["after"]:
        return 0
    path = _state_path(base)
    try:
        with _Lock(path):
            state = _load(path)
            new, action = step(state, now, False, spec["after"], spec["repeat"], hb, "heartbeat")
            if new != state:
                _save(path, new)
    except TimeoutError:
        if log:
            log.warning("alert-check skipped: lock")
        return 0
    if action:
        _emit(cfg, base, names, new, action, sender, log)
    return 0
