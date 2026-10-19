# 结构化日志和只读诊断。不连飞书，不发消息，不结束进程。
import base64, json, os, shutil, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401  管道改为 UTF-8，不依赖 PYTHONUTF8
import doctor, observe

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


log_dir = tempfile.mkdtemp(prefix="agentbot_obs_")
try:
    obs = observe.Observe(log_dir, max_bytes=2_000_000, backups=2)
    obs.begin("om_1")
    obs.note_tool("om_1", "docs_read", False, "99991672", 0.2, "读不到 13812345678 app_secret=abc")
    obs.finish("om_1", False, 1.5)
    raw = open(obs.path, encoding="utf-8").read()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    check("工具失败记下错误码，并打掉手机号和密钥",
          any(r.get("kind") == "tool" and r.get("code") == "99991672" and r.get("tool") == "docs_read" for r in rows)
          and "13812345678" not in raw and "abc" not in raw and "手机号已隐藏" in raw, raw)
    check("回答记录只有工具名和错误码，没有问题原文",
          any(r.get("kind") == "answer" and r.get("tools") == [{"tool": "docs_read", "ok": False, "code": "99991672"}] for r in rows)
          and "提问" not in raw and "question" not in raw, raw)
    check("能从异常 detail 里取出错误码",
          observe.code_of('{"code": 3380002, "message": "x"}') == "3380002"
          and observe.code_of('{"error": {"code": 7}}') == "7"
          and observe.code_of("不是 json") == "")
    small = observe.Observe(log_dir, max_bytes=180, backups=2)
    small.path = os.path.join(log_dir, "turn.jsonl")
    blob = "x" * 100
    for i in range(6):
        small.write({"kind": "note", "n": i, "blob": blob})
    obs = small
    check("events.jsonl 超过上限后轮转，旧文件还在",
          os.path.isfile(obs.path) and os.path.isfile(obs.path + ".1") and os.path.getsize(obs.path) < 2000)
    big = os.path.join(log_dir, "stdout.log")
    open(big, "wb").write(b"A" * 80)
    check("被占用风格的日志可以截断并把内容留在备份",
          observe.shrink_file(big, 40, 2) and os.path.getsize(big) == 0 and open(big + ".1", "rb").read() == b"A" * 80)
finally:
    shutil.rmtree(log_dir, ignore_errors=True)

check("密钥字段只报名字",
      doctor.secret_keys({"app_secret": "supersecretvalue", "tools": {"docs_read": {"enabled": True}}}) == ["app_secret"]
      and "supersecretvalue" not in str(doctor.secret_keys({"nested": {"password": "p"}})))
cfg_min = {"profile": "placeholder", "features": {"p2p": False, "attachments": False}, "progress": {"enabled": False},
           "tools": {"history": {"enabled": True}}, "chat_ids": ["oc_placeholder"]}
check("只要求已启用能力的 scope",
      doctor.required_scopes(cfg_min) == ["im:message.group_at_msg:readonly", "im:message:send_as_bot",
                                          "im:message:readonly", "im:message.group_msg"])
cfg_members = dict(cfg_min)
cfg_members["p2p"] = {"allow_group_members": True}
check("群成员私聊打开时，doctor 要求 im:chat.members:read",
      "im:chat.members:read" in doctor.required_scopes(cfg_members)
      and "im:chat.members:read" not in doctor.required_scopes(cfg_min)
      and "im:chat.members:read" not in doctor.required_scopes(dict(cfg_min, p2p={"allow_group_members": False})))
cfg_both_scopes = {"features": {"p2p": True, "attachments": False}, "progress": {"enabled": False}, "tools": {},
                   "chat_ids": [], "chats": {"auto_join": True}, "p2p": {"allow_group_members": True}}
check("自动认群和群成员私聊不把同一权限列两次",
      doctor.required_scopes(cfg_both_scopes).count("im:chat.members:read") == 1)
check("198.18/15 是假 IP，NO_PROXY 含飞书域名则不算漏配",
      doctor.is_fake_ip("198.18.1.5") and doctor.is_fake_ip("198.19.0.1") and not doctor.is_fake_ip("10.0.0.1")
      and not doctor.proxy_covers_feishu("") and doctor.proxy_covers_feishu("open.feishu.cn,localhost"))

calls = []


