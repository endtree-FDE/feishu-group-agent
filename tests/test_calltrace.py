# 调用记录。不连飞书，不外传。客户名和密钥只出现在用例输入里，落盘前要被打掉。
import json, os, shutil, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import calltrace, usage
from providers import settings as provider_settings

results = []
NAME = "杭州某某科技有限公司"
PHONE = "13812345678"


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


check("旧配置没写 calls 时默认记，false 才关掉",
      calltrace.enabled({}) and calltrace.enabled({"observability": {"journal": False}})
      and not calltrace.enabled({"observability": {"calls": False}})
      and not calltrace.enabled({"observability": {"calls": {"enabled": False}}})
      and calltrace.settings({"observability": {"calls": {"retain_days": 7}}})["retain_days"] == 7
      and calltrace.settings({"observability": {"calls": {"retain_days": 0}}})["retain_days"] == 30)

provider_settings.note_usage({"prompt_tokens": 11, "completion_tokens": 7})
pin, pout = calltrace.take_tokens()
check("模型报上来的 token 能记到当前这一步", pin == 11 and pout == 7)

work = tempfile.mkdtemp(prefix="agentbot_calls_")
try:
    spec = {"report": {"enabled": True, "timezone": "Asia/Shanghai", "weekday": "mon", "time": "09:00"}}
    start, end, err = usage.period_for(spec, time.time(), "2026-W42")
    check("能定位 2026-W42", err == "" and start is not None, err)
    epoch = start.timestamp() + 3600
    old = time.time() - 40 * 86400
    book = calltrace.Book(os.path.join(work, "logs"), [NAME], {})
    book.begin("oldmid", "旧问题", now=old)
    book.finish("oldmid", True, 0.1, "旧结果", traffic="live", now=old)
    book.begin("live1", "请问 %s %s app_secret=abc /home/ubuntu/x 截止" % (NAME, PHONE),
               chat="group", parent="parentmid", traffic="live", now=epoch)
    book.skill("live1", "post-mortem")
    book.tool("live1", "skill_read", True, 0.2, "不要写出 " + NAME, skill="post-mortem")
    book.model("live1", 0.4, 11, 7, "周五前")
    book.finish("live1", True, 0.9, "周五前交给 " + NAME, traffic="live", now=time.time())
    book.begin("drill", "演练问题", traffic="test", now=epoch)
    book.finish("drill", True, 0.2, "演练结果", traffic="test", now=time.time())
    raw = open(book.path, encoding="utf-8").read()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    check("调用链记下技能、工具、耗时和 token，并打掉客户名、手机号、密钥和路径",
          any(r.get("ref") and r.get("skills") == ["post-mortem"] and r.get("tokens_in") == 11
              and r.get("tokens_out") == 7 and any(s.get("kind") == "tool" and s.get("name") == "skill_read" for s in r["steps"])
              for r in rows)
          and NAME not in raw and PHONE not in raw and "app_secret=abc" not in raw and "/home/ubuntu" not in raw
          and "某客户" in raw and "[手机号已隐藏]" in raw and "旧问题" not in raw and "oldmid" not in raw
          and "live1" not in raw, raw)
    summary = calltrace.summarize(book.path, start.timestamp(), end.timestamp())
    check("周报只合计正式调用，演练不算",
          summary and summary["n"] == 1 and summary["fail"] == 0 and summary["tokens_in"] == 11
          and summary["skills"].get("post-mortem") == 1 and summary["tools"].get("skill_read") == 1, str(summary))
    usage.configure(work, test=False)
    md, csv_text, err = usage.build(work, spec, time.time(), "2026-W42", [NAME])
    check("用量周报带上调用合计，不含问题原文和客户名",
          err == "" and md and "调用记录（本机合计，已脱敏）" in md and "本周 1 条" in md
          and "post-mortem 1" in md and NAME not in md and PHONE not in md
          and "calls" in csv_text and "calls_prompt_tokens" in csv_text, (md or "")[-800:])
    owner = {"user": "user_admin", "chat_type": "p2p", "chat_id": "chat_owner", "parent_id": "live1"}
    shown = calltrace.handle_text({}, work, "最近调用", owner, [NAME], owner=True)
    why = calltrace.handle_text({}, work, "这条怎么答的", owner, [NAME], owner=True)
    check("主人私聊能看最近调用，并按所回复的那条解释",
          shown and "某客户" in shown and "post-mortem" in shown and NAME not in shown
          and why and "这条是这样答的" in why and "skill_read" in why and "输入11" in why and NAME not in why,
          (shown or "") + "\n" + (why or ""))
    stranger = calltrace.handle_text({}, work, "最近调用", dict(owner, user="user_other"), [NAME], owner=False)
    group = calltrace.handle_text({}, work, "这条怎么答的", dict(owner, chat_type="group"), [NAME], owner=True)
    check("别人和群里看不到明细",
          stranger == "只有主人可以查看调用记录。" and "某客户" not in (stranger or "")
          and group and "群里不贴明细" in group and "某客户" not in group and "skill_read" not in group, group or "")
    check("普通问题不是查看命令", calltrace.handle_text({}, work, "今天交什么", owner, [NAME], owner=True) is None)
    off_dir = os.path.join(work, "off")
    off = calltrace.Book(os.path.join(off_dir, "logs"), [NAME], {"observability": {"calls": {"enabled": False}}})
    off.begin("x", NAME + " 的问题")
    off.finish("x", True, 0.1, "不该落下")
    check("关掉之后不写文件", not os.path.isfile(off.path))
    closed = calltrace.handle_text({"observability": {"calls": False}}, work, "最近调用", owner, [NAME], owner=True)
    check("关掉之后私聊会说明现在没有记录", closed == "调用记录现在关着，所以没有可看的内容。", closed or "")
