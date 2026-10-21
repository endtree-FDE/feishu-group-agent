# 长连接：休眠才重连，安静的群不算断线。不向身份对不上的进程发信号。
import json, os, subprocess, sys, tempfile, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import feishu_link

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


check("墙上时钟和单调时钟一起走，不算休眠",
      feishu_link.clock_jump(100, 1000, 400, 1300, 20) == 0)
check("单调时钟停住、墙上时钟跳了两分钟，就是休眠或唤醒",
      feishu_link.clock_jump(100, 1000, 101, 1120, 20) == 119)
check("只跳 5 秒（校时）不到阈值，不重连",
      feishu_link.clock_jump(100, 1000, 100, 1005, 20) == 0)
check("时钟回拨不算断线",
      feishu_link.clock_jump(100, 1000, 110, 900, 20) == 0)
check("link_jump_sec 为 0 时不因为时钟去重连",
      feishu_link.clock_jump(100, 1000, 100, 5000, 0) == 0)
check("没写 link_jump_sec 是 20 秒，0 表示关掉，写错按 20 秒",
      feishu_link.jump_setting({}) == 20 and feishu_link.jump_setting({"link_jump_sec": 0}) == 0
      and feishu_link.jump_setting({"link_jump_sec": -1}) == 20
      and feishu_link.jump_setting({"link_jump_sec": "x"}) == 20
      and feishu_link.jump_setting({"link_jump_sec": 45}) == 45)

check("很久没有消息不是断线", feishu_link.classify_log("no events for 2 hours, group is quiet") == "")
check("无关的 error 不是断线", feishu_link.classify_log("error: parse config failed") == "")
check("空日志不是断线", feishu_link.classify_log("") == "" and feishu_link.classify_log(None) == "")
check("连上和 ready 记为 up",
      feishu_link.classify_log("feishu-websocket: connected") == "up"
      and feishu_link.classify_log("ready event_key=im.message.receive_v1") == "up")
check("写明断开才记为 down",
      feishu_link.classify_log("read error: websocket: close 1006") == "down"
      and feishu_link.classify_log("feishu-websocket: disconnected") == "down"
      and feishu_link.classify_log("connection reset by peer") == "down")

check("重连等待从 1 秒翻倍，最多 60 秒",
      [feishu_link.restart_delay(n) for n in (1, 2, 3, 4, 7)] == [1, 2, 4, 8, 60])
check("上一接收进程还活着时不能再启动一个",
      feishu_link.next_consumer_action(True, "wake", False) == "wait"
      and feishu_link.next_consumer_action(True, "down", False) == "wait"
      and feishu_link.next_consumer_action(True, "exit", False) == "wait")
check("上一进程已退出才允许重连；停机则结束这一轮",
      feishu_link.next_consumer_action(False, "wake", False) == "restart"
      and feishu_link.next_consumer_action(False, "down", False) == "restart"
      and feishu_link.next_consumer_action(False, "shutdown", False) == "stop"
      and feishu_link.next_consumer_action(True, "wake", True) == "stop"
      and feishu_link.next_consumer_action(False, "exit", True) == "stop")

woke = []
watch = feishu_link.LinkWatch(jump_sec=20, on_wake=woke.append)
watch.start(100, 1000)
check("刚开始计时不算唤醒", watch.pulse(101, 1001) == 0 and woke == [])
gap = watch.pulse(102, 1001 + 90)
check("时钟跳了会加 generation，并返回多出来的秒数",
      gap == 89 and watch.generation == 1, str(gap))
check("安静地再走几秒不会再次唤醒",
      watch.pulse(110, 1001 + 90 + 8) == 0 and watch.generation == 1)
check("30 秒内的第二次跳跃不重复报",
      watch.pulse(110, 1120) == 0 and watch.generation == 1)
watch.note("im.message.receive_v1", "feishu-websocket: connected")
d1 = watch.fail("im.message.receive_v1")
watch.note("im.message.receive_v1", "feishu-websocket: connected")
d2 = watch.fail("im.message.receive_v1")
check("日志显示连上之后，连续失败次数清零", d1 == 1 and d2 == 1 and watch.failures["im.message.receive_v1"] == 1)
check("普通日志不会把连接标成断开",
      watch.note("im.message.receive_v1", "no events for 2 hours, group is quiet") == "")
