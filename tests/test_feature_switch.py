# 私聊开关：权限、确认码、备份回滚、重启只留一个进程、失败提示。不连飞书。
import json, os, shutil, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import feature_switch

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


_JARGON = ("json", "JSON", ".enabled", "app_secret", "api_key", "open_id", "owner_open_ids", "open_to_all", "allow_open_ids")


def plain(text):
    text = text or ""
    return not any(word in text for word in _JARGON)


CFG = {
    "model_provider": "cursor",
    "p2p": {"open_to_all": False, "allow_open_ids": ["ou_owner1"], "allow_group_members": False},
    "report": {"enabled": False, "owner_open_ids": ["ou_owner1"]},
    "alerts": {"enabled": False, "owner_open_ids": ["ou_owner1"]},
    "write": {"allowed_users": {"user_admin": "管理员"}, "confirm_ttl_sec": 300},
    "knowledge": {"enabled": False},
    "memory": {"enabled": False, "growth": {"enabled": False, "approver_open_ids": []}},
    "digest": {"enabled": False, "chat_ids": []},
    "skills": {"enabled": False},
    "agent": {"model": "grok-4.7-high-fast", "api_key_env": "DEEPSEEK_API_KEY"},
    "model": {
        "provider": "openai_compatible",
        "base_url": "https://example.com/v1",
        "model": "demo-model",
        "api_key_env": "UNIT_MODEL_KEY",
    },
    "kb_dir": "kb",
}
OWNER = {"user": "ou_owner1", "chat_id": "oc_p2p1", "chat_type": "p2p", "message_id": "om_sw1", "scope": ""}
ADMIN = {"user": "user_admin", "chat_id": "oc_p2p1", "chat_type": "p2p", "message_id": "om_sw2", "scope": ""}
OTHER = {"user": "user_other", "chat_id": "oc_p2p1", "chat_type": "p2p", "message_id": "om_sw3", "scope": ""}
GROUP = {"user": "user_other", "chat_id": "oc_group1", "chat_type": "group", "message_id": "om_sw4", "scope": ""}


