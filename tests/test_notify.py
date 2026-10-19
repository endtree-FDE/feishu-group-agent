# 断线提醒和早晨摘要。默认关。发送走消息接口，不走事件长连接。
import datetime, json, os, shutil, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import digest, doctor, feishu_link, health_alert, outbound

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


EXAMPLE = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
check("例子里两项都关着",
      EXAMPLE.get("alerts", {}).get("enabled") is False and EXAMPLE.get("digest", {}).get("enabled") is False)
check("没写 alerts 或 enabled 不是 true 时不发",
      health_alert.settings({})["enabled"] is False
      and health_alert.settings({"alerts": {"enabled": False, "owner_open_ids": ["ou_owner1"]}})["enabled"] is False
      and health_alert.settings(EXAMPLE)["enabled"] is False)
check("0 或写错的阈值回到 180 秒和 1800 秒",
      health_alert.settings({"alerts": {"enabled": True, "unhealthy_after_sec": 0, "repeat_sec": -5}})["after"] == 180
      and health_alert.settings({"alerts": {"enabled": True, "unhealthy_after_sec": "x", "repeat_sec": "y"}})["repeat"] == 1800)
check("占位符和空编号不会变成收件人",
      health_alert.settings({"alerts": {"enabled": True, "owner_open_ids": ["REPLACE_OWNER", "ou_owner1", "ou_owner1", ""]}})["owners"] == ["ou_owner1"])
check("没写 digest 时不启用，非法时间不发",
      digest.enabled({}) is False and digest.enabled(EXAMPLE) is False
      and digest.prompt_note({}) == "" and digest.prompt_note(EXAMPLE) == ""
      and digest.settings({"digest": {"enabled": True, "time": "25:99"}})["time_ok"] is False
      and digest.settings({"digest": {"enabled": True}})["time_ok"] is True
      and digest.settings({"digest": {"window_min": -1}})["window"] == 30)

state = {}
state, action = health_alert.step(state, 1000, False, 180, 1800, 1000, "link")
check("没满 180 秒不发", action is None and state["phase"] == "pending")
state, action = health_alert.step(state, 1179, False, 180, 1800, 1000, "link")
check("179 秒仍不发", action is None and state["since"] == 1000)
state, action = health_alert.step(state, 1180, False, 180, 1800, 1000, "link")
check("满 180 秒发一条中断", action == "down" and state["phase"] == "alerted" and state["since"] == 1000)
held = dict(state)
state, action = health_alert.step(state, 1300, False, 180, 1800, 1000, "link")
check("还没到提醒间隔不发第二条", action is None and state["phase"] == "alerted")
state, action = health_alert.step(state, 1180 + 1800, False, 180, 1800, 1000, "link")
check("间隔到了只发提醒", action == "remind" and state["since"] == 1000)
state, action = health_alert.step(state, 4000, True, 180, 1800, None, "link")
check("恢复只在已经告警过之后发一次", action == "recover" and state["since"] == 1000)
recovered = health_alert.render("recover", "小助手", state["since"], state["last_alert"], "link")
check("恢复文案用的是整段中断，不是最后一分钟", "大约 50 分钟" in recovered and "已经恢复" in recovered, recovered)
state, action = health_alert.step(state, 4010, True, 180, 1800, None, "link")
check("已经恢复后再健康不再发", action is None)
blip, action = health_alert.step({"phase": "pending", "since": 1000, "last_alert": 0, "cause": "link"}, 1100, True, 180, 1800)
check("没超过阈值的闪断不发恢复", action is None and blip["phase"] == "ok")
again, action = health_alert.step(held, 1300, False, 180, 1800, 1000, "link")
check("重启后仍处于中断且刚发过，不会再发一条中断", action is None)
back, action = health_alert.step(held, 2000, True, 180, 1800, None, "link")
check("重启后发现已经恢复，补一条恢复", action == "recover")
down_text = health_alert.render("down", "小助手", 1000, 1180, "link")
hb_text = health_alert.render("down", "小助手", 1000, 1180, "heartbeat")
check("长连接和心跳用不同的说法，正文不带编号",
      "长连接已中断" in down_text and "发送接口" in down_text
      and "进程没有心跳" in hb_text and "ou_" not in down_text and "oc_" not in hb_text)

