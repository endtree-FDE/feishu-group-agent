# 只读诊断。不启动 event consume，不发消息，不写心跳。
# 外部命令、进程列表、域名解析都由调用方传入，测试不必连真的飞书。
import json, os, re, socket, subprocess

_SCOPE_RX = re.compile(r"\b[a-z][a-z0-9_.]*(?::[a-z0-9_.]+){1,8}\b")
_SECRET_KEY = re.compile(r"^(app_secret|api_key|api_secret|password|passwd)$", re.I)
_FAKE_SECOND = {18, 19}


def secret_keys(obj, prefix=""):
    """只返回键名。不返回值，避免把密钥打进诊断输出。"""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = str(key)
            path = prefix + name
            if _SECRET_KEY.match(name):
                found.append(path)
            else:
                found.extend(secret_keys(value, path + "."))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(secret_keys(item, prefix))
    return found


def _enabled(cfg, name):
    spec = (cfg.get("tools") or {}).get(name)
    if isinstance(spec, dict) and "enabled" in spec:
        return bool(spec.get("enabled"))
    if name in ("calendar_create_batch", "calendar_update", "calendar_delete") and name not in (cfg.get("tools") or {}):
        return bool(((cfg.get("tools") or {}).get("calendar_create") or {}).get("enabled"))
    return False


def required_scopes(cfg):
    """按已经启用的能力列出必须有的 scope。没开的工具不因此失败。"""
    cfg = cfg or {}
    need = ["im:message.group_at_msg:readonly", "im:message:send_as_bot"]
    feat = cfg.get("features") or {}
    if feat.get("p2p"):
        need.append("im:message.p2p_msg:readonly")
    if _enabled(cfg, "history"):
        need.extend(["im:message:readonly", "im:message.group_msg"])
    if feat.get("attachments", True):
        need.extend(["im:message:readonly", "im:resource"])
    progress = cfg.get("progress")
    if progress is None or (isinstance(progress, dict) and progress.get("enabled", True)):
        need.extend(["im:message.reactions:write_only", "im:message:update", "cardkit:card:write"])
    if _enabled(cfg, "docs_read"):
        need.append("docx:document:readonly")
    if _enabled(cfg, "docs_search"):
        need.append("search:docs:read")
    if _enabled(cfg, "docs_create"):
        need.append("docx:document:create")
    if _enabled(cfg, "docs_append"):
        need.append("docx:document:write_only")
    if _enabled(cfg, "calendar_read"):
        need.append("calendar:calendar.event:read")
    if _enabled(cfg, "calendar_create") or _enabled(cfg, "calendar_create_batch"):
        need.append("calendar:calendar.event:create")
    if _enabled(cfg, "calendar_update"):
        need.append("calendar:calendar.event:update")
    if _enabled(cfg, "calendar_delete"):
        need.append("calendar:calendar.event:delete")
    if _enabled(cfg, "base_read"):
        need.append("base:record:read")
    if _enabled(cfg, "base_create"):
        need.append("base:record:create")
    chats = cfg.get("chats") or {}
    if chats.get("auto_join") and not (cfg.get("chat_ids") or []):
        need.extend(["im:chat:read", "im:chat.members:read"])
    out = []
    for item in need:
        if item not in out:
            out.append(item)
    return out


def required_events(cfg):
    events = ["im.message.receive_v1"]
    chats = (cfg or {}).get("chats") or {}
    if chats.get("auto_join") and not ((cfg or {}).get("chat_ids") or []):
        events.extend(["im.chat.member.bot.added_v1", "im.chat.member.bot.deleted_v1"])
    return events


def scopes_in(text):
    return list(dict.fromkeys(_SCOPE_RX.findall(text or "")))


def is_fake_ip(ip):
    parts = str(ip or "").split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    return nums[0] == 198 and nums[1] in _FAKE_SECOND


def proxy_covers_feishu(no_proxy):
    text = (no_proxy or "").lower()
    return "feishu.cn" in text or "larksuite.com" in text


def logged_in(code, text):
    if code != 0:
        return False
    low = (text or "").lower()
    bad = ("not login", "not logged", "unauthorized", "please login", "no token", "未登录", "请登录")
    return not any(item in low for item in bad)


def usage_error(code, text):
    """退出码 2，或文本写明不认识这个参数。这是命令写法问题，不是飞书拒绝。"""
    if code == 2:
        return True
    if code == 0:
        return False
    low = (text or "").lower()
    marks = ("unknown flag", "unknown command", "unknown shorthand", "required flag",
             "invalid argument", "flag provided but not defined", "参数错误", "accepts ")
    return any(item in low for item in marks)