def workdir():
    base = tempfile.mkdtemp(prefix="switch_")
    os.makedirs(os.path.join(base, "kb"), exist_ok=True)
    path = os.path.join(base, "bot.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(CFG, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return base, path


check("主人含周报、提醒和管理员，旁人不算",
      feature_switch.is_manager(CFG, "ou_owner1") and feature_switch.is_manager(CFG, "user_admin")
      and not feature_switch.is_manager(CFG, "user_other") and not feature_switch.is_manager(CFG, ""))

base, path = workdir()
try:
    os.environ.pop("AGENTBOT_CARD_CALLBACK", None)
    menu = feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    body = menu["text"]
    check("回调没连上时只回精简序号，不先发长文，也不带卡片",
          menu and menu.get("card") is None and "已开" in body and "回复" in body
          and "先看这一项" not in body and "一次只改一项" not in body
          and all(item["title"] in body and item["blurb"] in body for item in feature_switch.FEATURES)
          and all(len(item["blurb"]) <= 12 for item in feature_switch.FEATURES)
          and plain(body) and "report.enabled" not in body and "第 1 项" not in body, body)
    detail = feature_switch.handle_text(CFG, base, "详细", OWNER, cfg_path=path)
    check("详细里才出现开关名字", "report.enabled" in detail["text"] and "bot.json" in detail["text"])
    group = feature_switch.handle_text(CFG, base, "怎么开知识库", GROUP)
    check("群里只让去找主人，不讲配置",
          group and "主人" in group["text"] and "私聊" in group["text"] and "设置" in group["text"]
          and plain(group["text"]) and group.get("card") is None and "现在是" not in group["text"], group["text"])
    stranger = feature_switch.handle_text(CFG, base, "/设置", OTHER)
    check("私聊里的旁人也不能改",
          stranger and "只有主人" in stranger["text"] and plain(stranger["text"]) and stranger.get("card") is None, stranger["text"])
    owner_group = feature_switch.handle_text(CFG, base, "打开用量周报", dict(OWNER, chat_type="group", chat_id="oc_group1"))
    check("主人在群里也要回到私聊", owner_group and "私聊" in owner_group["text"] and plain(owner_group["text"]))
    fresh, _fresh_path = workdir()
    bare = feature_switch.handle_text(CFG, fresh, "1", OWNER)
    shutil.rmtree(fresh, ignore_errors=True)
    check("没看过清单时，单个数字不当成开关", bare is None)
    proposed = feature_switch.handle_text(CFG, base, "1", OWNER)
    code = ""
    for line in (proposed["text"] if proposed else "").splitlines():
        if line.startswith("确认 "):
            code = line.split()[-1]
    check("看过清单后回复序号，先给摘要和确认码",
          proposed and code and len(code) == 6 and code.isdigit() and "确认 " + code in proposed["text"]
          and "取消 " + code in proposed["text"] and plain(proposed["text"].replace(code, ""))
          and ("私聊" in proposed["text"] or "不会主动发消息" in proposed["text"]),
          proposed["text"] if proposed else "")
    log_path = os.path.join(base, "logs", "feature_switch.jsonl")
    log_text = open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
    check("提议写了日志，但没有编号和确认码",
          "propose" in log_text and "ou_owner1" not in log_text and code not in log_text, log_text)
    wrong_user = feature_switch.try_confirm(CFG, base, path, code, OTHER, apply=True)
    still = json.load(open(path, encoding="utf-8"))
    check("别人不能确认，配置没变，码还在",
          wrong_user and "只有你本人" in wrong_user["text"] and still["report"]["enabled"] is False
          and not wrong_user.get("restart"))
    wrong_chat = feature_switch.try_confirm(CFG, base, path, code, dict(OWNER, chat_id="oc_other"), apply=True)
    check("别的会话不能确认", wrong_chat and "只有你本人" in wrong_chat["text"])
    wrong_scope = feature_switch.try_confirm(CFG, base, path, code, dict(OWNER, scope="topicA"), apply=True)
    check("别的话题不能确认", wrong_scope and "只有你本人" in wrong_scope["text"])
    empty = feature_switch.try_confirm(CFG, base, path, code, dict(OWNER, user="", message_id=""), apply=True)
    check("没有发送人不能确认", empty and "不能确认" in empty["text"] and not empty.get("restart"))
    missing = feature_switch.try_confirm(CFG, base, path, "000000", OWNER, apply=True)
    check("不是我们发的码交给原来的确认", missing is None)
    bad_check = lambda _p: ([("report.enabled", "要是 true 或 false")], [])
    failed = feature_switch.try_confirm(CFG, base, path, code, OWNER, apply=True, check=bad_check)
    rolled = open(path, "rb").read()
    original = open(os.path.join(base, "state", "config-backups", "latest.json"), "rb").read()
    after = json.load(open(path, encoding="utf-8"))
    check("检查失败会改回备份，并用大白话说下一步",
          failed and not failed.get("restart") and "没改成" in failed["text"] and "下一步" in failed["text"]
          and plain(failed["text"]) and after["report"]["enabled"] is False and rolled == original
          and after["model_provider"] == "cursor" and after["p2p"]["open_to_all"] is False, failed["text"])
finally:
    shutil.rmtree(base, ignore_errors=True)

base, path = workdir()
try:
    feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    proposed = feature_switch.handle_text(CFG, base, "打开用量周报", OWNER, cfg_path=path)
    code = [line.split()[-1] for line in proposed["text"].splitlines() if line.startswith("确认 ")][0]
    before = open(path, "rb").read()
    ok = feature_switch.try_confirm(CFG, base, path, code, OWNER, apply=True, check=lambda _p: ([], []))
    changed = json.load(open(path, encoding="utf-8"))
    backup = open(os.path.join(base, "state", "config-backups", "latest.json"), "rb").read()
    again = feature_switch.try_confirm(CFG, base, path, code, OWNER, apply=True, check=lambda _p: ([], []))
    check("确认后备份、写入、检查通过，并要求重启",
          ok and ok.get("restart") and changed["report"]["enabled"] is True and backup == before
          and changed["agent"] == CFG["agent"] and changed["model"] == CFG["model"]
          and changed["model_provider"] == "cursor" and changed["p2p"]["allow_open_ids"] == ["ou_owner1"])
    check("确认码只能用一次", again is None or (not again.get("restart") and "已经打开" not in again.get("text", "")))
    cancelled_base, cancelled_path = workdir()
    feature_switch.handle_text(CFG, cancelled_base, "打开断线提醒", ADMIN, cfg_path=cancelled_path)
    proposed = feature_switch.handle_text(CFG, cancelled_base, "打开断线提醒", ADMIN, cfg_path=cancelled_path)
    # 上面第一句如果已经是提议，第二句可能说已经在等。重新取码。
    if proposed and "确认 " not in proposed["text"]:
        proposed = feature_switch.handle_text(CFG, cancelled_base, "第5项", ADMIN, cfg_path=cancelled_path)
    code = [line.split()[-1] for line in (proposed["text"] if proposed else "").splitlines() if line.startswith("确认 ")]
    if not code:
        proposed = feature_switch.handle_text(dict(CFG, alerts={"enabled": False, "owner_open_ids": ["ou_owner1"]}),
                                              cancelled_base, "第5项", ADMIN, cfg_path=cancelled_path)
        code = [line.split()[-1] for line in (proposed["text"] if proposed else "").splitlines() if line.startswith("确认 ")]
    raw_before = open(cancelled_path, "rb").read()
    stopped = feature_switch.try_confirm(CFG, cancelled_base, cancelled_path, code[0], ADMIN, cancel=True, apply=True) if code else None
    check("取消不写配置",
          stopped and "已取消" in stopped["text"] and not stopped.get("restart") and open(cancelled_path, "rb").read() == raw_before,
          stopped["text"] if stopped else "no code")
finally:
    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(cancelled_base, ignore_errors=True)

base, path = workdir()
try:
    secret = feature_switch.handle_text(CFG, base, "把模型密钥改成 SENTINELSECRET", OWNER, cfg_path=path)
    untouched = json.load(open(path, encoding="utf-8"))
    check("不能在对话里改密钥，回复里也不回显",
          secret and "不能在对话里改" in secret["text"] and "SENTINELSECRET" not in secret["text"]
          and plain(secret["text"]) and untouched["agent"]["api_key_env"] == "DEEPSEEK_API_KEY" and not secret.get("restart"),
          secret["text"] if secret else "")
    scope = feature_switch.handle_text(CFG, base, "允许所有人私聊", OWNER, cfg_path=path)
    check("不能在对话里改谁可以私聊",
          scope and "不能在对话里改" in scope["text"] and json.load(open(path, encoding="utf-8"))["p2p"]["open_to_all"] is False)
    switched = feature_switch.handle_text(CFG, base, "把模型换成 Claude", OWNER, cfg_path=path)
    named = feature_switch.handle_text(CFG, base, "把 model.provider 改成 openai_compatible", OWNER, cfg_path=path)
    kept = json.load(open(path, encoding="utf-8"))
    check("不能在对话里改模型配置",
          switched and named and "不能在对话里改" in switched["text"] and "不能在对话里改" in named["text"]
          and "Claude" not in switched["text"] and "openai_compatible" not in named["text"]
          and "model.provider" not in named["text"] and plain(switched["text"]) and plain(named["text"])
          and kept["model"] == CFG["model"] and kept["model_provider"] == "cursor"
          and kept["agent"] == CFG["agent"] and not switched.get("restart") and not named.get("restart"),
          (switched or {}).get("text", "") + "\n" + (named or {}).get("text", ""))
    feature_switch._audit(base, "note", user="ou_owner1", reason="电话 13812345678 以及 app_secret=abc", names=())
    logged = open(os.path.join(base, "logs", "feature_switch.jsonl"), encoding="utf-8").read()
    check("日志脱敏，不留下编号、手机号和密钥",
          "ou_owner1" not in logged and "13812345678" not in logged and "app_secret=abc" not in logged and "note" in logged, logged)
finally:
    shutil.rmtree(base, ignore_errors=True)


class Env:
    def __init__(self, leave=False, fail=""):
        self.pids = [10] if leave or fail == "stuck" else []
        self.starts = 0
        self.stops = 0
        self.restores = 0
        self.leave = leave
        self.fail = fail

    def stop(self):
        self.stops += 1
        if not self.leave:
            self.pids = []

    def wait_until_dead(self):
        return None

    def start(self):
        self.starts += 1
        if self.pids:
            raise RuntimeError("second receiver")
        if self.fail == "raise-once" and self.starts == 1:
            raise RuntimeError("start failed")
        if self.fail == "raise-keep":
            self.pids.append(50)
            raise RuntimeError("started then failed")
        if self.fail == "two":
            self.pids.extend([21, 22])
            return
        self.pids.append(11 + self.starts)

    def our_pids(self):
        return list(self.pids)


def _restore(box):
    box["n"] += 1


box = {"n": 0}
happy = Env()
good = feature_switch.execute_reload(happy, restore=lambda: _restore(box))
check("停干净之后只启动一个接收进程",
      good["ok"] and good["started"] == 1 and happy.starts == 1 and happy.stops == 1 and len(happy.pids) == 1 and box["n"] == 0,
      str(good) + str(happy.pids))
box = {"n": 0}
stuck = Env(leave=True)
blocked = feature_switch.execute_reload(stuck, restore=lambda: _restore(box))
check("原来的还在就不要再启动，并回滚",
      not blocked["ok"] and blocked["reason"] == "still_running" and stuck.starts == 0 and box["n"] == 1 and "没改成" in feature_switch._fail_text("still_running")
      and "下一步" in feature_switch._fail_text("still_running") and plain(feature_switch._fail_text("still_running")))
box = {"n": 0}
kept = Env(fail="raise-keep")
kept.pids = []
once = feature_switch.execute_reload(kept, restore=lambda: _restore(box))
check("启动失败但进程还在时，不再启动第二个",
      not once["ok"] and kept.starts == 1 and len(kept.pids) == 1 and box["n"] == 1, str(once) + str(kept.pids))
box = {"n": 0}
retry = Env(fail="raise-once")
retry.pids = []
old = feature_switch.execute_reload(retry, restore=lambda: _restore(box))
check("第一次没启动成功才再试一次，最后仍是一个进程",
      not old["ok"] and old["reason"] == "restarted_old" and retry.starts == 2 and len(retry.pids) == 1 and box["n"] == 1, str(old))
dup = Env(fail="two")
dup.pids = []
many = feature_switch.execute_reload(dup)
check("如果一下出现两个进程，结果标成不是单个",
      not many["ok"] and many["reason"] == "not_single" and dup.starts == 1)

hello = feature_switch.welcome_text(CFG)
check("新手介绍只讲能做什么和建议先开哪几项",
      "断线提醒" in hello and "用量周报" in hello and "设置" in hello and "早间待办" in hello and plain(hello) and "群" in hello)
sent = []


def _send(kind, target, text, key):
    sent.append((kind, target, text, key))
    return "ok"


base, path = workdir()
try:
    first = feature_switch.maybe_welcome_user(CFG, base, "ou_owner1", "p2p", (), _send)
    second = feature_switch.maybe_welcome_user(CFG, base, "ou_owner1", "p2p", (), _send)
    group_hello = feature_switch.maybe_welcome_user(CFG, base, "ou_owner1", "group", (), _send)
    stranger_hello = feature_switch.maybe_welcome_user(CFG, base, "user_other", "p2p", (), _send)
    pushed = feature_switch.push_welcome(CFG, base, (), _send)
    check("欢迎只在主人的私聊发一次，不在群里发",
          first and not second and not group_hello and not stranger_hello and pushed == []
          and len(sent) == 1 and sent[0][0] == "user" and plain(sent[0][2]))
    feature_switch._write_result(base, "ou_owner1", "用量周报已经打开。我重新启动好了。")
    delivered = []

    def _deliver(kind, target, text, key):
        delivered.append((kind, target, text))
        return "ok"

    check("重启后的结果只私聊主人一次",
          feature_switch.deliver_result(base, _deliver) and delivered and delivered[0][0] == "user"
          and "已经打开" in delivered[0][2] and feature_switch.deliver_result(base, _deliver) is False)
finally:
    shutil.rmtree(base, ignore_errors=True)

base, path = workdir()
try:
    grown = feature_switch.mutate(CFG, "growth", True)
    check("打开记忆成长时会连着打开长期记忆，不碰模型和私聊范围",
          grown["memory"]["enabled"] is True and grown["memory"]["growth"]["enabled"] is True
          and grown["model"] == CFG["model"] and grown["agent"] == CFG["agent"]
          and grown["model_provider"] == "cursor" and grown["p2p"]["open_to_all"] is False)
    try:
        feature_switch.mutate(CFG, "model", True)
        check("白名单以外的项不能写", False)
    except ValueError:
        try:
            feature_switch.mutate(CFG, "p2p", True)
            check("白名单以外的项不能写", False)
        except ValueError:
            check("白名单以外的项不能写", True)
    event = {"header": {"event_type": "card.action.trigger", "event_id": "evt1"},
             "event": {"operator": {"open_id": "ou_owner1"},
                       "action": {"value": {"switch": "alerts", "op": "flip"}},
                       "context": {"open_chat_id": "oc_p2p1", "open_message_id": "om_btn1"}}}
    turned = feature_switch.card_event(event)
    normal = feature_switch.card_event({"message_id": "om_x", "content": "你好"})
    proposed = feature_switch.handle_text(CFG, base, turned["content"], OWNER, cfg_path=path) if turned else None
    check("按钮和回复序号一样，先给确认码；普通消息不是按钮",
          turned and turned["content"] == "第5项" and normal is None and proposed and "确认 " in proposed["text"]
          and "断线提醒" in proposed["text"], proposed["text"] if proposed else "")
finally:
    shutil.rmtree(base, ignore_errors=True)

script = open(os.path.join(FW, "scripts", "reload.ps1"), "rb").read()
text = script.decode("utf-8-sig")
check("重启脚本是 UTF-8 BOM，给 Windows PowerShell 5.1", script.startswith(b"\xef\xbb\xbf"))
stop_call = text.find('Join-Path $PSScriptRoot "stop.ps1"')
start_call = text.find('Join-Path $PSScriptRoot "start.ps1"')
check("重启脚本先停再查再启动，且不另写一套结束进程",
      stop_call >= 0 and start_call > stop_call and "Test-OurAgent" in text
      and "Test-ReceiverLeft" in text and "exit 3" in text and text.find("exit 3") < start_call
      and "Start-Process" not in text and "taskkill" not in text.lower() and "&&" not in text and "??" not in text
      and "Stop-Process" not in text and "Restore-SwitchBackup" in text)
argv = feature_switch.reloader_argv(FW, os.path.join(FW, "agents", "example.json"))
joined = " ".join(argv)
direct = "agentbot.py" in joined
if os.name == "nt":
    check("Windows 的重启命令只走原来的重启脚本，不直接再开一个接收进程",
          argv[:1] == ["powershell.exe"] and "reload.ps1" in joined and not direct,
          argv)
else:
    check("非 Windows 的重启命令只叫本模块，不直接再开一个接收进程",
          "feature_switch.py" in joined and "--reload" in argv and not direct,
          argv)

# 演练整条私聊：群成员、旁人、主人、确认码。不改仓库里的示例配置。
run = tempfile.mkdtemp(prefix="switch_bot_")
try:
    sample = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    sample["base_dir"] = run
    sample["chat_ids"] = ["oc_group1"]
    sample["alerts"] = {"enabled": False, "owner_open_ids": ["ou_owner1"], "unhealthy_after_sec": 180,
                        "repeat_sec": 1800, "local_log": True, "windows_toast": False}
    sample["report"] = {"enabled": False, "owner_open_ids": ["ou_owner1"]}
    sample["p2p"] = {"allow_open_ids": ["ou_owner1", "user_other"], "open_to_all": False, "allow_group_members": False}
    sample["write"]["allowed_users"] = {"user_admin": "管理员"}
    sample["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    cfg_path = os.path.join(run, "bot.json")
    json.dump(sample, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
    bot = sample["bot_names"][0]
    mention = [{"id": sample["bot_open_id"], "key": "@_user_1", "name": bot}]

    def ev(mid, text, sender, chat, ctype):
        item = {"message_id": mid, "chat_id": chat, "chat_type": ctype, "message_type": "text",
                "sender_id": sender, "sender_type": "user", "content": text, "mentions": mention if ctype == "group" else []}
        if ctype == "group":
            item["content"] = "@%s %s" % (bot, text)
        return item

    events = [
        ev("g1", "怎么开知识库", "user_other", "oc_group1", "group"),
        ev("p0", "设置", "user_other", "oc_p2p1", "p2p"),
        ev("p1", "设置", "ou_owner1", "oc_p2p1", "p2p"),
        ev("p2", "1", "ou_owner1", "oc_p2p1", "p2p"),
    ]
    blob = os.path.join(run, "events.jsonl")
    open(blob, "w", encoding="utf-8").write("\n".join(json.dumps({"event": item}, ensure_ascii=False) for item in events) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0.2"
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path, "--dry-run", blob],
                          cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=90)
    out = (proc.stdout or "") + (proc.stderr or "")

    def reply_of(mid):
        needle = "[dry-run] reply %s:" % mid
        if needle not in out:
            return ""
        return out.split(needle, 1)[1].split("[dry-run] reply ", 1)[0]

    g_reply, s_reply, o_reply, n_reply = reply_of("g1"), reply_of("p0"), reply_of("p1"), reply_of("p2")
    code = ""
    for line in n_reply.splitlines():
        if "确认 " in line:
            parts = line.strip().split()
            if parts and parts[-1].isdigit() and len(parts[-1]) == 6:
                code = parts[-1]
    events2 = [ev("p3", "确认 " + code, "user_other", "oc_p2p1", "p2p"),
               ev("p4", "确认 " + code, "ou_owner1", "oc_other", "p2p"),
               ev("p5", "确认 " + code, "ou_owner1", "oc_p2p1", "p2p")]
    blob2 = os.path.join(run, "events2.jsonl")
    open(blob2, "w", encoding="utf-8").write("\n".join(json.dumps({"event": item}, ensure_ascii=False) for item in events2) + "\n")
    proc2 = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path, "--dry-run", blob2],
                           cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=90)
    out2 = (proc2.stdout or "") + (proc2.stderr or "")

    def reply2(mid):
        needle = "[dry-run] reply %s:" % mid
        if needle not in out2:
            return ""
        return out2.split(needle, 1)[1].split("[dry-run] reply ", 1)[0]

    saved = json.load(open(cfg_path, encoding="utf-8"))
    logged = ""
    log_file = os.path.join(run, "logs", "feature_switch.jsonl")
    if os.path.isfile(log_file):
        logged = open(log_file, encoding="utf-8").read()
    check("演练：群成员和旁人看不到配置，主人能看到清单和确认码",
          proc.returncode == 0 and "主人" in g_reply and "私聊" in g_reply and plain(g_reply)
          and "只有主人" in s_reply and plain(s_reply)
          and "已开" in o_reply and "回复" in o_reply and "先看这一项" not in o_reply
          and "[dry-run] card p1:" not in out
          and code and "agent start p1" not in out and "agent start g1" not in out, out[-800:])
    check("演练：别人和别的会话不能确认；本人确认也不改文件、不重启",
          proc2.returncode == 0 and "只有你本人" in reply2("p3") and "只有你本人" in reply2("p4")
          and "演练" in reply2("p5") and "没有改动" in reply2("p5")
          and saved["report"]["enabled"] is False and "reload" not in out2
          and "ou_owner1" not in logged, reply2("p5") + "\n" + logged[-400:])
