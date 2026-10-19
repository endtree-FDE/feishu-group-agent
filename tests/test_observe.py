# 结构化日志和只读诊断。不连飞书，不发消息，不结束进程。
import json, os, shutil, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
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
        return 0, json.dumps({"tokenType": "user", "userScopes": doctor.required_scopes(cfg_min)}) + "\n", ""
    return 1, "", "unexpected"


lines, code = doctor.report(cfg_min, r"D:\bots\placeholder", ["lark-cli"], run_user_scopes, lambda: [],
                            lambda host: [("1.2.3.4", 443)], lambda host, port: True,
                            {"pypdf": True, "openpyxl": True, "PIL": True}, {"NO_PROXY": "open.feishu.cn"})
text = "\n".join(lines)
check("用户令牌的 userScopes 不算应用权限，bot 明确不可用是 FAIL",
      code == 1 and "应用身份未就绪" in text and "没有解析到应用权限列表" in text and "缺少" not in text, text)

win_argv = doctor.windows_process_argv()
check("Windows 进程列表走 powershell 的 Win32_Process，不用 pwsh",
      win_argv[0] == "powershell" and "-NoProfile" in win_argv and "-NonInteractive" in win_argv
      and "Get-CimInstance Win32_Process" in win_argv[-1] and "pwsh" not in win_argv)
raw16 = "12\tC:\\Python\\python.exe C:\\bots\\a\\agentbot.py cfg.json\n".encode("utf-16")
parsed = doctor.parse_cim_lines(doctor.decode_process_output(raw16))
check("能读 PowerShell 5.1 的 UTF-16 进程行，并丢掉无关进程",
      parsed == [{"pid": 12, "cmdline": r"C:\Python\python.exe C:\bots\a\agentbot.py cfg.json"}]
      and doctor.parse_cim_lines("9\tnotepad.exe\n") == [])

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
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=40)
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
    r = subprocess.run([sys.executable, "-c", doctor_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
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