def _json_obj(text):
    raw = text or ""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def bot_ready(text):
    """True 应用身份就绪，False 明确没有，None 这一版输出里看不出来。"""
    obj = _json_obj(text)
    if not isinstance(obj, dict):
        return None
    identities = obj.get("identities")
    if isinstance(identities, dict) and isinstance(identities.get("bot"), dict):
        bot = identities["bot"]
        if "available" in bot:
            return bool(bot.get("available"))
        return str(bot.get("status") or "") == "ready"
    if obj.get("identity") == "bot":
        return True
    return None


def app_scopes(text):
    """auth scopes 里应用已开通的权限。用户令牌的 userScopes 不算。"""
    obj = _json_obj(text)
    if not isinstance(obj, dict):
        return None
    scopes = obj.get("scopes")
    if isinstance(scopes, list):
        return [str(item) for item in scopes]
    return None


def duplicate_findings(procs, base, profile):
    """只描述重复，不结束任何进程。--doctor / --check / --dry-run 不算正在服务的机器人。"""
    lines = []
    base_norm = (base or "").replace("/", "\\").rstrip("\\").lower()
    serving = []
    consumes = []
    for proc in procs or []:
        cmd = proc.get("cmdline") or ""
        low = cmd.lower()
        if "agentbot.py" in low and not any(flag in low for flag in ("--doctor", "--check", "--dry-run", "--lock-test")):
            if not base_norm or base_norm in low.replace("/", "\\"):
                serving.append(proc)
        if "lark-cli" in low and re.search(r"(?:^|\s)event\s+consume(?:\s|$)", low):
            if profile and profile in cmd:
                consumes.append(proc)
    if len(serving) > 1:
        lines.append(("FAIL", "duplicate agentbot count=%d" % len(serving)))
    by_event = {}
    for proc in consumes:
        found = re.search(r"event\s+consume\s+(\S+)", proc.get("cmdline") or "", re.I)
        key = found.group(1) if found else "?"
        by_event.setdefault(key, []).append(proc)
    for key, group in by_event.items():
        if len(group) > 1:
            lines.append(("FAIL", "duplicate event consume %s count=%d" % (key, len(group))))
    if not lines:
        lines.append(("OK", "serving=%d consume=%d" % (len(serving), len(consumes))))
    return lines


def _line(level, name, info=""):
    return ("%s %s %s" % (level.ljust(4), name, info)).rstrip()


def report(cfg, base, exe, run, processes, resolve, connect, modules, env):
    """返回 (输出行, 退出码)。有 FAIL 时退出码为 1，只有 WARN 时仍是 0。"""
    cfg = cfg or {}
    env = env if env is not None else os.environ
    lines = []
    fail = False

    def add(level, name, info=""):
        nonlocal fail
        if level == "FAIL":
            fail = True
        lines.append(_line(level, name, info))

    secrets = secret_keys(cfg)
    if secrets:
        add("FAIL", "config", "含有密钥字段 %s，请从配置里删掉" % ", ".join(secrets))
    else:
        add("OK", "config", "没有 app_secret")

    profile = str(cfg.get("profile") or "")
    if not profile:
        add("FAIL", "profile", "未填写")
    else:
        add("OK", "profile", "已填写")

    code, out, err = run(list(exe) + ["--version"])
    if code == 0:
        add("OK", "lark-cli", ((out or err or "").strip().splitlines() or ["ok"])[0][:80])
    else:
        add("FAIL", "lark-cli", "无法运行 --version")

    if profile:
        code, out, err = run(list(exe) + ["config", "show"])
        add("OK" if code == 0 else "FAIL", "lark config", "能读到" if code == 0 else "config show 失败")
        # lark-cli 1.0.98 的 auth status / auth check / event list 不接受 --as。
        # 带上会退出码 2。那是参数错误，不是登录失败。
        code, out, err = run(list(exe) + ["auth", "status", "--json", "--profile", profile])
        auth_text = (out or "") + "\n" + (err or "")
        ready = bot_ready(auth_text)
        if usage_error(code, auth_text):
            add("WARN", "auth", "参数不被这一版接受，没有因此判失败")
        elif ready is True or (ready is None and logged_in(code, auth_text)):
            add("OK", "auth", "bot" if ready is True else "status 成功")
        else:
            add("FAIL", "auth", "应用身份未就绪或 status 失败")
        scope_code, scope_out, scope_err = run(list(exe) + ["auth", "scopes", "--profile", profile])
        scope_text = (scope_out or "") + "\n" + (scope_err or "")
        found = app_scopes(scope_text)
        if usage_error(scope_code, scope_text) or (scope_code != 0 and found is None):
            add("WARN", "scopes", "列不出应用权限，请在开放平台核对")
        elif found is None:
            add("WARN", "scopes", "没有解析到应用权限列表，请在开放平台核对")
        else:
            missing = [item for item in required_scopes(cfg) if item not in found]
            if missing:
                add("FAIL", "scopes", "缺少 " + ", ".join(missing))
            else:
                add("OK", "scopes", "%d 项" % len(found))
        # event list 列的是命令行认识的全部事件，不是这个应用在开放平台的订阅。不能据此判失败。
        need_ev = required_events(cfg)
        add("OK", "events", "需要 %s。订阅请在开放平台核对，这里不根据 event list 判断" % ", ".join(need_ev))

    try:
        addresses = list(resolve("open.feishu.cn"))
    except Exception:
        addresses = []
    if not addresses:
        add("FAIL", "network", "无法解析 open.feishu.cn")
    else:
        fake = [ip for ip, _port in addresses if is_fake_ip(ip)]
        if fake and not proxy_covers_feishu(env.get("NO_PROXY") or env.get("no_proxy") or ""):
            add("WARN", "network", "open.feishu.cn 解析到 198.18/15，NO_PROXY 未包含飞书域名")
        else:
            add("OK", "network", "已解析")
        try:
            ok = bool(connect("open.feishu.cn", 443))
        except Exception:
            ok = False
        add("OK" if ok else "FAIL", "tcp443", "open.feishu.cn" if ok else "连不上 443")

    for mod, label in (("pypdf", "pypdf"), ("openpyxl", "openpyxl"), ("PIL", "pillow")):
        if modules.get(mod):
            add("OK", "package", label)
        else:
            add("WARN", "package", label + " 未安装，对应附件会降级")

    try:
        procs = processes()
    except Exception:
        procs = None
    if procs is None:
        add("WARN", "processes", "列不出进程")
    else:
        for level, info in duplicate_findings(procs, base, profile):
            add(level, "processes", info)
    return lines, (1 if fail else 0)