finally:
    shutil.rmtree(run, ignore_errors=True)

def _walk(node, acc=None):
    acc = [] if acc is None else acc
    if isinstance(node, dict):
        acc.append(node)
        for val in node.values():
            _walk(val, acc)
    elif isinstance(node, list):
        for item in node:
            _walk(item, acc)
    return acc


def _visible(card):
    parts = []
    for el in (card or {}).get("body", {}).get("elements", []):
        if el.get("tag") == "collapsible_panel":
            continue
        parts.append(json.dumps(el, ensure_ascii=False))
    return "\n".join(parts)


def _panel_text(card, title):
    for el in _walk(card):
        if el.get("tag") != "collapsible_panel":
            continue
        header = ((el.get("header") or {}).get("title") or {}).get("content") or ""
        if header == title:
            return json.dumps(el, ensure_ascii=False)
    return ""


def _confirm_value(card, op):
    for el in _walk(card):
        if el.get("tag") != "button":
            continue
        val = ((el.get("behaviors") or [{}])[0].get("value") or {})
        if val.get("kind") == "write_confirm" and val.get("op") == op:
            return val
    return None


def _settings_event(op, switch="", mid="om_set1"):
    value = {"kind": "settings", "op": op}
    if switch:
        value["switch"] = switch
    return {"header": {"event_type": "card.action.trigger", "event_id": "evt_" + mid},
            "event": {"operator": {"open_id": "ou_owner1"},
                      "action": {"value": value},
                      "context": {"open_chat_id": "oc_p2p1", "open_message_id": mid}}}


