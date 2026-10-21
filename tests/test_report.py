# 用量周报。默认关。不发送时也能导出。周报里没有原文和编号。
import csv, io, json, logging, os, shutil, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import digest, feishu_link, guard, tools, usage

results = []
FIX = os.path.join(FW, "tests", "fixtures", "usage_week.jsonl")
EXAMPLE = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8"))
NOW = 1792371600  # 2026-10-19 09:00 Asia/Shanghai


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


def _cfg(**report):
    base = {"enabled": True, "owner_open_ids": ["ou_owner1"], "weekday": "mon", "time": "09:00",
            "timezone": "Asia/Shanghai", "window_min": 180}
    base.update(report)
    return {"report": base, "alerts": {"enabled": False, "owner_open_ids": ["ou_alert1"]}}


def _section(report, group):
    for item in report["sections"]:
        if item["group"] == group:
            return item
    return None


check("例子里的周报默认关着", EXAMPLE.get("report", {}).get("enabled") is False and EXAMPLE["report"]["time"] == "09:00"
      and EXAMPLE["report"]["weekday"] == "mon" and EXAMPLE["report"]["window_min"] == 180)

bare = usage.settings({})
check("不写 report 时不发送，时间仍是周一 09:00 东八区",
      bare["enabled"] is False and bare["weekday"] == 0 and bare["weekday_ok"] and bare["hour"] == 9
      and bare["minute"] == 0 and bare["time_ok"] and bare["window"] == 180 and bare["timezone"] == "Asia/Shanghai"
      and bare["owners"] == [])
bad = usage.settings({"report": {"enabled": True, "time": "25:99", "weekday": "不存在", "window_min": -3,
                                  "owner_open_ids": ["REPLACE_OWNER", "ou_real1", "bad id"]}})
check("写错的时间、星期和占位符不会变成一次发送",
      bad["enabled"] is True and bad["time_ok"] is False and bad["weekday_ok"] is False and bad["window"] == 180
      and bad["owners"] == ["ou_real1"])
check("report 的主人优先，空着才用 alerts，alerts 关着也行",
      usage.owner_ids(_cfg(owner_open_ids=["ou_report1"])) == ["ou_report1"]
      and usage.owner_ids(_cfg(owner_open_ids=[])) == ["ou_alert1"]
      and usage.owner_ids({"report": {"owner_open_ids": ["REPLACE_OWNER"]},
                           "alerts": {"enabled": False, "owner_open_ids": ["ou_alert1"]}}) == ["ou_alert1"]
      and usage.is_owner(_cfg(), "ou_owner1") and not usage.is_owner(_cfg(), "ou_other"))

names = ["杭州某某科技有限公司"]
raw = "电话 13812345678 找杭州某某科技有限公司"
text, hits = guard.redact_counted(raw, names)
check("脱敏次数和原来的替换结果一致，次数里没有原文",
      text == guard.redact(raw, names) and hits >= 2 and isinstance(hits, int)
      and "13812345678" not in text and "杭州某某" not in text and "13812345678" not in str(hits))
check("文件类型只留扩展名",
      usage.safe_file_type("客户合同.PDF") == "pdf" and "客户合同" not in usage.safe_file_type("客户合同.PDF")
      and usage.safe_file_type("noext") == "unknown" and usage.safe_file_type("目录/没有点") == "unknown"
      and usage.safe_file_type(r"C:\资料\报价.docx") == "docx")

