# 部署后验证只做判断。不连飞书，不发消息，不结束进程。
import json, os, re, subprocess, sys

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import verify_report

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


def levels(rows, name=None):
    return [level for level, item, _info in rows if name is None or item == name]


def infos(rows, name):
    return "\n".join(info for _level, item, info in rows if item == name)


A = r"C:\bots\bot"
B = r"C:\bots\bot2"
C = r"C:\bots\bot3"


def proc(pid, cmd, created="100", cwd=""):
    return {"pid": pid, "cmdline": cmd, "created": created, "cwd": cwd}


def bot(directory, lock, lock_proc, logs=None, events=None):
    return {"dir": directory, "lock_exists": lock is not None, "lock": lock or "", "lock_proc": lock_proc,
            "logs": logs if logs is not None else [{"name": "events.jsonl", "exists": True, "size": 10, "backups": 1},
                                                    {"name": "stdout.log", "exists": True, "size": 10, "backups": 0}],
            "events_tail": events if events is not None else []}


ours = proc(11, r'python "C:\bots\bot\agentbot.py" "C:\bots\bot\agents\a.json"', "100", A)
ours2 = proc(12, r'python "C:\bots\bot2\agentbot.py" "C:\bots\bot2\agents\b.json"', "200", B)
ours3 = proc(13, r'python "C:\bots\bot3\agentbot.py" "C:\bots\bot3\agents\c.json"', "300", C)
consume = lambda pid, profile, event: proc(pid, "lark-cli event consume %s --profile %s --as bot" % (event, profile))

snap = {
    "processes": [
        ours, ours2, ours3,
        consume(21, "app-a", "im.message.receive_v1"),
        consume(22, "app-b", "im.message.receive_v1"),
        consume(23, "app-c", "im.message.receive_v1"),
        consume(24, "app-a", "im.chat.member.bot.added_v1"),
        proc(25, "lark-cli event _bus --profile app-a"),
        proc(26, r'python "C:\bots\bot\agentbot.py" "C:\bots\bot\agents\a.json" --doctor'),
    ],
    "bots": [
        bot(A, "11\ncreated=100\nbase=%s\n" % A, {"alive": True, "pid": 11, "cmdline": ours["cmdline"], "created": "100", "cwd": A}),
        bot(B, "12\ncreated=200\nbase=%s\n" % B, {"alive": True, "pid": 12, "cmdline": ours2["cmdline"], "created": "200", "cwd": B}),
        bot(C, "13\ncreated=300\nbase=%s\n" % C, {"alive": True, "pid": 13, "cmdline": ours3["cmdline"], "created": "300", "cwd": C}),
    ],
    "doctors": [{"label": "app-a", "code": 0, "output": "OK   config 没有 app_secret\nWARN package pillow 未安装"}],
}
rows = verify_report.build_report(snap)
text = verify_report.render(rows)
check("三个目录都列出来，bot 不会当成 bot2",
      "dir=%s" % A in text and "dir=%s" % B in text and "dir=%s" % C in text and "FAIL" not in "\n".join(infos(rows, "agentbot").splitlines()[0:3]), text)
check("同一目录的 --doctor 不算第二个服务进程",
      "不算正在服务" in text and not any(level == "FAIL" and "同一目录" in info for level, _n, info in rows))
check("三个飞书应用各有一个消息接收进程；进群是另一种事件；总线不算",
      "profile=app-a event=im.message.receive_v1 count=1" in text
      and "profile=app-b event=im.message.receive_v1 count=1" in text
      and "profile=app-c event=im.message.receive_v1 count=1" in text
      and "im.chat.member.bot.added_v1 count=1" in text
      and "_bus" not in text
      and not any(level == "FAIL" for level in levels(rows, "consume")))
check("锁的 created 和 base 都对得上",
      not any(level == "FAIL" for level in levels(rows, "lock")) and "created 与进程一致" in text and "base 与目录一致" in text)
check("有轮转备份是 OK，诊断里的 WARN 不会让总结失败",
      "有 1 份备份" in text and "pillow" in text and rows[-1][0] == "OK" and "fail=0" in rows[-1][2], text)

