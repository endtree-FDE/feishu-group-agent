# 只读诊断。不启动 event consume，不发消息，不写心跳。
# 外部命令、进程列表、域名解析都由调用方传入，测试不必连真的飞书。
import base64, csv, io, json, os, re, socket, subprocess

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


# lark-cli 1.0.98 的 auth scopes 不接受 --as bot（带上退出码 2）。
# 官方实现（cmd/auth/scopes.go + getAppInfo）：开放平台若按身份标了 token_types，
# JSON 固定是 tokenType=user 和 userScopes，里面只有用户 token 的 scope。
# 实机登录后常见 userScopes 只有 offline_access，机器人实际用的 tenant 权限不在这份列表里。
# 接口没按身份分类时字段才是 scopes，那一份才是应用权限。这一版没有别的命令能列出 tenant 权限。
_PLAIN_SCOPE_KEYS = ("scopes", "enabledScopes", "enabled_scopes", "scopeList", "scope_list")
_EXPLICIT_APP_KEYS = ("appScopes", "app_scopes", "tenantScopes", "tenant_scopes", "botScopes", "bot_scopes")
_USER_SCOPE_KEYS = ("userScopes", "user_scopes")
_USER_TOKEN_TYPES = {"user", "uat", "user_access_token"}
_APP_TOKEN_TYPES = {"tenant", "bot", "app", "tat", "tenant_access_token", "application"}
_SCOPE_NESTS = ("data", "result", "app", "appInfo", "application")


def _scope_items(value):
    """列表就返回名字（可以是空列表）。不是列表则返回 None。"""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if ":" not in raw and "," not in raw:
            return None
        value = [part for part in re.split(r"[\s,]+", raw) if part]
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("scope") or item.get("name") or item.get("id") or item.get("key") or ""
            name = str(name).strip()
        else:
            name = str(item).strip().strip(",|")
        if name:
            out.append(name)
    return out


def _find_named(obj, keys, depth=0):
    if not isinstance(obj, dict) or depth > 3:
        return None
    for key in keys:
        found = _scope_items(obj.get(key))
        if found is not None:
            return found
    for key in _SCOPE_NESTS:
        found = _find_named(obj.get(key), keys, depth + 1)
        if found is not None:
            return found
    return None