work = tempfile.mkdtemp(prefix="agentbot_usage_")
try:
    os.makedirs(os.path.join(work, "kb", "notes"), exist_ok=True)
    open(os.path.join(work, "kb", "notes", "客户合同.md"), "w", encoding="utf-8").write("正文\n")
    usage.configure(work, test=False)
    usage.bind("oc_chat1", "group", "live")
    bot = tools.Tools({"name": "t", "profile": "p", "kb_dir": "kb", "tools": {"kb_read": {"enabled": True}},
                       "write": {"enabled": True, "confirm_ttl_sec": 1, "allowed_users": {}}},
                      work, lambda: ["lark-cli"], logging.getLogger("usage-test"), dry=True)
    got = bot.run_read("kb_read", {"path": "notes/客户合同.md"},
                       {"user": "ou_user1", "chat_id": "oc_chat1", "chat_type": "group", "message_id": "m1"})
    audit_dir = os.path.join(work, "audit")
    audit_name = sorted(os.listdir(audit_dir))[0]
    audit = open(os.path.join(audit_dir, audit_name), encoding="utf-8").read()
    usage_body = open(os.path.join(work, "logs", "usage.jsonl"), encoding="utf-8").read()
    check("读文件的审计有类型，没有原文件名",
          "正文" in got and '"file_types": ["md"]' in audit and "客户合同" not in audit and "notes/" not in audit, audit)
    check("用量里的文件类型也没有路径和编号",
          '"file_type": "md"' in usage_body and "客户合同" not in usage_body and "oc_chat1" not in usage_body
          and "ou_user1" not in usage_body, usage_body)
    bot.pending["111111"] = {"tool": "docs_create", "user": "ou_user1", "chat_id": "oc_chat1",
                             "ts": time.time() - 10000, "usage_scope": "group",
                             "memory_text": "记忆正文不要出现", "summary": "摘要不要出现XYZ"}
    bot._gc()
    audit = open(os.path.join(audit_dir, audit_name), encoding="utf-8").read()
    usage_body = open(os.path.join(work, "logs", "usage.jsonl"), encoding="utf-8").read()
    check("过期的确认码只记动作，不抄待写入的正文",
          "write_expired" in audit and "记忆正文不要出现" not in audit and "摘要不要出现XYZ" not in audit
          and '"action": "expired"' in usage_body and "记忆正文不要出现" not in usage_body
          and "ou_user1" not in usage_body and "oc_chat1" not in usage_body, usage_body)
    usage.unbind()
finally:
    shutil.rmtree(work, ignore_errors=True)

check("中位数取中间两位的平均，P90 用最近秩",
      usage.median([1000, 2000, 3000, 4000, 5000, 10000]) == 3500
      and usage.median([800, 1000, 2000, 3000, 4000, 5000, 10000]) == 3000
      and usage.percentile([800, 1000, 2000, 3000, 4000, 5000, 10000], 90) == 10000
      and usage.percentile([2000, 4000], 90) == 4000)