dup = dict(snap)
dup["processes"] = list(snap["processes"]) + [consume(27, "app-a", "im.message.receive_v1")]
dup_rows = verify_report.build_report(dup)
check("同一个飞书应用同一种事件有两个接收进程是 FAIL",
      any(level == "FAIL" and "app-a" in info and "count=2" in info and "不要在这里结束" in info for level, _n, info in dup_rows))

legacy = {"processes": [ours], "bots": [bot(A, "11\n", {"alive": True, "pid": 11, "cmdline": ours["cmdline"], "created": "555", "cwd": A},
                                            logs=[{"name": "stdout.log", "exists": True, "size": 20, "backups": 0}],
                                            events=[])],
          "doctors": []}
legacy_rows = verify_report.build_report(legacy)
check("旧锁没有 created= 且进程仍是本机器人时是 WARN，不是 FAIL",
      any(level == "WARN" and "没有 created=" in info for level, _n, info in legacy_rows)
      and not any(level == "FAIL" for level, _n, _i in legacy_rows if _n == "lock"))

reused = {"processes": [], "bots": [bot(A, "99\n", {"alive": True, "pid": 99, "cmdline": r"C:\Windows\notepad.exe", "created": "1", "cwd": r"C:\Windows"})],
          "doctors": []}
reused_rows = verify_report.build_report(reused)
check("旧锁指向别的程序时 FAIL，并写明不要结束它",
      any(level == "FAIL" and "不要结束" in info for level, _n, info in reused_rows))

mismatch = {"processes": [ours], "bots": [bot(A, "11\ncreated=100\nbase=%s\n" % A,
                                              {"alive": True, "pid": 11, "cmdline": ours["cmdline"], "created": "999", "cwd": A})]}
mismatch_rows = verify_report.build_report(mismatch)
check("created 对不上是 FAIL，不把进程当成自己",
      any(level == "FAIL" and "创建时间对不上" in info for level, _n, info in mismatch_rows))

gone = {"processes": [], "bots": [bot(A, "11\ncreated=100\nbase=%s\n" % A, {"alive": False, "pid": 11})]}
gone_rows = verify_report.build_report(gone)
check("锁还在但进程不在，是 WARN，启动时接管",
      any(level == "WARN" and "过期锁接管" in info for level, _n, info in gone_rows)
      and not any(level == "FAIL" for level in levels(gone_rows, "lock")))

big = {"processes": [], "bots": [bot(A, None, None, logs=[{"name": "stdout.log", "exists": True, "size": 2_500_000, "backups": 0},
                                                          {"name": "events.jsonl", "exists": False, "size": 0, "backups": 0}],
                                    events=None)]}
big_rows = verify_report.build_report(big)
check("日志超过约 2MB 且没有备份时警告轮转没生效",
      any(level == "WARN" and "轮转可能没生效" in info for level, _n, info in big_rows))
empty_logs = {"processes": [], "bots": [bot(A, None, None, logs=[
    {"name": "events.jsonl", "exists": False, "size": 0, "backups": 0},
    {"name": "watchdog.log", "exists": False, "size": 0, "backups": 0},
    {"name": "stdout.log", "exists": False, "size": 0, "backups": 0},
    {"name": "bot.log", "exists": False, "size": 0, "backups": 0},
], events=[])]}
empty_rows = verify_report.build_report(empty_logs)
empty_text = verify_report.render(empty_rows)
check("events.jsonl 和 watchdog.log 不存在是 OK（空），另外的日志不存在仍是 WARN",
      any(level == "OK" and info == "events.jsonl 不存在（空）" for level, name, info in empty_rows if name == "rotate")
      and any(level == "OK" and info == "watchdog.log 不存在（空）" for level, name, info in empty_rows if name == "rotate")
      and any(level == "WARN" and "stdout.log 不存在" in info for level, name, info in empty_rows if name == "rotate")
      and any(level == "WARN" and "bot.log 不存在" in info for level, name, info in empty_rows if name == "rotate")
      and not any(level == "WARN" and name == "rotate" and ("events.jsonl" in info or "watchdog.log" in info) for level, name, info in empty_rows),
      empty_text)

