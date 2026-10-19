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

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