def run(argv):
    calls.append(list(argv))
    if "consume" in argv:
        raise AssertionError("doctor must not consume")
    if "--as" in argv:
        return 2, "", "Error: unknown flag: --as"
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identities": {"bot": {"available": True, "status": "ready"}}}) + "\n", ""
    if "scopes" in argv:
        return 0, json.dumps({"scopes": doctor.required_scopes(cfg_min)}) + "\n", ""
    if "list" in argv or "check" in argv:
        return 2, "", "Error: unknown command"
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run,
                            lambda: [{"pid": 1, "cmdline": r"python D:\bots\placeholder\agentbot.py --doctor"}],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": False, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("权限齐全时没有 FAIL，可选包缺失只是警告",
      code == 0 and "FAIL" not in text and "WARN" in text and "openpyxl" in text and "scopes" in text, text)
check("诊断不带 --as，也不把 event list 当成订阅",
      calls and all("--as" not in c for c in calls)
      and all(not (len(c) >= 3 and c[1] == "event" and c[2] == "list") for c in calls)
      and "开放平台" in text, str(calls) + "\n" + text)
check("诊断命令里没有 consume、发送或回复",
      calls and all("consume" not in c and "send" not in c and "reply" not in " ".join(c) for c in calls), str(calls))

usage_calls = []


def run_usage(argv):
    usage_calls.append(list(argv))
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli version 1.0.98\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    return 2, "", "Error: unknown flag: --as\nUsage:\n  lark-cli auth status [flags]"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_usage, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("lark-cli 1.0.98 参数错误是 WARN，不是 FAIL",
      code == 0 and "FAIL" not in text and "参数不被这一版接受" in text and "列不出应用权限" in text, text)

miss_scopes = [item for item in doctor.required_scopes(cfg_min) if item != "im:message:send_as_bot"]


def run_missing(argv):
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identity": "bot"}) + "\n", ""
    if "scopes" in argv:
        return 0, json.dumps({"scopes": miss_scopes}) + "\n", ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_missing, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("应用权限列表里缺发送权限是 FAIL",
      code == 1 and "缺少" in text and "im:message:send_as_bot" in text, text)


def run_user_scopes(argv):
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identity": "user", "identities": {"bot": {"available": False, "status": "missing"}}}) + "\n", ""
    if "scopes" in argv:
        # 1.0.98 实机：登录用户的 token。tokenType 固定是 user，userScopes 往往只有 offline_access。
        body = {"appId": "cli_placeholder", "brand": "feishu", "tokenType": "user",
                "userScopes": ["offline_access"], "count": 1}
        return 0, json.dumps(body) + "\n", "Querying app scopes...\n\n"
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_user_scopes, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("1.0.98 用户 token 不是应用权限，降为 WARN；bot 明确不可用仍是 FAIL",
      code == 1 and "应用身份未就绪" in text and "WARN scopes" in text and "用户 token" in text
      and "缺少" not in text and "开放平台" in text, text)

need = doctor.required_scopes(cfg_min)
check("scopes 和 userScopes 同时存在、且没有 tokenType 时用 scopes",
      doctor.app_scopes(json.dumps({"scopes": need, "userScopes": ["contact:user.id:readonly"]})) == need)
check("tokenType 为 user 时即使另有 scopes 字段也不当成应用权限",
      doctor.app_scopes(json.dumps({"tokenType": "user", "scopes": need, "userScopes": ["offline_access"]})) is None)
user_lines = "\n".join([
    "Querying app scopes...",
    "{",
    '  "tokenType": "user",',
    '  "userScopes": [',
    '    "im:message:send_as_bot",',
    '    "offline_access"',
    "  ],",
    '  "count": 2',
    "}",
])
check("多行的用户 token JSON 不会被按行抠成应用权限", doctor.app_scopes(user_lines) is None, user_lines)
check("tokenType 为 tenant 的 scopes 是应用权限",
      doctor.app_scopes(json.dumps({"tokenType": "tenant", "scopes": need})) == need)
check("嵌套的 appScopes 和对象列表也能读",
      doctor.app_scopes(json.dumps({"data": {"appScopes": [{"scope": item} for item in need]}})) == need)