def _token_type(obj, depth=0):
    if not isinstance(obj, dict) or depth > 3:
        return ""
    for key in ("tokenType", "token_type"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    for key in _SCOPE_NESTS:
        found = _token_type(obj.get(key), depth + 1)
        if found:
            return found
    return ""


def classify_scopes(text):
    """返回 ('app', 列表)、('user', 列表) 或 (None, None)。

    app 才能拿去和已启用能力比对。user 是用户 token，不能当成应用权限。
    """
    obj = _json_obj(text)
    if isinstance(obj, dict):
        token = _token_type(obj)
        explicit = _find_named(obj, _EXPLICIT_APP_KEYS)
        plain = _find_named(obj, _PLAIN_SCOPE_KEYS)
        user = _find_named(obj, _USER_SCOPE_KEYS)
        if token in _APP_TOKEN_TYPES:
            for found in (explicit, plain, user):
                if found is not None:
                    return "app", found
        if explicit is not None and token not in _USER_TOKEN_TYPES:
            return "app", explicit
        if token in _USER_TOKEN_TYPES or (explicit is None and plain is None and user is not None):
            return "user", user if user is not None else []
        if plain is not None:
            return "app", plain
        if explicit is not None:
            return "app", explicit
        # 已经是 JSON 就不要再按行去抠权限名，否则用户 token 里的 scope 会被当成应用权限。
        return None, None
    found = scopes_from_text(text)
    if found is not None:
        return "app", found
    return None, None


def scopes_from_text(text):
    """pretty 的圆点列表，或 SCOPE / 权限 这种文本表格。一行一个权限名。"""
    found = []
    for line in (text or "").splitlines():
        body = line.strip()
        if not body or set(body) <= set("-|=:+ "):
            continue
        while body and body[0] in "•·*-|>":
            body = body[1:].strip()
        low = body.lower()
        if low.startswith(("scope", "enabled scopes", "status", "querying", "app id", "权限", "名称")):
            continue
        token = body.split()[0].strip(",|") if body.split() else ""
        if _SCOPE_RX.fullmatch(token):
            found.append(token)
    if found:
        return list(dict.fromkeys(found))
    if re.search(r"Enabled scopes\s*\(\s*0\s*\)", text or "", re.I):
        return []
    return None


def app_scopes(text):
    """应用或 tenant 权限。用户 token（tokenType=user / 只有 userScopes）返回 None。"""
    kind, found = classify_scopes(text)
    if kind == "app":
        return found
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
        # 不带 --as。1.0.98 拒绝 --as bot，而且 auth scopes 在按身份分类时只给用户 token。
        scope_code, scope_out, scope_err = run(list(exe) + ["auth", "scopes", "--profile", profile])
        scope_text = (scope_out or "") + "\n" + (scope_err or "")
        kind, found = classify_scopes(scope_text)
        if usage_error(scope_code, scope_text) or (scope_code != 0 and kind is None):
            add("WARN", "scopes", "列不出应用权限，请在开放平台核对")
        elif kind == "user":
            add("WARN", "scopes", "拿到的是用户 token，不是应用权限，请在开放平台核对")
        elif kind != "app" or found is None:
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


def windows_process_script():
    """固定脚本。单引号里的 `t 在 PowerShell 5.1 不是制表符，所以用 [char]9。

    先打出 #count=。一个都没枚举到是没看见进程表，不是「没有机器人」。
    """
    return r"""$OutputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $OutputEncoding
$rows = @(Get-CimInstance Win32_Process)
Write-Output ('#count=' + $rows.Count)
foreach ($p in $rows) {
  $cmd = [string]$p.CommandLine
  if (-not $cmd) { continue }
  if ($cmd -notmatch 'agentbot\.py|lark-cli') { continue }
  $flat = (($cmd -replace '\s+', ' ').Trim())
  Write-Output ($p.ProcessId.ToString() + [char]9 + $flat)
}
"""


def windows_process_argv():
    """Windows PowerShell 5.1。不用 pwsh。-EncodedCommand 避免 -Command 再剥一层引号。"""
    encoded = base64.b64encode(windows_process_script().encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def decode_process_output(raw):
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw.count(b"\x00") > max(1, len(raw) // 4):
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def _keep_proc(pid, cmd):
    pid = (pid or "").strip()
    cmd = (cmd or "").strip()
    if not pid.isdigit() or not cmd:
        return None
    if "agentbot.py" not in cmd and "lark-cli" not in cmd:
        return None
    return {"pid": int(pid), "cmdline": cmd}


def _split_proc_line(line):
    """pid 与命令行。制表符、5.1 单引号留下的字面 `t、表格里的多空格都认。"""
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None, None
    if "\t" in raw:
        pid, cmd = raw.split("\t", 1)
        return pid.strip(), cmd.strip()
    mark = "`t"
    if mark in raw:
        pid, cmd = raw.split(mark, 1)
        return pid.strip(), cmd.strip()
    found = re.match(r"^(\d+)\s+(.*)$", raw)
    if not found:
        return None, None
    return found.group(1), found.group(2).strip()


def _join_wrap(prev, extra):
    extra = (extra or "").strip()
    if not prev:
        return extra
    if not extra:
        return prev
    if extra.startswith('"') or extra.startswith("'"):
        return prev + " " + extra
    return prev + extra


def _parse_format_list(lines):
    props = {}
    order = []
    procs = []
    visible = False

    def flush():
        nonlocal visible
        pid = (props.get("ProcessId") or "").strip()
        cmd = props.get("CommandLine") or ""
        if pid.isdigit():
            visible = True
        item = _keep_proc(pid, cmd)
        if item:
            procs.append(item)

    for line in lines:
        found = re.match(r"^([A-Za-z][A-Za-z0-9]*)\s+:\s?(.*)$", line.rstrip())
        if found:
            key, value = found.group(1), found.group(2).strip()
            props[key] = value
            order.append(key)
            continue
        if line.startswith((" ", "\t")) and order:
            key = order[-1]
            props[key] = _join_wrap(props.get(key) or "", line)
            continue
        if not line.strip():
            flush()
            props = {}
            order = []
    flush()
    return procs, visible


def _parse_csv_snapshot(text):
    sample = (text or "").lstrip("\ufeff")
    first = next((line for line in sample.splitlines() if line.strip()), "")
    # Format-Table 的表头是空格对齐，不是 CSV。必须有逗号才按 CSV 读。
    if "," not in first or "ProcessId" not in first or "CommandLine" not in first:
        return None
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        return None
    procs = []
    rows = 0
    for row in reader:
        if not row:
            continue
        norm = {(key or "").strip().lower(): (value or "") for key, value in row.items()}
        pid = (norm.get("processid") or "").strip()
        cmd = (norm.get("commandline") or "").strip()
        if not pid and not cmd:
            continue
        rows += 1
        item = _keep_proc(pid, cmd)
        if item:
            procs.append(item)
    return procs, rows > 0


def parse_process_snapshot(text):
    """返回 (匹配的进程, 是否真的看见了进程表)。

    没看见进程表时，调用方应报 WARN，不要报 OK serving=0。
    """
    raw = text or ""
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    csv_found = _parse_csv_snapshot(raw)
    if csv_found is not None:
        return csv_found
    lines = raw.splitlines()
    if any(re.match(r"^(ProcessId|CommandLine)\s+:", line) for line in lines):
        return _parse_format_list(lines)
    procs = []
    visible = False
    counted = None
    for line in lines:
        stripped = line.strip()
        count = re.match(r"^#count=(\d+)\s*$", stripped)
        if count:
            counted = int(count.group(1))
            if counted > 0:
                visible = True
            continue
        pid, cmd = _split_proc_line(line)
        if pid is None:
            continue
        if not str(pid).strip().isdigit():
            continue
        visible = True
        item = _keep_proc(pid, cmd)
        if item:
            procs.append(item)
    if counted == 0 and not procs:
        visible = False
    return procs, visible


def parse_cim_lines(text):
    procs, _visible = parse_process_snapshot(text)
    return procs


def interpret_process_output(returncode, raw):
    """看懂了进程表就返回列表（可以是空的）。没看懂、命令失败、输出是空的，返回 None。"""
    text = decode_process_output(raw or b"")
    if returncode not in (0, None) and not text.strip():
        return None
    procs, visible = parse_process_snapshot(text)
    if not visible:
        return None
    return procs


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
    return interpret_process_output(got.returncode, got.stdout or b"")


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