os.environ["AGENTBOT_CARD_CALLBACK"] = "1"
base, path = workdir()
try:
    menu = feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    shown = menu["card"]
    visible = _visible(shown)
    names = _panel_text(shown, "配置名字")
    tags = [el for el in _walk(shown) if el.get("tag") == "checker"]
    columns = [el for el in _walk(shown) if el.get("tag") == "column_set"]
    containers = [el for el in _walk(shown) if el.get("tag") == "interactive_container"]
    check("卡片回调连上时只准备一张紧凑卡片，文字留着但标记为不另发",
          menu.get("card_only") and shown and shown.get("schema") == "2.0"
          and menu.get("callback", {}).get("card", {}).get("type") == "raw"
          and menu["callback"]["card"]["data"] is shown
          and "已开 0/9" in visible and "v" + feature_switch.product_version() in visible
          and "在线" in visible and all(name in visible for name in ("日常", "记忆", "技能", "高级"))
          and all(item["title"] in visible and item["blurb"] in visible for item in feature_switch.FEATURES)
          and len(tags) == len(feature_switch.FEATURES) and columns and containers
          and "全部推荐设置" in visible and "第 1 项" not in visible and "先看这一项" not in visible
          and plain(visible) and "report.enabled" not in visible
          and "report.enabled" in names and shown["body"]["elements"][-2].get("expanded") is False
          and _confirm_value(shown, "yes") is None, visible[:500])
    stranger = feature_switch.handle_settings(
        CFG, base, {"user": "user_other", "chat_id": "oc_p2p1", "message_id": "om_set1", "op": "stage", "switch": "report"},
        cfg_path=path)
    check("旁人点开关不能改，配置不动",
          stranger and "只有主人" in stranger["text"] and json.load(open(path, encoding="utf-8"))["report"]["enabled"] is False)
    parsed = feature_switch.parse_settings(_settings_event("stage", "report"))
    import confirm_mode
    legacy = {"header": {"event_type": "card.action.trigger", "event_id": "evt_old"},
              "event": {"operator": {"open_id": "ou_owner1"},
                        "action": {"value": {"switch": "alerts", "op": "flip"}},
                        "context": {"open_chat_id": "oc_p2p1", "open_message_id": "om_old"}}}
    turned = feature_switch.card_event(legacy)
    check("设置勾选不是写入确认，旧的第 N 项按钮仍能转成序号",
          parsed and parsed["op"] == "stage" and confirm_mode.parse_click(_settings_event("stage", "report")) is None
          and turned and turned["content"] == "第5项")
    staged = feature_switch.handle_settings(CFG, base, parsed, cfg_path=path)
    staged_card = staged["card"]
    yes = _confirm_value(staged_card, "yes")
    no = _confirm_value(staged_card, "no")
    check("点一下只把原卡片改成确认开启和取消，配置还没写",
          staged.get("patch") and staged.get("quiet") and staged.get("text") == ""
          and staged.get("callback", {}).get("card", {}).get("type") == "raw"
          and yes and no and yes.get("kind") == "write_confirm"
          and "确认开启" in json.dumps(staged_card, ensure_ascii=False)
          and "取消" in json.dumps(staged_card, ensure_ascii=False)
          and "待确认" in json.dumps(staged_card, ensure_ascii=False)
          and json.load(open(path, encoding="utf-8"))["report"]["enabled"] is False)
    tampered = feature_switch.try_click(
        CFG, base, path, yes["id"], "yes", "0" * 64, OWNER, apply=True, check=lambda _p: ([], []), message_id="om_set1")
    check("改掉确认哈希不会写入",
          tampered and "对不上" in tampered["text"] and json.load(open(path, encoding="utf-8"))["report"]["enabled"] is False)
    old_yes = yes
    again = feature_switch.handle_settings(CFG, base, feature_switch.parse_settings(_settings_event("stage", "alerts")), cfg_path=path)
    stale = feature_switch.try_click(
        CFG, base, path, old_yes["id"], "yes", old_yes["hash"], OWNER, apply=True,
        check=lambda _p: ([], []), message_id="om_set1")
    mid = json.load(open(path, encoding="utf-8"))
    check("勾选第二项后，旧确认按钮不能再生效",
          again and stale and stale.get("quiet") and mid["report"]["enabled"] is False and mid["alerts"]["enabled"] is False
          and "待确认" in json.dumps(stale.get("card") or {}, ensure_ascii=False))
    fresh_yes = _confirm_value(again["card"], "yes")
    check("两项一起确认时按钮写成确认这 2 项",
          fresh_yes and "确认这 2 项" in json.dumps(again["card"], ensure_ascii=False))
    bad = feature_switch.try_click(
        CFG, base, path, fresh_yes["id"], "yes", fresh_yes["hash"], OWNER, apply=True,
        check=lambda _p: ([("report.enabled", "不要")], []), message_id="om_set1")
    rolled = json.load(open(path, encoding="utf-8"))
    check("多项确认检查失败会整份改回，两项都没开",
          bad and not bad.get("restart") and "没改成" in bad["text"] and "下一步" in bad["text"]
          and rolled["report"]["enabled"] is False and rolled["alerts"]["enabled"] is False
          and rolled["model_provider"] == "cursor")
    feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    rec = feature_switch.handle_settings(
        CFG, base, feature_switch.parse_settings(_settings_event("recommend", mid="om_rec")), cfg_path=path)
    rec_yes = _confirm_value(rec["card"], "yes")
    rec_no = _confirm_value(rec["card"], "no")
    stopped = feature_switch.try_click(
        CFG, base, path, rec_no["id"], "no", rec_no["hash"], OWNER, apply=True, message_id="om_rec")
    after_cancel = json.load(open(path, encoding="utf-8"))
    check("取消推荐设置不写配置，卡片回到清单",
          rec_yes and stopped and stopped.get("quiet") and stopped.get("text") == ""
          and after_cancel["report"]["enabled"] is False and after_cancel["alerts"]["enabled"] is False
          and "全部推荐设置" in json.dumps(stopped.get("card") or {}, ensure_ascii=False))
    feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    rec = feature_switch.handle_settings(
        CFG, base, feature_switch.parse_settings(_settings_event("recommend", mid="om_rec2")), cfg_path=path)
    rec_yes = _confirm_value(rec["card"], "yes")
    before = open(path, "rb").read()
    applied = feature_switch.try_click(
        CFG, base, path, rec_yes["id"], "yes", rec_yes["hash"], OWNER, apply=True,
        check=lambda _p: ([], []), message_id="om_rec2")
    changed = json.load(open(path, encoding="utf-8"))
    backup = open(os.path.join(base, "state", "config-backups", "latest.json"), "rb").read()
    check("确认推荐设置后备份并打开这两项，其它开关和模型不动",
          applied and applied.get("restart") and changed["alerts"]["enabled"] is True
          and changed["report"]["enabled"] is True and changed["knowledge"]["enabled"] is False
          and changed["digest"]["enabled"] is False and changed["agent"] == CFG["agent"]
          and changed["p2p"]["open_to_all"] is False and backup == before)
    asked = feature_switch.handle_settings(
        changed, base, feature_switch.parse_settings(_settings_event("help", "digest", "om_help")), cfg_path=path)
    help_panel = _panel_text(asked["card"], "说明")
    check("问号展开说明，默认仍把配置名字折起来",
          asked and asked.get("patch") and "早间待办" in help_panel and "主动发到群里" in help_panel
          and _panel_text(asked["card"], "配置名字") and "digest.enabled" in _panel_text(asked["card"], "配置名字")
          and "digest.enabled" not in _visible(asked["card"]))
