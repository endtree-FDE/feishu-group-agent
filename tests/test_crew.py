# 主理人加专员。不连飞书。写入工具必须被拒绝，客户名不能留在汇总里。
import json, os, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import crew, feature_switch

results = []
NAME = "杭州某某科技有限公司"


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


check("旧配置没有主理人时关着，短句不拆",
      not crew.enabled({})
      and not crew.enabled({"agent": {"crew": {"enabled": False}}})
      and crew.enabled({"agent": {"crew": {"enabled": True}}})
      and not crew.complex("你好")
      and crew.complex("请查资料并且写一段说明")
      and crew.complex("？" * 0 + "甲？" * 0 + "第一件？第二件？")
      and crew.settings({})["total_rounds"] == 16
      and crew.settings({"agent": {"crew": {"enabled": True, "total_rounds": 0}}})["total_rounds"] == 16
      and crew.settings({"agent": {"crew": {"enabled": True, "total_sec": 99999}}})["total_sec"] == 3600)

check("拆分只含只读角色，并且总有起草",
      crew.plan("请查最新资料") == ["查资料", "起草"]
      and crew.plan("请读附件里的文档") == ["读文档", "起草"]
      and crew.plan("今天天气怎么样，要比较长的一句说明才算复杂问题吧")[-1] == "起草")

calls = []
tools = []


def complete(prompt):
    calls.append(prompt)
    head = prompt.split("已有材料", 1)[0]
    if prompt.startswith("你是主理人"):
        return "综合：已根据材料写完，交给 " + NAME
    if "专员「查资料」" in head and "工具结果" not in prompt:
        return '先查。\n<<TOOL>>{"tool":"web_search","query":"进度"}\n<<TOOL>>{"tool":"docs_create","title":"不要写"}'
    if "专员「读文档」" in head and "工具结果" not in prompt:
        return '<<TOOL>>{"tool":"docs_read","doc":"d1"}'
    return "专员说完了"


def run_tool(name, args, ctx):
    tools.append(name)
    if name in crew._WRITE:
        raise AssertionError("write tool ran")
    return "读到 " + NAME + " 的材料", False


cfg = {"agent": {"crew": {"enabled": True, "step_rounds": 2, "total_rounds": 8}}}
text = crew.run(cfg, "请查资料并且读一下文档，再写一段说明", {}, complete, run_tool, names=[NAME])
check("专员只能读，写入不会执行，汇总里客户名被打掉",
      tools == ["web_search", "docs_read"] and "docs_create" not in tools
      and "不能做这件事" in "\n".join(calls) and NAME not in text and "某客户" in text and "综合" in text,
      text + "\n" + ",".join(tools))

limited = []


def boom(_prompt):
    limited.append(1)
    return "不应被主理人再叫一次"


def clock():
    clock.n += 1
    return 0 if clock.n < 3 else 10 ** 9


clock.n = 0
tight = {"agent": {"crew": {"enabled": True, "total_sec": 10, "step_sec": 10, "step_rounds": 1, "total_rounds": 8}}}
early = crew.run(tight, "请查资料并且写一段说明", {}, boom, run_tool, clock=clock, names=[NAME])
check("超过时间预算就停，不再继续拆",
      "预算用完" in early and len(limited) <= 1, early + " rounds " + str(len(limited)))

one = {"agent": {"crew": {"enabled": True, "total_rounds": 1, "step_rounds": 1}}}
stopped = crew.run(one, "请查资料并且写一段说明", {}, complete, run_tool, names=[NAME])
check("总轮数用完就用已有材料收束，不再汇总调用",
      "预算用完" in stopped and "总轮数" in stopped, stopped[:400])

base = tempfile.mkdtemp(prefix="crew_cfg_")
try:
    data = feature_switch.mutate({"features": {"tools": False}, "agent": {"model": "x"}}, "crew", True)
    check("从设置打开主理人时会一并打开工具，模型名不动",
          data["agent"]["crew"]["enabled"] is True and data["features"]["tools"] is True and data["agent"]["model"] == "x"
          and feature_switch.is_on(data, "crew"))
    off = feature_switch.mutate(data, "crew", False)
    check("关掉主理人不关掉工具",
          off["agent"]["crew"]["enabled"] is False and off["features"]["tools"] is True)
    owner = {"user": "user_owner", "chat_id": "chat_owner", "chat_type": "p2p", "message_id": "om_crew1", "scope": ""}
    proposed = feature_switch.handle_text(
        {"write": {"allowed_users": {"user_owner": "主人"}}, "agent": {}}, base, "第9项", owner)
    stranger = feature_switch.handle_text(
        {"write": {"allowed_users": {"user_owner": "主人"}}}, base, "打开主理人",
        dict(owner, user="user_other", message_id="om_crew2"))
    group = feature_switch.handle_text(
        {"write": {"allowed_users": {"user_owner": "主人"}}}, base, "第9项",
        dict(owner, chat_type="group", chat_id="chat_alpha", message_id="om_crew3"))
    check("只有主人能在私聊里改主理人开关",
          proposed and "主理人" in proposed["text"] and stranger and "只有主人" in stranger["text"]
          and group and "私聊" in group["text"],
          "\n".join([proposed["text"] if proposed else "", stranger["text"] if stranger else "", group["text"] if group else ""]))
finally:
    import shutil
    shutil.rmtree(base, ignore_errors=True)

run = tempfile.mkdtemp(prefix="agentbot_crew_run_")
try:
    import shutil, subprocess
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = run
    src["model_provider"] = "fake"
    src["progress"] = {"enabled": False}
    src["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    src["bot_open_id"] = "ou_placeholder"
    src["bot_names"] = ["小助手"]
    src["chat_ids"] = ["chat_group1"]
    src["p2p"] = {"allow_open_ids": ["user_owner"], "open_to_all": True}
    src["agent"]["crew"]["enabled"] = True
    src["agent"]["crew"]["step_rounds"] = 1
    path = os.path.join(run, "bot.json")
    json.dump(src, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(run, "redact.txt"), "w", encoding="utf-8").write(NAME + "\n")
    ask = {
        "message_id": "crewmid1", "chat_id": "chat_group1", "chat_type": "group", "message_type": "text",
        "sender_id": "user_sender", "sender_type": "user",
        "content": "@小助手 请查资料并且写一段说明 " + NAME,
        "mentions": [{"id": "ou_placeholder", "key": "@_user_1", "name": "小助手"}],
    }
    events = os.path.join(run, "events.jsonl")
    open(events, "w", encoding="utf-8").write(json.dumps({"event": ask}, ensure_ascii=False) + "\n")
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
    models = 0
    if rows:
        models = sum(1 for step in rows[0].get("steps") or [] if step.get("kind") == "model")
    check("复杂问题会拆开答，不执行写入，客户名不进调用记录",
          proc.returncode == 0 and models >= 3 and "docs +create" not in out and "base +create" not in out
          and NAME not in body and "某客户" in out,
          out[-800:] + "\nmodels " + str(models))
finally:
    import shutil
    shutil.rmtree(run, ignore_errors=True)

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