sample = tempfile.mkdtemp(prefix="agentbot_week_")
try:
    os.makedirs(os.path.join(sample, "logs"), exist_ok=True)
    shutil.copy(FIX, os.path.join(sample, "logs", "usage.jsonl"))
    report = usage.collect(sample, usage._stamp(usage.period_for(_cfg(), NOW, "2026-W42")[0]),
                           usage._stamp(usage.period_for(_cfg(), NOW, "2026-W42")[1]))
    total = _section(report, "all")
    g1 = _section(report, "g1")
    g2 = _section(report, "g2")
    check("样例周：合计 7 题 6 份完成，中位数 3 秒，P90 10 秒",
          total["questions"] == {"cur": 7, "prev": 2} and total["answers"] == {"cur": 6, "prev": 2}
          and total["p50"]["cur"] == 3000 and total["p90"]["cur"] == 10000 and total["p50"]["prev"] == 3000
          and total["p90"]["prev"] == 4000 and total["redact"] == {"cur": 3, "prev": 1}
          and report["skipped_test"] == 1 and report["skipped_replay"] == 1, total)
    check("样例周：工具、文件类型、写入和错误按群加总",
          total["tools"]["web_search"] == {"cur": 3, "prev": 1} and total["tools"]["docs"]["cur"] == 2
          and total["tools"]["calendar"]["cur"] == 1 and total["tools"]["base"]["cur"] == 0
          and total["files"]["pdf"] == {"cur": 3, "prev": 1} and total["files"]["docx"]["cur"] == 1
          and total["files"]["png"]["cur"] == 1 and total["writes"]["proposed"] == {"cur": 3, "prev": 1}
          and total["writes"]["confirmed"] == {"cur": 1, "prev": 1} and total["writes"]["cancelled"]["cur"] == 1
          and total["writes"]["expired"]["cur"] == 1 and total["errors"]["model"]["cur"] == 1
          and total["errors"]["timeout"]["cur"] == 1 and total["errors"]["permission"]["cur"] == 1
          and total["drops"] == {"cur": 1, "prev": 0} and total["downtime"] == {"cur": 600, "prev": 0}
          and total["health"]["down"]["cur"] == 1 and total["health"]["recover"]["cur"] == 1, total)
    check("样例周：群和私聊分开，连接中断只在合计",
          g1["questions"]["cur"] == 6 and g1["answers"]["cur"] == 5 and g1["p50"]["cur"] == 3500
          and g2["scope"] == "p2p" and g2["questions"]["cur"] == 1 and g2["p50"]["cur"] == 800
          and g2["files"]["png"]["cur"] == 1 and g1["drops"] is None and g2["health"] is None)
    md, csv_text, err = usage.build(sample, _cfg(), NOW, "2026-W42", names)
    check("周报正文没有原文、编号和客户名",
          err == "" and "用量周报（2026-10-12 ~ 2026-10-18）" in md and "未计入演练 1 条、补答 1 条。" in md
          and "| 处理问题 | 7 | 2 | +5 |" in md and "| 完成回答 | 6 | 2 | +4 |" in md
          and "| 响应时间中位数 | 3.0 秒 | 3.0 秒 | +0.0 秒 |" in md
          and "| 响应时间 P90 | 10.0 秒 | 4.0 秒 | +6.0 秒 |" in md
          and "网页搜索：3（+2）" in md and "pdf 3（+2）" in md and "docx 1（+1）" in md
          and "写入：提议 3（+2），确认 1（+0），取消 1（+1），过期 1（+1）" in md
          and "连接中断：1 次（+1），累计中断 600 秒（+600）" in md
          and "问题类别：文档 2、其他 1、帮助 1、搜索 1、表格 1" in md
          and "## 群 1" in md and "## 私聊 2" in md
          and "原始问题不要出现XYZ" not in md and "ou_poisonowner" not in md and "oc_poisonchat" not in md
          and "open_id" not in md and "杭州某某" not in md and "13812345678" not in md, md)
    table = list(csv.reader(io.StringIO(csv_text)))
    def cell(group, metric):
        for row in table:
            if row[1] == group and row[2] == metric:
                return row
        return None
    qrow = cell("all", "questions")
    prow = cell("all", "latency_p90_ms")
    frow = cell("all", "tool_file_pdf")
    check("CSV 用英文列名，坏行不会变成一行",
          table[0] == ["scope", "group", "metric", "current", "previous", "delta"]
          and qrow[:3] == ["total", "all", "questions"] and qrow[3:] == ["7", "2", "5"]
          and float(prow[3]) == 10000 and float(prow[4]) == 4000
          and frow[2] == "tool_file_pdf" and frow[3] == "3"
          and "原始问题不要出现XYZ" not in csv_text and "ou_poisonowner" not in csv_text
          and "oc_poisonchat" not in csv_text, csv_text[:500])
finally:
    shutil.rmtree(sample, ignore_errors=True)

slot = tempfile.mkdtemp(prefix="agentbot_slot_")
try:
    usage.configure(slot, test=False)
    os.makedirs(os.path.join(slot, "logs"), exist_ok=True)
    shutil.copy(FIX, os.path.join(slot, "logs", "usage.jsonl"))
    calls = []

    def sender(kind, target, text, key):
        calls.append((kind, target, text, key))
        return "ok"

    sent = usage.deliver(_cfg(owner_open_ids=["ou_owner1", "ou_owner2"]), slot, (), NOW, sender, None)
    state = open(usage._report_state(slot), encoding="utf-8").read()
    check("到点给每位主人一份，状态文件不留编号",
          sent == ["ou_owner1", "ou_owner2"] and len(calls) == 2 and all(item[0] == "user" for item in calls)
          and "用量周报" in calls[0][2] and "ou_owner1" not in calls[0][2] and "ou_" not in state
          and "oc_" not in state, state)
    calls.clear()
    again = usage.deliver(_cfg(owner_open_ids=["ou_owner1", "ou_owner2"]), slot, (), NOW, sender, None)
    check("同一周再调用不再发送", again == [] and calls == [])

    fail_dir = tempfile.mkdtemp(prefix="agentbot_fail_")
    try:
        os.makedirs(os.path.join(fail_dir, "logs"), exist_ok=True)
        shutil.copy(FIX, os.path.join(fail_dir, "logs", "usage.jsonl"))
        mode = {"value": "fail"}

        def flaky(kind, target, text, key):
            return mode["value"]

        missed = usage.deliver(_cfg(), fail_dir, (), NOW, flaky, None)
        mode["value"] = "ok"
        retried = usage.deliver(_cfg(), fail_dir, (), NOW, flaky, None)
        check("明确失败会放掉名额，下一次还能发", missed == [] and retried == ["ou_owner1"])
        mode["value"] = "unknown"
        usage.deliver(_cfg(owner_open_ids=["ou_owner9"]), fail_dir, (), NOW + 60, flaky, None)
        held = usage.deliver(_cfg(owner_open_ids=["ou_owner9"]), fail_dir, (), NOW + 60, flaky, None)
        check("看不懂结果就保持占坑，不发第二份", held == [])
    finally:
        shutil.rmtree(fail_dir, ignore_errors=True)

    quiet = tempfile.mkdtemp(prefix="agentbot_quiet_")
    try:
        def boom(kind, target, text, key):
            raise AssertionError("disabled report must not send")

        none = usage.deliver({"report": {"enabled": False, "owner_open_ids": ["ou_owner1"]}}, quiet, (), NOW, boom, None)
        check("enabled 为 false 不发送，也不写周报状态",
              none == [] and not os.path.isfile(usage._report_state(quiet)))
        skipped = usage.deliver(_cfg(owner_open_ids=[]), slot, (), NOW, boom, None)
        check("没有主人时不发送", skipped == [] and calls == [])
        early = usage.deliver(_cfg(), slot, (), 1792371540, boom, None)
        late = usage.deliver(_cfg(), slot, (), 1792382400, boom, None)
        sunday = usage.deliver(_cfg(), slot, (), 1792285200, boom, None)
        check("窗口外不发送：周一 08:59、12:00 和周日早上", early == [] and late == [] and sunday == [])
        bad_day = usage.deliver(_cfg(weekday="昨天"), slot, (), NOW, boom, None)
        check("认不出的星期不发送", bad_day == [])
    finally:
        shutil.rmtree(quiet, ignore_errors=True)