finally:
    os.environ.pop("AGENTBOT_CARD_CALLBACK", None)
    shutil.rmtree(base, ignore_errors=True)

base, path = workdir()
try:
    os.environ.pop("AGENTBOT_CARD_CALLBACK", None)
    feature_switch.handle_text(CFG, base, "设置", OWNER, cfg_path=path)
    many = feature_switch.handle_text(CFG, base, "1 5", OWNER, cfg_path=path)
    code = [line.split()[-1] for line in many["text"].splitlines() if line.startswith("确认 ")]
    check("文字回退可以一次报两个序号",
          many and code and "用量周报" in many["text"] and "断线提醒" in many["text"] and plain(many["text"].replace(code[0], "")))
    done = feature_switch.try_confirm(CFG, base, path, code[0], OWNER, apply=True, check=lambda _p: ([], []))
    both = json.load(open(path, encoding="utf-8"))
    check("一次确认两个序号会一起写入并要求重启",
          done and done.get("restart") and both["report"]["enabled"] is True and both["alerts"]["enabled"] is True
          and both["knowledge"]["enabled"] is False)
    feature_switch.handle_text(both, base, "设置", OWNER, cfg_path=path)
    zero = feature_switch.handle_text(both, base, "0", OWNER, cfg_path=path)
    check("推荐项已经开着时，回复 0 不再提议",
          zero and "已经开着" in zero["text"] and not zero.get("restart"))
    evolve_cfg = json.load(open(path, encoding="utf-8"))
    feature_switch.handle_text(evolve_cfg, base, "设置", OWNER, cfg_path=path)
    proposed = feature_switch.handle_text(evolve_cfg, base, "第8项", OWNER, cfg_path=path)
    code = [line.split()[-1] for line in proposed["text"].splitlines() if line.startswith("确认 ")][0]
    opened = feature_switch.try_confirm(evolve_cfg, base, path, code, OWNER, apply=True, check=lambda _p: ([], []))
    evolved = json.load(open(path, encoding="utf-8"))
    check("确认技能改进会打开技能，不会当成替换技能文件",
          opened and opened.get("restart") and evolved["skills"]["enabled"] is True
          and evolved["skills"]["evolve"]["enabled"] is True and "技能文件" not in opened["text"])
