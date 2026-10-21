# 复盘写回。不连飞书。客户名只出现在输入里，提案和事实里都要变成「某客户」。
import json, os, shutil, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import feature_switch, memory, memory_growth, retrospect

results = []
NAME = "杭州某某科技有限公司"
PHONE = "13812345678"


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


def cfg_on(deliver="p2p"):
    return {
        "memory": {
            "enabled": True,
            "max_fact_chars": 300,
            "growth": {
                "enabled": False,
                "approver_open_ids": ["user_approver"],
                "deliver": deliver,
                "confirm_ttl_sec": 3600,
            },
            "retrospect": {"enabled": True},
        },
        "write": {"allowed_users": {"user_approver": "审批人"}},
    }


def row_for(result, tools=None, skills=None, ok=True, receive="请处理"):
    steps = [{"kind": "receive", "text": receive}]
    for name in skills or []:
        steps.append({"kind": "skill", "name": name})
    for name, good in tools or []:
        steps.append({"kind": "tool", "name": name, "ok": good, "result": result})
    return {
        "ok": ok, "epoch": time.time(), "chat": "group", "skills": list(skills or []),
        "steps": steps, "result": result,
    }


check("旧配置没写复盘时关着，只写一项也不跑",
      not retrospect.enabled({})
      and not retrospect.enabled({"memory": {"enabled": True}})
      and not retrospect.enabled({"memory": {"enabled": False, "retrospect": {"enabled": True}}})
      and not retrospect.enabled({"memory": {"retrospect": {"enabled": False}}})
      and retrospect.enabled({"memory": {"enabled": True, "retrospect": {"enabled": True}}}))

base = tempfile.mkdtemp(prefix="agentbot_retro_")
try:
    cfg = cfg_on()
    chat_a, chat_b = "chat_alpha", "chat_beta"
    names = [NAME]
    idle = row_for("你好呀")
    check("闲聊不提议", retrospect.consider(cfg, base, chat_a, "group", idle, names) == [])
    check("私聊不写群记忆",
          retrospect.consider(cfg, base, chat_a, "p2p", row_for("做完了", skills=["post-mortem"]), names) == [])
    bare = dict(cfg)
    bare["memory"] = dict(cfg["memory"])
    bare["memory"]["growth"] = dict(cfg["memory"]["growth"])
    bare["memory"]["growth"]["approver_open_ids"] = []
    check("没指定审批人时不提议",
          retrospect.consider(bare, base, chat_a, "group", row_for("做完了"), names) == [])

    raw_result = "周五前交给 %s %s app_secret=abc /home/ubuntu/secret" % (NAME, PHONE)
    issued = retrospect.consider(
        cfg, base, chat_a, "group",
        row_for(raw_result, tools=[("skill_read", True), ("web_search", False)], skills=["post-mortem"]),
        names)
    book = memory_growth._load_book(base)
    payload = {}
    code = ""
    if issued:
        code = issued[0]["code"]
        payload = book["codes"][code]["payload"]
    content = payload.get("content") or ""
    check("用过工具才提议，客户名和密钥不进提案",
          len(issued) == 1 and payload.get("op") == "add" and payload.get("source", {}).get("kind") == "retrospect"
          and "做了什么" in content and "post-mortem" in content and "skill_read" in content
          and "哪里出错" in content and "web_search" in content and "下次怎么做" in content
          and NAME not in content and PHONE not in content and "app_secret=abc" not in content
          and "/home/ubuntu" not in content and "某客户" in content
          and book["codes"][code]["chat_id"] == chat_a, content)
    gmem = memory.GroupMemory(cfg, base, names)
    check("点头之前不写事实", gmem.facts(chat_a) == [] and gmem.facts(chat_b) == [])
    again = retrospect.consider(cfg, base, chat_a, "group", row_for("又做完了", tools=[("skill_read", True)]), names)
    check("同一个群有未处理的复盘时不叠第二条", again == [] and len(book["codes"]) == len(memory_growth._load_book(base)["codes"]))

    other = {"user": "user_approver", "chat_type": "group", "chat_id": chat_b}
    blocked = memory_growth.handle_text(cfg, base, gmem, "确认记忆 " + code, other, names)
    check("别的群确认不会写入",
          blocked and "没有写入" in blocked and gmem.facts(chat_a) == [] and gmem.facts(chat_b) == [], blocked)

    home = {"user": "user_approver", "chat_type": "p2p", "chat_id": "chat_owner"}
    approved = memory_growth.handle_text(cfg, base, gmem, "确认记忆 " + code, home, names)
    facts_a = gmem.facts(chat_a)
    body = "\n".join(json.dumps(item, ensure_ascii=False) for item in facts_a)
    check("审批人点头后只写入这个群",
          approved and "已记" in approved and len(facts_a) == 1 and facts_a[0].get("how") == "growth"
          and "某客户" in body and NAME not in body and PHONE not in body and gmem.facts(chat_b) == [],
          approved or "")

    issued_b = retrospect.consider(
        cfg, base, chat_b, "group", row_for("完成了", tools=[("history", False)]), names)
    code_b = issued_b[0]["code"] if issued_b else ""
    rejected = memory_growth.handle_text(cfg, base, gmem, "驳回记忆 " + code_b, home, names)
    check("驳回不写事实",
          rejected and "已驳回" in rejected and gmem.facts(chat_b) == [], rejected or "")

    group_cfg = cfg_on("group")
    group_base = os.path.join(base, "group")
    os.makedirs(group_base, exist_ok=True)
    issued_g = retrospect.consider(
        group_cfg, group_base, chat_a, "group", row_for("结束了", tools=[("kb_search", True)]), names)
    code_g = issued_g[0]["code"] if issued_g else ""
    gmem_g = memory.GroupMemory(group_cfg, group_base, names)
    wrong_place = memory_growth.handle_text(
        group_cfg, group_base, gmem_g, "确认记忆 " + code_g,
        {"user": "user_approver", "chat_type": "group", "chat_id": chat_b}, names)
    check("发到群里的复盘，换一个群点头也不写",
          wrong_place and "没有写入" in wrong_place and gmem_g.facts(chat_a) == [], wrong_place or "")

    turned = feature_switch.mutate(
        {"memory": {"enabled": False, "dir": "memory", "growth": {"enabled": False}}}, "retrospect", True)
    check("从设置打开时会一并打开长期记忆，其它键不动",
          turned["memory"]["enabled"] is True and turned["memory"]["retrospect"]["enabled"] is True
          and turned["memory"]["dir"] == "memory" and turned["memory"]["growth"]["enabled"] is False
          and feature_switch.is_on(turned, "retrospect")
          and not feature_switch.is_on({}, "retrospect"))
    off = feature_switch.mutate(turned, "retrospect", False)
    check("关掉复盘不关掉已经开着的长期记忆",
          off["memory"]["retrospect"]["enabled"] is False and off["memory"]["enabled"] is True)

    switch_base = os.path.join(base, "switch")
    os.makedirs(switch_base, exist_ok=True)
    owner = {"user": "user_approver", "chat_id": "chat_owner", "chat_type": "p2p", "message_id": "om_retrospect1", "scope": ""}
    stranger = dict(owner, user="user_other", message_id="om_retrospect2")
    group = dict(owner, chat_type="group", chat_id=chat_a, message_id="om_retrospect3")
    switch_cfg = {
        "report": {"owner_open_ids": []},
        "alerts": {"owner_open_ids": []},
        "write": {"allowed_users": {"user_approver": "审批人"}},
        "memory": {"enabled": False},
    }
    proposed = feature_switch.handle_text(switch_cfg, switch_base, "第7项", owner)
    refused = feature_switch.handle_text(switch_cfg, switch_base, "第7项", stranger)
    in_group = feature_switch.handle_text(switch_cfg, switch_base, "打开复盘", group)
    check("只有主人能在私聊里改复盘开关",
          proposed and "任务复盘" in proposed["text"] and "打开" in proposed["text"]
          and refused and "只有主人" in refused["text"]
          and in_group and "私聊" in in_group["text"] and "复盘" not in in_group["text"].split("设置")[0],
          "\n".join([proposed["text"] if proposed else "", refused["text"] if refused else "", in_group["text"] if in_group else ""]))