up, since = watch.link_snapshot()
check("日志写明连上之后才算健康，安静不会把它改回断开", up is True and since > 0)
down_watch = feishu_link.LinkWatch()
down_watch.mark_link(False, 1000)
down_watch.mark_link(False, 2000)
still_up, still_since = down_watch.link_snapshot()
check("重复断开不把计时清零", still_up is False and still_since == 1000)
down_watch.mark_link(True, 2500)
back_up, back_since = down_watch.link_snapshot()
check("连上才改计时", back_up is True and back_since == 2500)
down_watch.start(1, 999)
kept_up, kept_since = down_watch.link_snapshot()
check("已经有计时后再 start 不覆盖", kept_up is True and kept_since == 2500)
down_watch.note("im.message.receive_v1", "feishu-websocket: disconnected")
logged_up, logged_since = down_watch.link_snapshot()
check("日志写明断开才把连接记为不健康", logged_up is False and logged_since != 2500)

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
try:
    time.sleep(0.2)
    created = feishu_link.created_token(child.pid)
    check("能读到子进程的创建时间", bool(created), created)
    check("创建时间一致时才允许发信号", feishu_link.may_signal(child, created) is True)
    check("创建时间对不上时不允许发信号，进程还在",
          feishu_link.may_signal(child, "not-ours") is False and child.poll() is None)
    check("没有记下创建时间时不允许发信号",
          feishu_link.may_signal(child, "") is False and child.poll() is None)
finally:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)
check("进程退出后不再允许发信号", feishu_link.may_signal(child, created) is False)