finally:
    shutil.rmtree(base, ignore_errors=True)


demo = os.path.join(FW, "docs", "demo")
listing = json.load(open(os.path.join(demo, "switch-card.example.json"), encoding="utf-8"))
confirming = json.load(open(os.path.join(demo, "switch-card-confirm.example.json"), encoding="utf-8"))
sample = json.dumps({"listing": listing, "confirming": confirming}, ensure_ascii=False)
shown = _visible(listing)
keys = _panel_text(listing, "配置名字")
check("仓库里的示例卡片和确认态都是这张紧凑卡，没有真实身份",
      listing.get("schema") == "2.0" and confirming.get("schema") == "2.0"
      and listing.get("config", {}).get("update_multi") is True
      and "已开 6/9" in shown and "全部推荐设置" in shown
      and all(name in shown for name in ("日常", "记忆", "技能", "高级"))
      and len([el for el in _walk(listing) if el.get("tag") == "checker"]) == 9
      and _confirm_value(listing, "yes") is None
      and "report.enabled" not in shown and "report.enabled" in keys
      and "第 1 项" not in shown
      and "确认这 2 项" in json.dumps(confirming, ensure_ascii=False)
      and "早间待办会发到群里" in json.dumps(confirming, ensure_ascii=False)
      and "example-token-not-real" in sample and "ou_" not in sample and "secret" not in sample.lower())


if any(not ok for _name, ok in results):
    print("%d failed" % sum(1 for _n, ok in results if not ok))
    sys.exit(1)
print("feature switch ok (%d)" % len(results))