finally:
    shutil.rmtree(slot, ignore_errors=True)

link_dir = tempfile.mkdtemp(prefix="agentbot_link_")
try:
    usage.configure(link_dir, test=False)
    watch = feishu_link.LinkWatch()
    watch.on_change = usage.make_link_handler()
    watch.mark_link(True, now=1000)
    first = usage.read_rows(link_dir)
    watch.mark_link(False, now=2000)
    watch.mark_link(True, now=2600)
    later = [row for row in usage.read_rows(link_dir) if row.get("kind") == "link"]
    check("第一次连上不算恢复，断开再连上才记中断时长",
          first == [] and [row.get("event") for row in later] == ["down", "up"]
          and later[1].get("downtime_sec") == 600)
finally:
    shutil.rmtree(link_dir, ignore_errors=True)
usage.configure("", test=False)


def _load_example(work, **over):
    cfg = json.loads(json.dumps(EXAMPLE))
    cfg["base_dir"] = work
    cfg["bot_open_id"] = "ou_bot_placeholder"
    cfg["profile"] = "prof_test"
    cfg["chat_ids"] = ["oc_chat1"]
    cfg["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    cfg.update(over)
    path = os.path.join(work, "bot.json")
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(work, "redact.txt"), "w", encoding="utf-8").write("杭州某某科技有限公司\n")
    os.makedirs(os.path.join(work, "logs"), exist_ok=True)
    shutil.copy(FIX, os.path.join(work, "logs", "usage.jsonl"))
    return path