pretty = (
    "Querying app scopes...\n\nApp ID: cli_placeholder\nEnabled scopes (%d):\n\n" % len(need)
    + "".join(" • %s\n" % item for item in need)
)
check("pretty 的圆点列表能读出权限", doctor.app_scopes(pretty) == need, pretty)
table = "SCOPE                              STATUS\n" + "".join("%-34s enabled\n" % item for item in need)
check("文本表格能读出权限", doctor.app_scopes(table) == need, table)
check("Enabled scopes (0) 是空列表，不是没解析到", doctor.app_scopes("Enabled scopes (0):\n\n") == [])
short = [item for item in need if item != "im:message:send_as_bot"]


def run_user_missing(argv):
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identities": {"bot": {"available": True}}}) + "\n", ""
    if "scopes" in argv:
        body = {"tokenType": "user", "userScopes": short, "count": len(short)}
        return 0, json.dumps(body), ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_user_missing, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("userScopes 里缺发送权限是 WARN，不是 FAIL",
      code == 0 and "FAIL" not in text and "WARN scopes" in text and "用户 token" in text
      and "缺少" not in text and "开放平台" in text, text)


def run_user_complete(argv):
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identities": {"bot": {"available": True}}}) + "\n", ""
    if "scopes" in argv:
        body = {"tokenType": "user", "userScopes": list(need), "count": len(need)}
        return 0, json.dumps(body), ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_user_complete, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("userScopes 即使列全了必需权限也不算应用权限核对通过",
      code == 0 and "FAIL" not in text and "OK   scopes" not in text and "用户 token" in text, text)


def run_tenant_missing(argv):
    if argv[1:2] == ["--version"] or (len(argv) > 1 and argv[1] == "--version"):
        return 0, "lark-cli test\n", ""
    if "show" in argv:
        return 0, "profile ok\n", ""
    if "status" in argv:
        return 0, json.dumps({"identities": {"bot": {"available": True}}}) + "\n", ""
    if "scopes" in argv:
        return 0, json.dumps({"tokenType": "tenant", "scopes": short, "count": len(short)}), ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_tenant_missing, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("tenant 权限列表里缺发送权限是 FAIL",
      code == 1 and "FAIL scopes" in text and "缺少" in text and "im:message:send_as_bot" in text, text)