err_line = json.dumps({"kind": "tool", "mid": "m1", "tool": "docs_read", "ok": False, "code": "99991672", "err": "手机号 13812345678"})
ok_line = json.dumps({"kind": "answer", "mid": "m1", "ok": False, "tools": [{"tool": "docs_read", "ok": False, "code": "99991672"}]})
ev = {"processes": [], "bots": [bot(A, None, None, events=[err_line, ok_line, "not-json"])]}
ev_rows = verify_report.build_report(ev)
ev_text = verify_report.render(ev_rows)
check("最近错误码按工具汇总，不把 err 原文打出来",
      any(level == "WARN" and "docs_read" in info and "99991672" in info and "共 1 条" in info for level, _n, info in ev_rows)
      and "13812345678" not in ev_text and "不是 JSON" in ev_text)

doc_fail = {"processes": [], "bots": [], "doctors": [{"label": "app-a", "code": 1, "output": "FAIL scopes 缺少 im:message:send_as_bot"}]}
doc_rows = verify_report.build_report(doc_fail)
check("诊断里的 FAIL 让退出失败",
      any(level == "FAIL" and "scopes" in info for level, _n, info in doc_rows) and doc_rows[-1][0] == "FAIL")

got = subprocess.run([sys.executable, os.path.join(FW, "verify_report.py")], input=json.dumps(snap),
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
check("标准输入的判断不发消息，没有 FAIL 时退出码 0",
      got.returncode == 0 and "fail=0" in (got.stdout or "") and "consume" not in (got.stderr or ""), got.stdout)

bad = subprocess.run([sys.executable, os.path.join(FW, "verify_report.py")], input="",
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
check("没有输入时失败，且不列进程", bad.returncode == 1 and "FAIL" in (bad.stdout or ""))

ps1 = open(os.path.join(FW, "scripts", "verify_windows.ps1"), encoding="utf-8").read()
body = "\n".join(ln for ln in ps1.splitlines() if not ln.strip().startswith("#"))
for word in ("Stop-Process", "taskkill", "Remove-Item", "Set-Content", "Add-Content", "Out-File", "Move-Item", "Start-Process"):
    check("验证脚本不含 %s" % word, word not in body)
check("验证脚本会跑 --doctor，并把结果交给只读判断",
      "--doctor" in body and "verify_report.py" in body and "Get-CimInstance" in body and "bot.lock" in body)
check("Generic.List 用 ToArray，不再放进 @()",
      ".ToArray()" in body and "@($processes)" not in body and "@($bots)" not in body and "@($doctors)" not in body)
check("旧版没有 --doctor 时跳过，退出码 0，不启动",
      "Test-HasDoctor" in body and "没有 --doctor" in body and "已跳过，没有启动" in body and "code = 0" in body)
upgrade = open(os.path.join(FW, "docs", "升级三个机器人.md"), encoding="utf-8").read()
after = open(os.path.join(FW, "docs", "部署后验证.md"), encoding="utf-8").read()
doctor_doc = open(os.path.join(FW, "docs", "升级诊断.md"), encoding="utf-8").read()
check("说明里写了三个机器人的升级和回退",
      "逐个" in upgrade and "created=" in upgrade and "不发" in after)
check("停机前先在旁边克隆并跑完测试，避免看门狗半路拉起",
      "看门狗" in upgrade and "克隆" in upgrade and "run_tests.py" in upgrade
      and upgrade.find("临时克隆") < upgrade.find("停这一个"))
check("轮转和诊断说明与真机误报一致",
      "不存在（空）" in after and "还没有" in after and "--doctor" in after and "event list" in after
      and "--as" in doctor_doc and "auth scopes" in doctor_doc and "开放平台" in doctor_doc)


def ps1_bom_or_ascii(path):
    raw = open(path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        return True
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        return False
    return True


for dirpath, _dirs, names in os.walk(os.path.join(FW, "scripts")):
    for name in sorted(names):
        if not name.lower().endswith(".ps1"):
            continue
        path = os.path.join(dirpath, name)
        check("ps1 带 BOM 或纯 ASCII：%s" % name, ps1_bom_or_ascii(path), path)
        text = open(path, encoding="utf-8-sig").read()
        wrapped = []
        for listed in re.findall(r"\$(\w+)\s*=\s*New-Object System\.Collections\.Generic\.List", text):
            if "@($%s)" % listed in text:
                wrapped.append(listed)
        check("ps1 不用 @() 包 Generic.List：%s" % name, not wrapped, " ".join(wrapped))

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