def _run(work, args, events=None, env_over=None):
    path = args[0] if args and os.path.isfile(args[0]) else _load_example(work)
    cmd = [sys.executable, os.path.join(FW, "agentbot.py"), path] + list(args[1:] if args and os.path.isfile(args[0]) else args)
    if events:
        cmd.append(events)
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    if env_over:
        env.update(env_over)
    proc = subprocess.run(cmd, cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _reply_of(text, mid):
    key = "[dry-run] reply %s: " % mid
    i = text.find(key)
    if i < 0:
        return ""
    lines = []
    for n, line in enumerate(text[i + len(key):].splitlines()):
        if n and (line.startswith("Q ") or line.startswith("[dry-run]") or line.startswith("chat gate")
                  or line.startswith("agent ") or line.startswith("dry-run finished") or " INFO " in line[:40]):
            break
        lines.append(line)
    return "\n".join(lines)


cli = tempfile.mkdtemp(prefix="agentbot_cli_")
try:
    path = _load_example(cli, report={"enabled": True, "owner_open_ids": ["ou_owner1"], "weekday": "mon",
                                      "time": "09:00", "timezone": "Asia/Shanghai", "window_min": 180})
    md_path = os.path.join(cli, "周报.md")
    csv_path = os.path.join(cli, "周报.csv")
    code, out = _run(cli, [path, "--report", "--week", "2026-W42", "--out", md_path])
    body = open(md_path, encoding="utf-8").read() if os.path.isfile(md_path) else ""
    check("命令行导出 markdown，不发送",
          code == 0 and "用量周报（2026-10-12 ~ 2026-10-18）" in body and "| 处理问题 | 7 | 2 | +5 |" in body
          and not os.path.isfile(os.path.join(cli, "state", "report_state.json"))
          and "ou_owner1" not in body, out[-400:])
    code, out = _run(cli, [path, "--report", "--week", "2026-W42", "--out", csv_path])
    raw_csv = open(csv_path, "rb").read() if os.path.isfile(csv_path) else b""
    check("命令行导出的 CSV 带 UTF-8 BOM",
          code == 0 and raw_csv.startswith(b"\xef\xbb\xbf") and b"tool_file_pdf" in raw_csv)
    code, out = _run(cli, [path, "--report", "--week", "不是一周"])
    check("写错的 --week 退出码是 2", code == 2 and "2026-W42" in out)
    code, out = _run(cli, [path, "--report", "--out", os.path.join(cli, "周报.txt")])
    check("不能导出的后缀退出码是 2", code == 2)
    loaded = json.load(open(path, encoding="utf-8"))
    loaded.pop("bot_open_id", None)
    bare_path = os.path.join(cli, "bare.json")
    json.dump(loaded, open(bare_path, "w", encoding="utf-8"), ensure_ascii=False)
    code, out = _run(cli, [bare_path, "--report", "--week", "2026-10-12"])
    check("只导出时可以不填 bot_open_id", code == 0 and "处理问题" in out, out[-400:])
finally:
    shutil.rmtree(cli, ignore_errors=True)


def _events(path, items):
    open(path, "w", encoding="utf-8").write("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items))


def _mentioned(mid, text, sender, chat_type="group"):
    return {"event": {"message_id": mid, "chat_id": "oc_chat1" if chat_type == "group" else "oc_p2p1",
                      "chat_type": chat_type, "message_type": "text", "sender_id": sender, "sender_type": "user",
                      "content": "@小助手 " + text,
                      "mentions": [{"id": "ou_bot_placeholder", "key": "@_user_1", "name": "小助手"}]}}


cmd = tempfile.mkdtemp(prefix="agentbot_cmd_")
try:
    path = _load_example(cmd, report={"enabled": True, "owner_open_ids": ["ou_owner1"], "weekday": "mon",
                                      "time": "09:00", "timezone": "Asia/Shanghai", "window_min": 180},
                         p2p={"allow_open_ids": ["ou_owner1", "ou_other"], "open_to_all": False})
    events = os.path.join(cmd, "events.jsonl")
    sink = os.path.join(cmd, "sent.jsonl")
    _events(events, [
        _mentioned("own", "/report", "ou_owner1"),
        _mentioned("str", "/report", "ou_other"),
        _mentioned("p2", "/report", "ou_owner1", "p2p"),
        _mentioned("st", "/状态", "ou_owner1"),
    ])
    code, out = _run(cmd, [path, "--dry-run", events], env_over={"AGENTBOT_FAKE_SEND": sink, "AGENTBOT_FAKE_SEND_RESULT": "ok"})
    own = _reply_of(out, "own")
    stranger = _reply_of(out, "str")
    private = _reply_of(out, "p2")
    status = _reply_of(out, "st")
    sent = open(sink, encoding="utf-8").read() if os.path.isfile(sink) else ""
    sent_rows = [json.loads(line) for line in sent.splitlines() if line.strip()]
    check("群里的 /report 只给主人私聊明细",
          code == 0 and "已私聊" in own and "群里不贴明细" in own and "处理问题" not in own
          and sent_rows and sent_rows[0]["target"] == "ou_owner1" and "处理问题" in sent_rows[0]["text"]
          and "ou_owner1" not in sent_rows[0]["text"] and "原始问题不要出现XYZ" not in sent_rows[0]["text"]
          and not os.path.isfile(os.path.join(cmd, "state", "report_state.json")), own + "\n" + sent[:400])
    check("不是主人看不到合计",
          "只有主人可以查看用量周报。" in stranger and "处理问题" not in stranger and "网页搜索" not in stranger, stranger)
    check("主人的私聊里直接给出周报",
          "用量周报（" in private and "处理问题" in private and "ou_owner1" not in private, private[-400:])
    check("打开后 /状态 多一行，不打印编号",
          "用量周报：开" in status and "ou_owner1" not in status and "oc_chat1" not in status, status)
finally:
    shutil.rmtree(cmd, ignore_errors=True)

off = tempfile.mkdtemp(prefix="agentbot_offreport_")
try:
    path = _load_example(off)
    loaded = json.load(open(path, encoding="utf-8"))
    loaded.pop("report", None)
    json.dump(loaded, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    events = os.path.join(off, "events.jsonl")
    _events(events, [_mentioned("r0", "/report", "ou_owner1"), _mentioned("s0", "/状态", "ou_owner1")])
    code, out = _run(off, [path, "--dry-run", events], env_over={"AGENTBOT_FAKE_DUMP_PROMPT": "1"})
    reply = _reply_of(out, "r0")
    status = _reply_of(out, "s0")
    check("没打开时 /report 仍交给模型，/状态 不提周报",
          code == 0 and "agent start r0" in out and "只有主人" not in reply and "只有主人" not in out
          and "用量周报" not in status, status + "\n" + reply)
finally:
    shutil.rmtree(off, ignore_errors=True)

gap = tempfile.mkdtemp(prefix="agentbot_gap_")
try:
    os.makedirs(os.path.join(gap, "kb", "notes"), exist_ok=True)
    open(os.path.join(gap, "kb", "notes", "客户合同.md"), "w", encoding="utf-8").write("文件正文标记\n")
    path = _load_example(gap)
    os.remove(os.path.join(gap, "logs", "usage.jsonl"))
    fake = os.path.join(gap, "lark.json")
    json.dump({"im +chat-messages-list": {"ok": False, "error": {"code": 99991672, "message": "access denied"}}},
              open(fake, "w", encoding="utf-8"), ensure_ascii=False)
    events = os.path.join(gap, "events.jsonl")
    _events(events, [_mentioned("h1", "电话 13812345678 " + '<<FAKE_TOOL>>{"tool":"history","args":{}}<<END>> 查一下', "ou_owner1"),
                     _mentioned("k1", '<<FAKE_TOOL>>{"tool":"kb_read","args":{"path":"notes/客户合同.md"}}<<END>> 读文件', "ou_owner1")])
    code, out = _run(gap, [path, "--dry-run", events], env_over={"AGENTBOT_FAKE_LARK": fake})
    body = open(os.path.join(gap, "logs", "usage.jsonl"), encoding="utf-8").read()
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    journal = open(os.path.join(gap, "logs", "journal.jsonl"), encoding="utf-8").read()
    ev_body = open(os.path.join(gap, "logs", "events.jsonl"), encoding="utf-8").read()
    ev_rows = [json.loads(line) for line in ev_body.splitlines() if line.strip()]
    turns = [row for row in rows if row.get("kind") == "turn"]
    check("演练流量、脱敏次数和权限错误能记下来，原文不进计数",
          code == 0 and turns and all(row.get("traffic") == "test" for row in turns)
          and any(row.get("redact_hits", 0) >= 1 for row in turns)
          and any(row.get("kind") == "error" and row.get("category") == "permission" for row in rows)
          and "13812345678" not in body and "客户合同" not in body and "oc_chat1" not in body, body)
    kb = [row for row in ev_rows if row.get("kind") == "tool" and row.get("tool") == "kb_read"]
    ans = [row for row in ev_rows if row.get("kind") == "answer" and row.get("mid") == "k1"]
    check("事件日志只给读文件补上类型，回答摘要仍是三项",
          kb and kb[0].get("file_types") == ["md"] and "客户合同" not in ev_body
          and ans and ans[-1].get("tools") == [{"tool": "kb_read", "ok": True, "code": ""}])
    check("流水记了演练标记，不记问题和手机号",
          '"traffic": "test"' in journal and "13812345678" not in journal and "查一下" not in journal)
finally:
    shutil.rmtree(gap, ignore_errors=True)

failed = [name for name, ok in results if not ok]
print("%d checks, %d failed" % (len(results), len(failed)))
sys.exit(1 if failed else 0)