work = tempfile.mkdtemp(prefix="agentbot_alert_")
try:
    calls = []

    def sender(kind, target, text, key):
        calls.append((kind, target, text, key))
        return "ok"

    alert_cfg = {"alerts": {"enabled": True, "owner_open_ids": ["ou_owner1", "ou_owner2"],
                            "unhealthy_after_sec": 180, "repeat_sec": 1800, "local_log": True, "windows_toast": False},
                 "bot_names": ["杭州某某科技有限公司13812345678"]}
    names = ["杭州某某科技有限公司"]
    action = health_alert.tick(alert_cfg, work, names, False, 1000, now=1179, sender=sender)
    check("没到点不写告警、不调用发送", action is None and calls == [] and not os.path.isfile(os.path.join(work, "logs", "alerts.log")))
    action = health_alert.tick(alert_cfg, work, names, False, 1000, now=1180, sender=sender)
    log_text = open(os.path.join(work, "logs", "alerts.log"), encoding="utf-8").read()
    check("到点给每位主人一条，正文已脱敏，日志不写 open_id",
          action == "down" and len(calls) == 2 and calls[0][0] == "user" and calls[1][0] == "user"
          and calls[0][1] == "ou_owner1" and "杭州某某科技有限公司" not in calls[0][2]
          and "13812345678" not in calls[0][2] and "某客户" in calls[0][2] and "[手机号已隐藏]" in calls[0][2]
          and "ou_owner1" not in log_text and "ou_owner2" not in log_text and "13812345678" not in log_text
          and calls[0][3] != calls[1][3] and "ou_owner1" not in calls[0][3] and len(calls[0][3]) <= 50,
          log_text + "\n" + str(calls))
    health_alert.tick(alert_cfg, work, names, False, 1000, now=1300, sender=sender)
    check("状态已经记下，短时间内不再发", len(calls) == 2)
    lonely = tempfile.mkdtemp(prefix="agentbot_alert_none_")
    try:
        none_calls = []
        none_cfg = {"alerts": {"enabled": True, "owner_open_ids": [], "local_log": True}, "bot_names": ["小助手"]}
        health_alert.tick(none_cfg, lonely, [], False, 1000, now=2000, sender=lambda *a: none_calls.append(a))
        none_log = open(os.path.join(lonely, "logs", "alerts.log"), encoding="utf-8").read()
        check("没有主人时只写本机日志", none_calls == [] and "feishu=0/0" in none_log and "长连接已中断" in none_log, none_log)
    finally:
        shutil.rmtree(lonely, ignore_errors=True)
    off = tempfile.mkdtemp(prefix="agentbot_alert_off_")
    try:
        off_calls = []
        health_alert.tick({"bot_names": ["小助手"]}, off, [], False, 1, now=10**10, sender=lambda *a: off_calls.append(a))
        check("关掉时不写状态也不发送", off_calls == [] and not os.path.isfile(os.path.join(off, "state", "health_alert.json")))
    finally:
        shutil.rmtree(off, ignore_errors=True)
finally:
    shutil.rmtree(work, ignore_errors=True)

ext = tempfile.mkdtemp(prefix="agentbot_ext_")
try:
    hb = os.path.join(ext, "state", "heartbeat")
    os.makedirs(os.path.dirname(hb), exist_ok=True)
    sent = []
    cfg = {"alerts": {"enabled": True, "owner_open_ids": ["ou_owner1"], "unhealthy_after_sec": 180, "repeat_sec": 1800},
           "bot_names": ["小助手"]}
    health_alert.external_check(cfg, ext, [], now=5000, sender=lambda *a: sent.append(a))
    check("没有心跳文件不告警", sent == [] and not os.path.isfile(os.path.join(ext, "state", "health_alert.json")))
    open(hb, "w", encoding="utf-8").write("4900")
    before = open(hb, encoding="utf-8").read()
    health_alert.external_check(cfg, ext, [], now=5080, sender=lambda *a: sent.append(a))
    check("心跳还新鲜时不发，也不改心跳文件", sent == [] and open(hb, encoding="utf-8").read() == before)
    open(hb, "w", encoding="utf-8").write("1000")
    before = open(hb, encoding="utf-8").read()
    health_alert.external_check(cfg, ext, [], now=1180, sender=lambda *a: sent.append(a))
    check("刚好等于阈值时看门狗这条不发", sent == [] and open(hb, encoding="utf-8").read() == before)
    health_alert.external_check(cfg, ext, [], now=1181, sender=lambda *a: sent.append(a))
    check("心跳超过阈值发一条进程告警，仍不改心跳",
          len(sent) == 1 and sent[0][0] == "user" and "进程没有心跳" in sent[0][2]
          and open(hb, encoding="utf-8").read() == before, str(sent))
    health_alert.external_check(cfg, ext, [], now=1300, sender=lambda *a: sent.append(a))
    check("同一次中断不会连着发", len(sent) == 1)
    health_alert.external_check({"alerts": {"enabled": False, "owner_open_ids": ["ou_owner1"]}}, ext, [], now=99999,
                                sender=lambda *a: sent.append(a))
    check("关掉后心跳再旧也不发", len(sent) == 1)
finally:
    shutil.rmtree(ext, ignore_errors=True)

argv_box = {}