finally:
    shutil.rmtree(work, ignore_errors=True)

run = tempfile.mkdtemp(prefix="agentbot_calls_run_")
try:
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = run
    src["model_provider"] = "fake"
    src["progress"] = {"enabled": False}
    src["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    src["bot_open_id"] = "ou_placeholder"
    src["bot_names"] = ["小助手"]
    src["chat_ids"] = ["chat_group1"]
    src["p2p"] = {"allow_open_ids": ["user_admin", "user_other"], "open_to_all": True}
    src["write"]["allowed_users"] = {"user_admin": "管理员"}
    src["skills"] = {"enabled": True, "dir": os.path.join(FW, "skills"), "preload": False, "only": ["post-mortem"]}
    src.pop("observability", None)
    path = os.path.join(run, "bot.json")
    json.dump(src, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(run, "redact.txt"), "w", encoding="utf-8").write(NAME + "\n")
    ask = {
        "message_id": "callmid1", "chat_id": "chat_group1", "chat_type": "group", "message_type": "text",
        "sender_id": "user_sender", "sender_type": "user",
        "content": '@小助手 <<FAKE_TOOL>>{"tool":"skill_read","name":"post-mortem"}<<END>> %s %s 什么时候交' % (NAME, PHONE),
        "mentions": [{"id": "ou_placeholder", "key": "@_user_1", "name": "小助手"}],
    }
    recent = {"message_id": "callmid2", "chat_id": "chat_owner", "chat_type": "p2p", "message_type": "text",
              "sender_id": "user_admin", "sender_type": "user", "content": "最近调用"}
    why = {"message_id": "callmid3", "chat_id": "chat_owner", "chat_type": "p2p", "message_type": "text",
           "sender_id": "user_admin", "sender_type": "user", "content": "这条怎么答的", "parent_id": "callmid1"}
    stranger = {"message_id": "callmid4", "chat_id": "chat_other", "chat_type": "p2p", "message_type": "text",
                "sender_id": "user_other", "sender_type": "user", "content": "最近调用"}
    leaked = {"message_id": "callmid5", "chat_id": "chat_group1", "chat_type": "group", "message_type": "text",
              "sender_id": "user_sender", "sender_type": "user", "content": "@小助手 最近调用",
              "mentions": [{"id": "ou_placeholder", "key": "@_user_1", "name": "小助手"}]}
    events = os.path.join(run, "events.jsonl")
    with open(events, "w", encoding="utf-8") as handle:
        for item in (ask, recent, why, stranger, leaked):
            handle.write(json.dumps({"event": item}, ensure_ascii=False) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    body = ""
    call_path = os.path.join(run, "logs", "calls.jsonl")
    if os.path.isfile(call_path):
        body = open(call_path, encoding="utf-8").read()
    rows = [json.loads(line) for line in body.splitlines() if line.strip()] if body else []
    check("真实回答会写调用链，旧配置不必先加键，原文和编号不进文件",
          proc.returncode == 0 and rows and rows[0].get("skills") == ["post-mortem"]
          and any(s.get("name") == "skill_read" for s in rows[0].get("steps") or [])
          and NAME not in body and PHONE not in body and "callmid1" not in body and "ou_placeholder" not in body
          and "某客户" in body, out[-800:] + "\n" + body[-800:])

    def reply_of(mid):
        key = "[dry-run] reply %s: " % mid
        i = out.find(key)
        if i < 0:
            return ""
        lines = []
        for n, line in enumerate(out[i + len(key):].splitlines()):
            if n and (line.startswith("Q ") or line.startswith("[dry-run]") or line.startswith("chat gate")
                      or line.startswith("agent ") or line.startswith("dry-run finished") or " INFO " in line[:40]):
                break
            lines.append(line)
        return "\n".join(lines)

    owner_reply = reply_of("callmid2")
    why_reply = reply_of("callmid3")
    stranger_reply = reply_of("callmid4")
    group_reply = reply_of("callmid5")
    check("主人能在私聊里查看，群里和别人看不到明细",
          "某客户" in owner_reply and "post-mortem" in owner_reply and NAME not in owner_reply
          and "skill_read" in why_reply and NAME not in why_reply
          and "只有主人" in stranger_reply and "某客户" not in stranger_reply
          and "群里不贴明细" in group_reply and "某客户" not in group_reply and NAME not in group_reply,
          "\n".join([owner_reply, why_reply, stranger_reply, group_reply, out[-600:]]))
    off = json.loads(json.dumps(src))
    off["base_dir"] = os.path.join(run, "off")
    off["observability"] = {"journal": True, "calls": {"enabled": False, "retain_days": 14}}
    off_path = os.path.join(run, "off.json")
    json.dump(off, open(off_path, "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(off["base_dir"], exist_ok=True)
    open(os.path.join(off["base_dir"], "redact.txt"), "w", encoding="utf-8").write(NAME + "\n")
    one = os.path.join(run, "one.jsonl")
    open(one, "w", encoding="utf-8").write(json.dumps({"event": dict(ask, message_id="callmid9")}, ensure_ascii=False) + "\n")
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), off_path, "--dry-run", one],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    off_file = os.path.join(off["base_dir"], "logs", "calls.jsonl")
    journal = os.path.join(off["base_dir"], "logs", "journal.jsonl")
    check("calls.enabled 为 false 时不写调用链，原来的 journal 还在",
          proc.returncode == 0 and not os.path.isfile(off_file) and os.path.isfile(journal),
          (proc.stdout or "") + (proc.stderr or ""))
finally:
    shutil.rmtree(run, ignore_errors=True)

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