src = r'''
import os, sys
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot
w = agentbot._LINK
w.jump_sec = 20
w.start(10, 500)
quiet = w.pulse(70, 560)
jump = w.pulse(71, 560 + 100)
print("QUIET", quiet)
print("JUMP", jump)
print("GEN", w.generation)
print("HAS", hasattr(agentbot, "_on_wake") and hasattr(agentbot, "_poke_consumer"))
'''
work = tempfile.mkdtemp(prefix="agentbot_link_")
try:
    cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    cfg["base_dir"] = work
    cfg["bot_open_id"] = "ou_placeholder_not_a_real_account"
    path = os.path.join(work, "bot.json")
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ)
    env["CFG"] = path
    r = subprocess.run([sys.executable, "-c", src], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
    out = (r.stdout or "") + (r.stderr or "")
    check("机器人使用同一套判断：安静不加计数，时钟跳跃才加",
          r.returncode == 0 and "QUIET 0" in out and "JUMP 99" in out and "GEN 1" in out and "HAS True" in out, out)
finally:
    import shutil
    shutil.rmtree(work, ignore_errors=True)

poke = r'''
import os, sys, subprocess, time
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot, feishu_link
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(0.2)
down = agentbot.threading.Event(); down.set()
created = feishu_link.created_token(child.pid)
if os.environ["MODE"] == "bad":
    agentbot._poke_consumer(child, "not-ours", 0, down)
else:
    agentbot._poke_consumer(child, created, 0, down)
time.sleep(0.4)
print("ALIVE" if child.poll() is None else "DEAD")
if child.poll() is None:
    child.kill()
'''
for mode, expect, title in (
    ("bad", "ALIVE", "唤醒时创建时间对不上，不会结束那个进程"),
    ("ok", "DEAD", "唤醒时创建时间仍一致，才会结束本次拉起的进程"),
):
    work = tempfile.mkdtemp(prefix="agentbot_poke_")
    try:
        cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
        cfg["base_dir"] = work
        cfg["bot_open_id"] = "ou_placeholder_not_a_real_account"
        path = os.path.join(work, "bot.json")
        json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ)
        env["CFG"] = path
        env["MODE"] = mode
        r = subprocess.run([sys.executable, "-c", poke], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
        out = (r.stdout or "") + (r.stderr or "")
        check(title, r.returncode == 0 and expect in out.split(), out)
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

isolated = feishu_link.LinkWatch()
isolated.mark_link(True, 1000)
isolated.note("card.action.trigger", "feishu-websocket: disconnected")
card_up, card_since = isolated.link_snapshot()
isolated.note("card.action.trigger", "feishu-websocket: connected")
still_card_up, still_card_since = isolated.link_snapshot()
check("卡片进程断开或连上都不改变收消息的健康状态",
      card_up is True and card_since == 1000 and still_card_up is True and still_card_since == 1000)
isolated.note("im.message.receive_v1", "feishu-websocket: disconnected")
msg_up, msg_since = isolated.link_snapshot()
check("只有收消息的日志写明断开，健康状态才变成断线", msg_up is False and msg_since != 1000)
check("没订阅的日志本身不是断线",
      feishu_link.classify_log("failed_precondition: requires callbacks not subscribed in console") == "")

card_src = r'''
import os, sys, threading
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot, confirm_mode, feishu_link, health_alert
agentbot.CFG["alerts"]["enabled"] = True
agentbot.CFG["alerts"]["unhealthy_after_sec"] = 1
agentbot.CFG["alerts"]["owner_open_ids"] = ["ou_owner1"]
agentbot.CFG["report"]["owner_open_ids"] = ["ou_owner1"]
notes = []
def send(kind, target, text, key):
    notes.append((kind, target, text))
    return "ok"
agentbot._outbound_send = send
calls = []
def argv(key):
    calls.append(key)
    return [sys.executable, os.environ["FAKE"]]
agentbot.consume_argv = argv
agentbot._LINK.mark_link(True, now=1000)
print("PROBE", agentbot._probe_card_subscription())
print("PROBE_CALLS", len(calls))
calls.clear()
def run():
    agentbot._consume_forever(confirm_mode.CARD_EVENT)
worker = threading.Thread(target=run)
worker.start()
worker.join(15)
up, since = agentbot._LINK.link_snapshot()
print("JOINED", int(not worker.is_alive()))
print("RUN_CALLS", len(calls))
print("UP", int(bool(up)))
print("SINCE", since)
action = health_alert.tick(agentbot.CFG, agentbot.BASE, (), up, since, now=float(since) + 1000, sender=send)
print("ALERT", action)
before = len(notes)
agentbot._give_up_card("second")
print("NOTES", len(notes))
print("SECOND", len(notes) - before)
print("MODE", confirm_mode.effective_mode(agentbot.CFG, False, confirm_mode.cards_ready(agentbot.BASE)))
notice = open(os.path.join(agentbot.BASE, "state", "card_notice.json"), encoding="utf-8").read()
print("SECRET", int("ou_owner1" in notice))
print("TEXT", notes[0][2] if notes else "")
'''
boom_src = r'''
import os, sys, threading
sys.argv = ["agentbot.py", os.environ["CFG"], "--dry-run"]
import agentbot, feishu_link
calls = []
def argv(key):
    calls.append(key)
    if len(calls) >= 2:
        agentbot.lifecycle.request_shutdown(agentbot.STATE)
    return [sys.executable, os.environ["FAKE"]]
agentbot.consume_argv = argv
agentbot._LINK.mark_link(True, now=3000)
def run():
    agentbot._consume_forever(feishu_link.MESSAGE_EVENT)
worker = threading.Thread(target=run)
worker.start()
worker.join(15)
up, _since = agentbot._LINK.link_snapshot()
print("MSG_JOINED", int(not worker.is_alive()))
print("MSG_CALLS", len(calls))
print("MSG_UP", int(bool(up)))
'''
unsub = tempfile.mkdtemp(prefix="agentbot_unsub_")
crash = tempfile.mkdtemp(prefix="agentbot_crash_")
try:
    open(os.path.join(unsub, "exit2.py"), "w", encoding="utf-8").write(
        "import sys\n"
        "sys.stderr.write('failed_precondition: EventKey card.action.trigger requires callbacks not subscribed in console\\n')\n"
        "sys.exit(2)\n")
    open(os.path.join(crash, "exit1.py"), "w", encoding="utf-8").write(
        "import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n")
    cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    cfg["base_dir"] = unsub
    cfg["bot_open_id"] = "ou_placeholder_not_a_real_account"
    cfg["alerts"]["owner_open_ids"] = ["ou_owner1"]
    cfg["report"]["owner_open_ids"] = ["ou_owner1"]
    path = os.path.join(unsub, "bot.json")
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ)
    env["CFG"] = path
    env["FAKE"] = os.path.join(unsub, "exit2.py")
    r = subprocess.run([sys.executable, "-c", card_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=40)
    out = (r.stdout or "") + (r.stderr or "")
    check("启动前能探测到没订阅", r.returncode == 0 and "PROBE False" in out and "PROBE_CALLS 1" in out, out)
    check("没订阅时卡片进程只启动一次，不再重启",
          "JOINED 1" in out and "RUN_CALLS 1" in out, out)
    check("卡片进程退出后收消息仍算连着，断线提醒不发",
          "UP 1" in out and "ALERT None" in out, out)
    check("确认自动退回确认码，开通提醒只发一次，状态里不写编号",
          "MODE code" in out and "NOTES 1" in out and "SECOND 0" in out and "SECRET 0" in out
          and "确认码" in out and "card.action.trigger" in out, out)
    cfg["base_dir"] = crash
    path = os.path.join(crash, "bot.json")
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    env["CFG"] = path
    env["FAKE"] = os.path.join(crash, "exit1.py")
    r = subprocess.run([sys.executable, "-c", boom_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=40)
    out = (r.stdout or "") + (r.stderr or "")
    check("收消息进程退出仍会重连，并把健康状态记成断线",
          r.returncode == 0 and "MSG_JOINED 1" in out and "MSG_CALLS 2" in out and "MSG_UP 0" in out, out)
finally:
    import shutil
    shutil.rmtree(unsub, ignore_errors=True)
    shutil.rmtree(crash, ignore_errors=True)

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