win_argv = doctor.windows_process_argv()
win_script = base64.b64decode(win_argv[win_argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
check("Windows 进程列表走 powershell 的 Win32_Process，不用 pwsh",
      win_argv[0] == "powershell" and "-NoProfile" in win_argv and "-NonInteractive" in win_argv
      and "-EncodedCommand" in win_argv and "pwsh" not in win_argv
      and "Get-CimInstance Win32_Process" in win_script and "[char]9" in win_script
      and "#count=" in win_script and "'{0}`t{1}'" not in win_script, win_script)

mimi_py = '"C:\\Program Files\\Python311\\python.exe" "D:\\bots\\mimi\\agentbot.py" "D:\\bots\\mimi\\agents\\mimi.json"'
mimi_cli = '"C:\\Program Files\\lark-cli\\lark-cli.exe" event consume im.message.receive_v1 --profile mimi --as bot'
mimi_text = "#count=87\n4812\t%s\n5021\t%s\n" % (mimi_py, mimi_cli)
mimi_procs, mimi_visible = doctor.parse_process_snapshot(doctor.decode_process_output(mimi_text.encode("utf-16-le")))
mimi_found = doctor.duplicate_findings(mimi_procs, r"D:\bots\mimi", "mimi")
check("UTF-16 的 Win32_Process 行能认出带空格的 python.exe，Mimi 算 1 个服务进程和 1 个接收进程",
      mimi_visible and mimi_procs[0]["pid"] == 4812 and "Program Files" in mimi_procs[0]["cmdline"]
      and "agentbot.py" in mimi_procs[0]["cmdline"] and any("serving=1 consume=1" in info for _level, info in mimi_found),
      str(mimi_procs) + "\n" + str(mimi_found))
broken = '4812`t%s\n9`tC:\\Windows\\System32\\notepad.exe\n' % mimi_py
broken_procs, broken_visible = doctor.parse_process_snapshot(broken)
check("PowerShell 5.1 单引号留下的字面 `t 仍能解析，路径里的空格留在命令行里",
      broken_visible and broken_procs == [{"pid": 4812, "cmdline": mimi_py}], str(broken_procs))
fmt_list = (
    'ProcessId   : 4812\n'
    'Name        : python.exe\n'
    'CommandLine : "C:\\Program Files\\Python311\\python.exe" "D:\\bots\\mimi\\agent\n'
    '              bot.py" "D:\\bots\\mimi\\agents\\mimi.json"\n'
    '\n'
    'ProcessId   : 5021\n'
    'CommandLine : %s\n' % mimi_cli
)
list_procs, list_visible = doctor.parse_process_snapshot(fmt_list)
check("Format-List 折行后仍能拼出 agentbot.py",
      list_visible and len(list_procs) == 2 and "agentbot.py" in list_procs[0]["cmdline"]
      and "Program Files" in list_procs[0]["cmdline"], str(list_procs))
fmt_table = (
    "ProcessId CommandLine\n"
    "--------- -----------\n"
    "     4812 %s\n"
    "     5021 %s\n"
    "        9 C:\\Windows\\System32\\notepad.exe\n" % (mimi_py, mimi_cli)
)
table_procs, table_visible = doctor.parse_process_snapshot(fmt_table)
check("Format-Table 的对齐行能认出机器人和接收进程，记事本不算",
      table_visible and [item["pid"] for item in table_procs] == [4812, 5021], str(table_procs))
cim_csv = (
    '"ProcessId","CommandLine"\n'
    '"4812","""C:\\Program Files\\Python311\\python.exe"" ""D:\\bots\\mimi\\agentbot.py"" ""D:\\bots\\mimi\\agents\\mimi.json"""\n'
    '"9","C:\\Windows\\System32\\notepad.exe"\n'
    '"5021","""C:\\Program Files\\lark-cli\\lark-cli.exe"" event consume im.message.receive_v1 --profile mimi --as bot"\n'
)
csv_procs, csv_visible = doctor.parse_process_snapshot(cim_csv)
check("ConvertTo-Csv 的引号字段能认出带空格的命令行",
      csv_visible and [item["pid"] for item in csv_procs] == [4812, 5021]
      and "Program Files" in csv_procs[0]["cmdline"] and "agentbot.py" in csv_procs[0]["cmdline"], str(csv_procs))
check("没输出或进程数为 0 是看不见，不是 serving=0",
      doctor.interpret_process_output(0, b"") is None
      and doctor.interpret_process_output(1, b"") is None
      and doctor.interpret_process_output(0, "#count=0\n".encode("utf-16")) is None)
check("枚举到了别的进程但没有机器人，列表为空，调用方可以报 serving=0",
      doctor.interpret_process_output(0, b"#count=40\n") == []
      and doctor.parse_cim_lines("9\tnotepad.exe\n") == [])

lines, code = doctor.report(cfg_min, r"D:\bots\mimi", ["lark-cli"], run, lambda: None,
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("看不到进程时报 WARN，不报 OK serving=0",
      code == 0 and "列不出进程" in text and "serving=0" not in text and "FAIL" not in text, text)

secret_cfg = dict(cfg_min, app_secret="supersecretvalue123")
lines, code = doctor.report(secret_cfg, r"D:\bots\placeholder", ["lark-cli"], run, lambda: [],
                            lambda host: [("198.18.1.5", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {})
text = "\n".join(lines)
check("配置里的 app_secret 判失败，并且不打印值；假 IP 且没配 NO_PROXY 是警告",
      code == 1 and "app_secret" in text and "supersecretvalue123" not in text and "198.18" in text and "WARN" in text, text)

dup = [
    {"pid": 2, "cmdline": r"python D:\bots\placeholder\agentbot.py D:\bots\placeholder\a.json"},
    {"pid": 3, "cmdline": r"python D:\bots\placeholder\agentbot.py D:\bots\placeholder\a.json"},
    {"pid": 4, "cmdline": "lark-cli event consume im.message.receive_v1 --profile placeholder"},
    {"pid": 5, "cmdline": "lark-cli event consume im.message.receive_v1 --profile placeholder"},
    {"pid": 6, "cmdline": "lark-cli event consume im.message.receive_v1 --profile otherbot"},
]
found = doctor.duplicate_findings(dup, r"D:\bots\placeholder", "placeholder")
check("重复的机器人和同一事件的接收进程会被点名，别的 profile 不算，且不包含结束动作",
      any(level == "FAIL" and "agentbot" in info for level, info in found)
      and any("im.message.receive_v1" in info and "count=2" in info for _level, info in found)
      and all("kill" not in info.lower() for _level, info in found), str(found))

work = tempfile.mkdtemp(prefix="agentbot_doc_")
try:
    src_cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src_cfg["base_dir"] = work
    src_cfg["model_provider"] = "fake"
    src_cfg["progress"] = {"enabled": False}
    src_cfg["bot_open_id"] = "ou_placeholder"
    src_cfg["bot_names"] = ["小助手"]
    src_cfg["chat_ids"] = ["oc_placeholder"]
    path = os.path.join(work, "bot.json")
    json.dump(src_cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(work, "redact.txt"), "w", encoding="utf-8").write("")
    fake = {"im +chat-messages-list": {"ok": False, "error": {"code": 12345, "message": "读不到 13812345678"}}}
    fake_path = os.path.join(work, "fake.json")
    json.dump(fake, open(fake_path, "w", encoding="utf-8"), ensure_ascii=False)
    ev = {"message_id": "om_obs", "chat_id": "oc_placeholder", "chat_type": "group", "message_type": "text",
          "sender_id": "user_sender", "sender_type": "user",
          "content": '@小助手 <<FAKE_TOOL>>{"tool":"history","args":{}}<<END>> 问题原文TOKEN不进事件日志',
          "mentions": [{"id": "ou_placeholder", "key": "@_user_1", "name": "小助手"}]}
    events = os.path.join(work, "one.jsonl")
    open(events, "w", encoding="utf-8").write(json.dumps({"event": ev}, ensure_ascii=False) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_LARK"] = fake_path
    r = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=40)
    out = (r.stdout or "") + (r.stderr or "")
    ev_path = os.path.join(work, "logs", "events.jsonl")
    body = open(ev_path, encoding="utf-8").read() if os.path.isfile(ev_path) else ""
    check("真实回答路径把工具错误码写入 events.jsonl，不写问题原文和手机号",
          r.returncode == 0 and '"code": "12345"' in body and "history" in body
          and "TOKEN不进事件日志" not in body and "13812345678" not in body, out + "\n" + body)
    doctor_src = r'''
import os, sys
sys.argv = ["agentbot.py", os.environ["CFG"], "--doctor"]
import agentbot, doctor
def fake_run(cfg, base, exe):
    print("DOCTOR_RAN")
    return 0
doctor.run_doctor = fake_run
agentbot.main()
'''
    env["CFG"] = path
    r = subprocess.run([sys.executable, "-c", doctor_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
    gbk_env = dict(env)
    gbk_env["PYTHONUTF8"] = "0"
    gbk_env["PYTHONIOENCODING"] = "gbk"
    gbk_ev = dict(ev, message_id="om_gbk", content='@小助手 编码探针中文不崩')
    gbk_events = os.path.join(work, "gbk.jsonl")
    open(gbk_events, "w", encoding="utf-8").write(json.dumps({"event": gbk_ev}, ensure_ascii=False) + "\n")
    try:
        gbk = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", gbk_events],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=gbk_env, timeout=40)
        gbk_out = (gbk.stdout or "") + (gbk.stderr or "")
        gbk_ok = gbk.returncode == 0 and "编码探针中文不崩" in gbk_out
    except UnicodeDecodeError as exc:
        gbk_out = str(exc)
        gbk_ok = False
    check("PYTHONUTF8=0 且标准流是 gbk 时，干跑仍能按 UTF-8 读到中文", gbk_ok, gbk_out)
    out = (r.stdout or "") + (r.stderr or "")
    check("--doctor 不写心跳、不启动接收进程",
          r.returncode == 0 and "DOCTOR_RAN" in out and not os.path.isfile(os.path.join(work, "state", "heartbeat"))
          and "consumer" not in out, out)
finally:
    shutil.rmtree(work, ignore_errors=True)

start_txt = open(os.path.join(FW, "scripts", "start.ps1"), encoding="utf-8").read()
watch_txt = open(os.path.join(FW, "scripts", "watchdog.ps1"), encoding="utf-8").read()
rot_txt = open(os.path.join(FW, "scripts", "rotatelog.ps1"), encoding="utf-8").read()
check("启动和看门狗会轮转日志，轮转脚本不结束进程",
      "rotatelog.ps1" in start_txt and "rotatelog.ps1" in watch_txt and "Rotate-LogFile" in rot_txt
      and "taskkill" not in rot_txt.lower() and "Stop-Process" not in rot_txt
      and start_txt.find("already running") < start_txt.find("Rotate-LogFile"))

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