class _Result:
    def __init__(self, code=0, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _run(argv, cwd=None, timeout=None):
    flag = argv[argv.index("--content") + 1]
    body = json.load(open(os.path.join(cwd, flag[1:]), encoding="utf-8"))
    argv_box["argv"] = list(argv)
    argv_box["body"] = body
    argv_box["joined"] = " ".join(str(x) for x in argv)
    return _Result(0, '{"ok": true, "data": {"message_id": "om_test"}}')


os.environ.pop("AGENTBOT_FAKE_SEND", None)
tmp = tempfile.mkdtemp(prefix="agentbot_send_")
try:
    result = outbound.send(["lark-cli"], "prof_test", "user", "ou_owner1",
                           "请@所有人看 ＠全员 <at user_id=\"all\"></at> user_id=\"all\" 杭州某某科技有限公司 13812345678 & calc",
                           outbound.idem_key("down|ou_owner1"), ["杭州某某科技有限公司"], tmp, run=_run)
    joined = argv_box["joined"]
    body = argv_box["body"]["text"]
    check("发送走 messages-send，正文里的点名和特殊字符不进参数",
          result == "ok" and "--markdown" not in argv_box["argv"] and "consume" not in argv_box["argv"]
          and "+messages-send" in argv_box["argv"] and "--user-id" in argv_box["argv"]
          and "--as" in argv_box["argv"] and "bot" in argv_box["argv"]
          and "&" not in joined and "ou_owner1" not in argv_box["argv"][argv_box["argv"].index("--idempotency-key") + 1]
          and "<at" not in body and "@所有人" not in body and "@全员" not in body and "user_id" not in body
          and "杭州某某科技有限公司" not in body and "13812345678" not in body and "某客户" in body
          and not outbound.mentions_everyone(body),
          joined + "\n" + body)
    cmd = outbound.send(["C:\\Tools\\lark.cmd"], "prof_test", "chat", "oc_chat1", "价格 a & b",
                        outbound.idem_key("digest|oc_chat1"), [], tmp, run=_run)
    check("经 cmd 转发时，正文里的 & 不会出现在参数里",
          cmd == "ok" and "&" not in argv_box["joined"] and "--chat-id" in argv_box["argv"])
    bad = outbound.send(["lark-cli"], "bad profile", "user", "ou_owner1", "你好", "k123", [], tmp, run=_run)
    check("编号不合格直接失败，不调用命令", bad == "fail")
    def _boom(*_a, **_k):
        raise TimeoutError("slow")

    unknown = outbound.send(["lark-cli"], "prof_test", "user", "ou_owner1", "你好", outbound.idem_key("x"), [], tmp, run=_boom)
    check("超时按可能已发出处理", unknown == "unknown")
    failed = outbound.send(["lark-cli"], "prof_test", "user", "ou_owner1", "你好", outbound.idem_key("y"), [], tmp,
                           run=lambda *a, **k: _Result(2, '{"ok": false}'))
    check("明确失败可以再试", failed == "fail")
    only = outbound.send(["lark-cli"], "prof_test", "chat", "oc_chat1", "@all", outbound.idem_key("z"), [], tmp, run=_run)
    check("只剩点名所有人时不发", only == "fail")
    check("@allocation 不是点名所有人",
          outbound.prepare("see @allocation", []) == "see @allocation" and not outbound.mentions_everyone("see @allocation"))
    key = outbound.idem_key("digest|2026-10-19|oc_chat1")
    check("幂等键固定长度，不带群号", len(key) <= 50 and key.startswith("k") and "oc_chat1" not in key and len(key) == 21)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sink = tempfile.mkdtemp(prefix="agentbot_fake_send_")
try:
    os.environ["AGENTBOT_FAKE_SEND"] = os.path.join(sink, "sent.jsonl")
    os.environ["AGENTBOT_FAKE_SEND_RESULT"] = "ok"
    got = outbound.send(["lark-cli"], "prof_test", "chat", "oc_chat1", "今天的安排", outbound.idem_key("m"), [], sink)
    row = json.loads(open(os.environ["AGENTBOT_FAKE_SEND"], encoding="utf-8").read().splitlines()[0])
    check("测试替身记下种类、目标和键", got == "ok" and row["via"] == "messages-send" and row["kind"] == "chat" and row["target"] == "oc_chat1")
finally:
    os.environ.pop("AGENTBOT_FAKE_SEND", None)
    os.environ.pop("AGENTBOT_FAKE_SEND_RESULT", None)
    shutil.rmtree(sink, ignore_errors=True)

toast = health_alert.toast_command("标题", "a & b | c")
script = toast[-1]
check("系统通知把正文放进 base64，参数里没有 &",
      "a & b" not in script and "FromBase64String" in script and toast[0] == "powershell.exe")
called = []
toasted = health_alert.maybe_toast(True, "标题", "a & b", run=called.append)
if os.name == "nt":
    check("Windows 上才尝试系统通知", toasted is True and called and "a & b" not in called[0][-1])
else:
    check("非 Windows 不弹系统通知", toasted is False and called == [])
check("没打开时不弹", health_alert.maybe_toast(False, "标题", "a & b", run=called.append) is False)

sh_ts = datetime.datetime(2026, 10, 19, 0, 30, tzinfo=datetime.timezone.utc).timestamp()
old_zone = digest.ZoneInfo


class _Boom:
    def __init__(self, name):
        raise OSError("no tzdata")


digest.ZoneInfo = _Boom
try:
    fallback = digest.zone("Asia/Shanghai")
    moment = digest.local_now(sh_ts, fallback)
    check("没有 tzdata 时上海时间仍是东八区 08:30",
          fallback is not None and moment.hour == 8 and moment.minute == 30
          and digest.in_window(moment, 8, 30, 30) and not digest.in_window(moment, 9, 0, 30))
    check("不认识的时区不猜测", digest.zone("Mars/Phobos") is None)
finally:
    digest.ZoneInfo = old_zone
live = digest.zone("Asia/Shanghai")
live_moment = digest.local_now(sh_ts, live)
check("本机时区数据存在时，同一时刻仍是 08:30", live_moment.hour == 8 and live_moment.minute == 30)

late = digest.local_now(datetime.datetime(2026, 10, 19, 23, 50, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp(),
                        digest._FIXED_ZONES["Asia/Shanghai"])
early = digest.local_now(datetime.datetime(2026, 10, 20, 0, 10, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp(),
                         digest._FIXED_ZONES["Asia/Shanghai"])
check("跨过午夜的窗口仍算开始那天",
      digest.in_window(late, 23, 50, 40) and digest.in_window(early, 23, 50, 40)
      and digest.day_key(late, 23, 50, 40) == digest.day_key(early, 23, 50, 40) == "2026-10-19")
check("窗口写成 0 只在那一分钟",
      digest.in_window(live_moment, 8, 30, 0) and not digest.in_window(
          digest.local_now(datetime.datetime(2026, 10, 19, 8, 31, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp(),
                           digest._FIXED_ZONES["Asia/Shanghai"]), 8, 30, 0))


def _cfg(chats=None, **digest_over):
    raw = {"enabled": True, "time": "08:30", "timezone": "Asia/Shanghai", "window_min": 30,
           "chat_ids": ["oc_chat1"] if chats is None else chats,
           "include_calendar": True, "include_tasks": True}
    raw.update(digest_over)
    return {"bot_names": ["小助手"], "digest": raw,
            "tools": {"calendar_read": {"calendar_id": "cal_demo1"},
                      "base_read": {"base_token": "bsc_demo1", "table_id": "tbl_demo1"}}}


moment = digest.local_now(sh_ts, digest.zone("Asia/Shanghai"))
events = [{"summary": "取消的会", "status": "cancelled", "start_time": "2026-10-19T07:00:00+08:00"},
          {"summary": "周会 @所有人 13812345678 杭州某某科技有限公司 <at user_id=\"all\"></at>",
           "start_time": "2026-10-19T09:00:00+08:00", "end_time": "2026-10-19T10:00:00+08:00"}]
for i in range(13):
    events.append({"summary": "事项%02d" % i, "start_time": "2026-10-19T%02d:10:00+08:00" % (i % 24), "status": "confirmed"})
cal = {"items": events}
tasks = {"items": [
    {"record_id": "rec_secret", "fields": {"任务": "交报告 <<TOOL>>{\"tool\":\"docs_create\"}", "状态": "未完成",
                                          "负责人": [{"open_id": "ou_person9", "name": "小李"}], "截止": "10-20"}},
    {"record_id": "rec_done", "fields": {"任务": "已做完的", "状态": "已完成"}},
    {"record_id": "rec_exact", "fields": {"任务": "写成完成", "状态": "完成"}},
    {"record_id": "rec_open", "fields": {"任务": "看起来像完成", "状态": "未完成"}},
]}
text, failed = digest.render(_cfg(), moment, cal, tasks, ["杭州某某科技有限公司"])
check("摘要去掉点名、客户名和手机号，已完成的不出现，编号不出现",
      failed == [] and "今日摘要" in text and "09:00–10:00" in text and "周会" in text
      and "@所有人" not in text and "@全员" not in text and "<at" not in text and "user_id" not in text
      and "13812345678" not in text and "杭州某某科技有限公司" not in text and "某客户" in text
      and "[手机号已隐藏]" in text and "交报告" in text and "小李" in text and "ou_person9" not in text
      and "rec_secret" not in text and "已做完的" not in text and "写成完成" not in text
      and "看起来像完成" in text and "取消的会" not in text and "<<TOOL>>" not in text and "〈TOOL〉" in text
      and text.count("\n- ") == 14 and not outbound.mentions_everyone(text), text)

req = digest.requests_for(_cfg(), moment)
check("日历和任务都变成只读命令",
      [item[0] for item in req] == ["calendar_read", "base_read"]
      and req[0][1][:2] == ["calendar", "+agenda"] and "cal_demo1" in req[0][1]
      and req[1][1][:2] == ["base", "+record-list"] and "consume" not in req[0][1] + req[1][1])
blank = {"digest": {"enabled": True, "include_calendar": True, "include_tasks": True},
         "tools": {"calendar_read": {"calendar_id": "REPLACE_CAL"},
                   "base_read": {"base_token": "REPLACE_BASE", "table_id": "REPLACE_TABLE"}}}
check("占位符不算已配置，不会去读", digest.requests_for(blank, moment) == [] and digest.calendar_id(blank) == "" and digest.task_table(blank) is None)

box = tempfile.mkdtemp(prefix="agentbot_digest_")
try:
    fetches = []
    posts = []

    def fetch(tool, argv):
        fetches.append(tool)
        if tool == "calendar_read":
            return cal
        return tasks

    def sender(chat, text, key):
        posts.append((chat, text, key))
        return "ok"

    cfg = _cfg(chats=["oc_chat1", "oc_chat2"])
    out_of = digest.deliver(cfg, box, ["杭州某某科技有限公司"], 
                            datetime.datetime(2026, 10, 19, 7, 0, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp(),
                            fetch, sender, ["oc_chat1", "oc_chat2"])
    check("窗口外不读也不发", out_of == [] and fetches == [] and posts == [])
    now = sh_ts
    sent = digest.deliver(cfg, box, ["杭州某某科技有限公司"], now, fetch, sender, ["oc_chat1", "oc_chat2"])
    check("窗口内每个群一条，键不相同且不含群号",
          sent == ["oc_chat1", "oc_chat2"] and len(posts) == 2 and posts[0][2] != posts[1][2]
          and "oc_chat1" not in posts[0][2] and "oc_chat2" not in posts[1][2] and fetches == ["calendar_read", "base_read"],
          str(posts))
    again_posts = len(posts)
    digest.deliver(cfg, box, ["杭州某某科技有限公司"], now + 60, fetch, sender, ["oc_chat1", "oc_chat2"])
    check("同一天再调用不读也不发", len(posts) == again_posts and fetches == ["calendar_read", "base_read"])
    nxt = datetime.datetime(2026, 10, 20, 8, 30, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp()
    digest.deliver(cfg, box, ["杭州某某科技有限公司"], nxt, fetch, sender, ["oc_chat1", "oc_chat2"])
    check("下一天可以再发", len(posts) == again_posts + 2)

    claimed = tempfile.mkdtemp(prefix="agentbot_claim_")
    try:
        path = os.path.join(claimed, "state", "digest_state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        day = "2026-10-19"
        json.dump({"items": {day + "|oc_chat1": {"status": "claimed", "at": now}}}, open(path, "w", encoding="utf-8"))
        crashed = []
        digest.deliver(_cfg(), claimed, [], now, lambda *a: crashed.append(a), lambda *a: crashed.append(a), ["oc_chat1"])
        check("已经占坑但进程没记下成功时，不再发第二条", crashed == [])
    finally:
        shutil.rmtree(claimed, ignore_errors=True)

    retry = tempfile.mkdtemp(prefix="agentbot_retry_")
    try:
        seq = {"n": 0}

        def flaky(chat, text, key):
            seq["n"] += 1
            return "fail" if seq["n"] == 1 else "ok"

        digest.deliver(_cfg(), retry, [], now, fetch, flaky, ["oc_chat1"])
        digest.deliver(_cfg(), retry, [], now + 30, fetch, flaky, ["oc_chat1"])
        third = []
        digest.deliver(_cfg(), retry, [], now + 40, fetch, lambda *a: third.append(a) or "ok", ["oc_chat1"])
        check("明确失败会放掉名额并再试一次，成功后停止", seq["n"] == 2 and third == [])
    finally:
        shutil.rmtree(retry, ignore_errors=True)

    unk = tempfile.mkdtemp(prefix="agentbot_unknown_")
    try:
        times = {"n": 0}

        def maybe(chat, text, key):
            times["n"] += 1
            return "unknown"

        digest.deliver(_cfg(), unk, [], now, fetch, maybe, ["oc_chat1"])
        digest.deliver(_cfg(), unk, [], now + 30, fetch, maybe, ["oc_chat1"])
        check("结果不清楚就不再发", times["n"] == 1)
    finally:
        shutil.rmtree(unk, ignore_errors=True)

    missed = tempfile.mkdtemp(prefix="agentbot_miss_")
    try:
        got = digest.deliver(_cfg(chats=["oc_chat1", "oc_other"]), missed, [], now, fetch, sender, ["oc_chat1"])
        check("不在服务范围里的群不发", got == ["oc_chat1"])
        nowhere = []
        digest.deliver(_cfg(chats=[]), missed, [], now, fetch, lambda *a: nowhere.append(a), ["oc_chat1"])
        digest.deliver(_cfg(chats=["oc_out"]), missed, [], now + 1, fetch, lambda *a: nowhere.append(a), ["oc_chat1"])
        check("没配群或不在服务范围时不发", nowhere == [])
    finally:
        shutil.rmtree(missed, ignore_errors=True)

    bad_src = tempfile.mkdtemp(prefix="agentbot_src_")
    try:
        calls = {"n": 0}

        def broken(tool, argv):
            calls["n"] += 1
            return {"_error": "token=supersecretvalue"}

        digest.deliver(_cfg(), bad_src, [], now, broken, sender, ["oc_chat1"])
        digest.deliver(_cfg(), bad_src, [], now + 30, broken, sender, ["oc_chat1"])
        check("两边都失败时不占坑，五分钟内不反复读", calls["n"] == 2, str(calls))
        state = json.load(open(os.path.join(bad_src, "state", "digest_state.json"), encoding="utf-8"))
        check("失败不记成已发送，群里的正文也不会带原始错误",
              "items" not in state or not state.get("items"), state)
        shown, why = digest.render(_cfg(), moment, {"_error": "token=supersecretvalue"}, {"_error": "nope"}, [])
        check("读失败只说暂时没有读到", "暂时没有读到" in shown and "supersecretvalue" not in shown and why == ["calendar", "tasks"], shown)
        digest.deliver(_cfg(), bad_src, [], now + 301, broken, sender, ["oc_chat1"])
        check("过了五分钟可以再读一次", calls["n"] == 4)
    finally:
        shutil.rmtree(bad_src, ignore_errors=True)

    half = tempfile.mkdtemp(prefix="agentbot_half_")
    try:
        def half_fetch(tool, argv):
            if tool == "calendar_read":
                return {"_error": "down"}
            return tasks

        posted = []
        digest.deliver(_cfg(), half, ["杭州某某科技有限公司"], now, half_fetch, lambda *a: posted.append(a) or "ok", ["oc_chat1"])
        digest.deliver(_cfg(), half, [], now + 60, half_fetch, lambda *a: posted.append(a) or "ok", ["oc_chat1"])
        check("有一边成功就发一条，同一天不补第二条",
              len(posted) == 1 and "暂时没有读到" in posted[0][1] and "交报告" in posted[0][1] and "down" not in posted[0][1],
              posted[0][1] if posted else "")
    finally:
        shutil.rmtree(half, ignore_errors=True)

    wrap = tempfile.mkdtemp(prefix="agentbot_wrap_")
    try:
        wrap_posts = []
        wcfg = _cfg(**{"time": "23:50", "window_min": 40})
        t1 = datetime.datetime(2026, 10, 19, 23, 50, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp()
        t2 = datetime.datetime(2026, 10, 20, 0, 10, tzinfo=digest._FIXED_ZONES["Asia/Shanghai"]).timestamp()
        digest.deliver(wcfg, wrap, [], t1, fetch, lambda *a: wrap_posts.append(a) or "ok", ["oc_chat1"])
        digest.deliver(wcfg, wrap, [], t2, fetch, lambda *a: wrap_posts.append(a) or "ok", ["oc_chat1"])
        check("23:50 和次日 00:10 只发一条", len(wrap_posts) == 1)
    finally:
        shutil.rmtree(wrap, ignore_errors=True)

    bad_tz = tempfile.mkdtemp(prefix="agentbot_tz_")
    try:
        tz_fetch = []
        digest.deliver(_cfg(**{"timezone": "Mars/Phobos"}), bad_tz, [], now, lambda *a: tz_fetch.append(a), sender, ["oc_chat1"])
        digest.deliver(_cfg(**{"timezone": "Mars/Phobos"}), bad_tz, [], now + 10, lambda *a: tz_fetch.append(a), sender, ["oc_chat1"])
        check("时区无效时不发", tz_fetch == [])
    finally:
        shutil.rmtree(bad_tz, ignore_errors=True)
finally:
    shutil.rmtree(box, ignore_errors=True)

cfg_min = {"profile": "placeholder", "features": {"p2p": False, "attachments": False}, "progress": {"enabled": False},
           "tools": {"history": {"enabled": True}}, "chat_ids": ["oc_placeholder"]}
check("没开摘要时 doctor 的权限清单不变",
      doctor.required_scopes(cfg_min) == ["im:message.group_at_msg:readonly", "im:message:send_as_bot",
                                          "im:message:readonly", "im:message.group_msg"])
scope_cfg = {"features": {"p2p": False, "attachments": False}, "progress": {"enabled": False}, "chat_ids": ["oc_demo1"],
             "tools": {"calendar_read": {"enabled": False, "calendar_id": "cal_demo1"},
                       "base_read": {"enabled": False, "base_token": "bsc_demo1", "table_id": "tbl_demo1"}},
             "digest": {"enabled": True, "include_calendar": True, "include_tasks": True}}
scopes = doctor.required_scopes(scope_cfg)
check("摘要要读日历和任务表时补这两项权限，各一次",
      scopes.count("calendar:calendar.event:read") == 1 and scopes.count("base:record:read") == 1)
scope_cfg["tools"]["calendar_read"]["calendar_id"] = "REPLACE_CAL"
scope_cfg["digest"]["include_tasks"] = False
check("占位符日历和关掉的任务栏不额外要权限",
      "calendar:calendar.event:read" not in doctor.required_scopes(scope_cfg)
      and "base:record:read" not in doctor.required_scopes(scope_cfg))
both = dict(scope_cfg)
both["tools"] = {"calendar_read": {"enabled": True, "calendar_id": "cal_demo1"}}
both["digest"] = {"enabled": True, "include_calendar": True, "include_tasks": False}
check("工具本身已经要的日历权限不重复列",
      doctor.required_scopes(both).count("calendar:calendar.event:read") == 1)

watch = open(os.path.join(FW, "scripts", "watchdog.ps1"), "rb").read()
watch_txt = watch.decode("utf-8-sig")
check("看门狗仍是 UTF-8 BOM，先判断开关再通知，失败也不挡住重启",
      watch[:3] == b"\xef\xbb\xbf" and "&&" not in watch_txt and "??" not in watch_txt
      and watch_txt.find("alerts.enabled") < watch_txt.find("--alert-check") < watch_txt.find("start.ps1")
      and "if ($alive)" in watch_txt and "stop.ps1" in watch_txt and "taskkill" not in watch_txt.lower()
      and "alert-check failed" in watch_txt and "不依赖事件长连接" in watch_txt)


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
    return path


def _reply_of(text, mid):
    key = "[dry-run] reply %s: " % mid
    i = text.find(key)
    if i < 0:
        return ""
    lines = []
    for n, line in enumerate(text[i + len(key):].splitlines()):
        if n and len(line) > 30 and line[4] == "-" and " INFO " in line[:40]:
            break
        lines.append(line)
    return "\n".join(lines)


def _prompts(text):
    out = []
    for line in text.splitlines():
        if "fake prompt: " not in line:
            continue
        out.append(json.loads(line.split("fake prompt: ", 1)[1]))
    return out


def _stable(prompt):
    cut = len(prompt)
    for mark in ("\n\n以下是本话题之前的对话", "\n\n提问："):
        i = prompt.find(mark)
        if i != -1:
            cut = min(cut, i)
    return prompt[:cut]


note = digest.prompt_note({"digest": {"enabled": True}})
run_dir = tempfile.mkdtemp(prefix="agentbot_prompt_")
try:
    here = datetime.datetime.now(digest.zone("Asia/Shanghai"))
    cfg_over = {
        "alerts": {"enabled": True, "owner_open_ids": ["ou_owner1"], "unhealthy_after_sec": 180, "repeat_sec": 1800,
                   "local_log": True, "windows_toast": False},
        "digest": {"enabled": True, "time": "%02d:%02d" % (here.hour, here.minute), "timezone": "Asia/Shanghai",
                   "chat_ids": ["oc_chat1"], "window_min": 30, "include_calendar": True, "include_tasks": True},
    }
    path = _load_example(run_dir, **cfg_over)
    # 日历编号放进已经写好的文件，确认演练仍不会去发摘要。
    loaded = json.load(open(path, encoding="utf-8"))
    loaded["tools"]["calendar_read"]["calendar_id"] = "cal_demo1"
    loaded["tools"]["base_read"].update({"base_token": "bsc_demo1", "table_id": "tbl_demo1"})
    json.dump(loaded, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    sink = os.path.join(run_dir, "sent.jsonl")
    events = os.path.join(run_dir, "events.jsonl")
    bot = "小助手"
    mention = [{"id": "ou_bot_placeholder", "key": "@_user_1", "name": bot}]

    def ev(mid, text, root=""):
        item = {"message_id": mid, "chat_id": "oc_chat1", "chat_type": "group", "message_type": "text",
                "sender_id": "ou_sender1", "sender_type": "user", "content": "@%s %s" % (bot, text), "mentions": mention}
        if root:
            item["root_id"] = root
        return {"event": item}

    open(events, "w", encoding="utf-8").write("\n".join(json.dumps(item, ensure_ascii=False) for item in (
        ev("q1", "今天有什么安排"), ev("q2", "再问一次", "q1"), ev("st", "/状态"))) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
    env["AGENTBOT_FAKE_SEND"] = sink
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                          cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    prompts = _prompts(out)
    stables = [_stable(p) for p in prompts]
    check("打开摘要后，说明在稳定前缀里，两次提问逐字相同，今天的日程不进提示词",
          proc.returncode == 0 and len(prompts) == 2 and note in stables[0] and stables[0] == stables[1]
          and stables[0].find(note) < stables[0].find("当前会话类型") and "提问：" not in stables[0]
          and "周会" not in prompts[0] and "cal_demo1" not in prompts[0],
          out[-1500:])
    reply = _reply_of(out, "st")
    check("打开后 /状态 多两行，不打印编号",
          "断线提醒：开" in reply and "早晨摘要：开" in reply and "ou_owner1" not in reply and "oc_chat1" not in reply, reply)
    check("演练不发摘要也不发断线私聊",
          not os.path.isfile(sink) and not os.path.isfile(os.path.join(run_dir, "state", "health_alert.json"))
          and not os.path.isfile(os.path.join(run_dir, "state", "digest_state.json")))
finally:
    shutil.rmtree(run_dir, ignore_errors=True)

off_dir = tempfile.mkdtemp(prefix="agentbot_offprompt_")
try:
    path = _load_example(off_dir)
    loaded = json.load(open(path, encoding="utf-8"))
    loaded.pop("alerts", None)
    loaded.pop("digest", None)
    json.dump(loaded, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    events = os.path.join(off_dir, "events.jsonl")
    bot = "小助手"
    mention = [{"id": "ou_bot_placeholder", "key": "@_user_1", "name": bot}]
    item = {"event": {"message_id": "q0", "chat_id": "oc_chat1", "chat_type": "group", "message_type": "text",
                      "sender_id": "ou_sender1", "sender_type": "user", "content": "@%s 你好" % bot, "mentions": mention}}
    status = {"event": dict(item["event"], message_id="st0", content="@%s /状态" % bot)}
    open(events, "w", encoding="utf-8").write(json.dumps(item, ensure_ascii=False) + "\n" + json.dumps(status, ensure_ascii=False) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                          cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    prompts = _prompts(out)
    quiet_reply = _reply_of(out, "st0")
    check("旧配置没有这两项时，提示词和 /状态 都和以前一样",
          proc.returncode == 0 and len(prompts) == 1 and note not in prompts[0] and "早晨摘要由程序" not in prompts[0]
          and "断线提醒" not in quiet_reply and "早晨摘要" not in quiet_reply, quiet_reply + "\n" + out[-800:])
finally:
    shutil.rmtree(off_dir, ignore_errors=True)


def _alert_proc(work, enabled, heartbeat):
    path = _load_example(work, alerts={"enabled": enabled, "owner_open_ids": ["ou_owner1"], "unhealthy_after_sec": 180,
                                       "repeat_sec": 1800, "local_log": True, "windows_toast": False})
    if heartbeat is not None:
        os.makedirs(os.path.join(work, "state"), exist_ok=True)
        open(os.path.join(work, "state", "heartbeat"), "w", encoding="utf-8").write(heartbeat)
    sink = os.path.join(work, "sent.jsonl")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_SEND"] = sink
    env["AGENTBOT_FAKE_SEND_RESULT"] = "ok"
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--alert-check"],
                          cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    return proc, sink


one = tempfile.mkdtemp(prefix="agentbot_check_")
try:
    proc, sink = _alert_proc(one, True, "1000")
    out = (proc.stdout or "") + (proc.stderr or "")
    rows = []
    if os.path.isfile(sink):
        rows = [json.loads(line) for line in open(sink, encoding="utf-8") if line.strip()]
    hb = open(os.path.join(one, "state", "heartbeat"), encoding="utf-8").read()
    check("心跳过期时 --alert-check 发一条私聊，不拿锁、不启动长连接、不改心跳",
          proc.returncode == 0 and len(rows) == 1 and rows[0]["kind"] == "user" and rows[0]["target"] == "ou_owner1"
          and rows[0]["via"] == "messages-send" and "长连接" not in rows[0]["text"] and "进程没有心跳" in rows[0]["text"]
          and hb == "1000" and not os.path.isfile(os.path.join(one, "state", "consumer.pid"))
          and not os.path.isfile(os.path.join(one, "state", "bot.lock"))
          and "lock acquired" not in out and "starting " not in out, out[-1500:] + "\n" + str(rows))
    proc2, sink2 = _alert_proc(one, True, None)
    rows2 = [json.loads(line) for line in open(sink2, encoding="utf-8") if line.strip()] if os.path.isfile(sink2) else []
    check("同一次中断再跑一次不会多发", proc2.returncode == 0 and len(rows2) == 1 and open(os.path.join(one, "state", "heartbeat"), encoding="utf-8").read() == "1000")
finally:
    shutil.rmtree(one, ignore_errors=True)

quiet = tempfile.mkdtemp(prefix="agentbot_check_off_")
try:
    proc, sink = _alert_proc(quiet, False, "1000")
    check("没打开时 --alert-check 直接返回，不发送",
          proc.returncode == 0 and not os.path.isfile(sink) and not os.path.isfile(os.path.join(quiet, "state", "health_alert.json"))
          and not os.path.isfile(os.path.join(quiet, "state", "consumer.pid")))
finally:
    shutil.rmtree(quiet, ignore_errors=True)

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