def default_run(argv):
    if "consume" in argv:
        return 1, "", "refused"
    try:
        got = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)[:200]
    return got.returncode, got.stdout or "", got.stderr or ""


def default_resolve(host):
    found = []
    for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        ip = item[4][0]
        if (ip, 443) not in found:
            found.append((ip, 443))
    return found


def default_connect(host, port):
    sock = socket.create_connection((host, port), timeout=5)
    sock.close()
    return True


def windows_process_argv():
    """Windows PowerShell 5.1。不用 pwsh。命令是固定的，不拼用户输入。"""
    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
        "$OutputEncoding = [Console]::OutputEncoding; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and ($_.CommandLine -match 'agentbot\\.py|lark-cli') } | "
        "ForEach-Object { '{0}`t{1}' -f $_.ProcessId, (($_.CommandLine -replace '[`r`n`t]', ' ')) }"
    )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def decode_process_output(raw):
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw.count(b"\x00") > max(1, len(raw) // 4):
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def parse_cim_lines(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if "\t" not in line:
            continue
        pid, cmd = line.split("\t", 1)
        pid = pid.strip()
        cmd = cmd.strip()
        if not pid.isdigit() or not cmd:
            continue
        if "agentbot.py" not in cmd and "lark-cli" not in cmd:
            continue
        out.append({"pid": int(pid), "cmdline": cmd})
    return out


def _linux_processes():
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            raw = open("/proc/%s/cmdline" % name, "rb").read()
        except OSError:
            continue
        cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if "agentbot.py" in cmd or "lark-cli" in cmd:
            out.append({"pid": int(name), "cmdline": cmd})
    return out


def _windows_processes():
    try:
        got = subprocess.run(windows_process_argv(), capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = decode_process_output(got.stdout or b"")
    if got.returncode != 0 and not text.strip():
        return None
    return parse_cim_lines(text)


def default_processes():
    if os.path.isdir("/proc"):
        return _linux_processes()
    return _windows_processes()


def default_modules():
    found = {}
    for name in ("pypdf", "openpyxl", "PIL"):
        try:
            __import__(name)
            found[name] = True
        except Exception:
            found[name] = False
    return found


def run_doctor(cfg, base, exe, run=None, processes=None, resolve=None, connect=None, modules=None, env=None):
    lines, code = report(
        cfg, base, exe,
        run or default_run,
        processes or default_processes,
        resolve or default_resolve,
        connect or default_connect,
        modules if modules is not None else default_modules(),
        env)
    for line in lines:
        print(line)
    return code