finally:
    shutil.rmtree(base, ignore_errors=True)

run = tempfile.mkdtemp(prefix="agentbot_retro_run_")
try:
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = run
    src["model_provider"] = "fake"
    src["progress"] = {"enabled": False}
    src["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    src["bot_open_id"] = "ou_placeholder"
    src["bot_names"] = ["小助手"]
    src["chat_ids"] = ["chat_group1"]
    src["p2p"] = {"allow_open_ids": ["user_approver"], "open_to_all": True}
    src["write"]["allowed_users"] = {"user_approver": "审批人"}
    src["skills"] = {"enabled": True, "dir": os.path.join(FW, "skills"), "preload": False, "only": ["post-mortem"]}
    src["memory"]["enabled"] = True
    src["memory"]["growth"]["approver_open_ids"] = ["user_approver"]
    src["memory"]["growth"]["deliver"] = "p2p"
    src["memory"]["retrospect"] = {"enabled": True}
    path = os.path.join(run, "bot.json")
    json.dump(src, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(run, "redact.txt"), "w", encoding="utf-8").write(NAME + "\n")
    ask = {
        "message_id": "retmid1", "chat_id": "chat_group1", "chat_type": "group", "message_type": "text",
        "sender_id": "user_sender", "sender_type": "user",
        "content": '@小助手 <<FAKE_TOOL>>{"tool":"skill_read","name":"post-mortem"}<<END>> %s 什么时候交' % NAME,
        "mentions": [{"id": "ou_placeholder", "key": "@_user_1", "name": "小助手"}],
    }
    events = os.path.join(run, "events.jsonl")
    open(events, "w", encoding="utf-8").write(json.dumps({"event": ask}, ensure_ascii=False) + "\n")
    sink = os.path.join(run, "sent.jsonl")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_SEND"] = sink
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    book_path = os.path.join(run, "memory", "_growth", "approvals.json")
    book = {}
    if os.path.isfile(book_path):
        book = json.load(open(book_path, encoding="utf-8"))
    recs = [item for item in (book.get("codes") or {}).values() if (item.get("payload") or {}).get("source", {}).get("kind") == "retrospect"]
    content = (recs[0].get("payload") or {}).get("content") if recs else ""
    facts = os.path.join(run, "memory", "groups", "chat_group1", "facts.jsonl")
    calls = ""
    call_path = os.path.join(run, "logs", "calls.jsonl")
    if os.path.isfile(call_path):
        calls = open(call_path, encoding="utf-8").read()
    sent = open(sink, encoding="utf-8").read() if os.path.isfile(sink) else ""
    check("答完一轮会提议复盘，点头前不写事实，群号不进调用记录",
          proc.returncode == 0 and len(recs) == 1 and recs[0].get("chat_id") == "chat_group1"
          and "做了什么" in content and "某客户" in content and NAME not in content
          and not os.path.isfile(facts) and "chat_group1" not in calls
          and "确认记忆" in sent and NAME not in sent,
          out[-800:] + "\n" + content + "\n" + sent[-400:])
finally:
    shutil.rmtree(run, ignore_errors=True)

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
