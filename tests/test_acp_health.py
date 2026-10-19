# ACP 卡死判断、进度心跳、崩溃拉起、set_config_option 的形状。不连飞书，也不用真的 cursor-agent。
import json, os, subprocess, sys, tempfile, threading, time

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import acp_client

results = []


def check(name, cond, out=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and out:
        print(str(out)[-1500:])


class Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a):
        self.lines.append(msg % a if a else str(msg))

    def warning(self, msg, *a):
        self.lines.append(msg % a if a else str(msg))

    def error(self, msg, *a):
        self.lines.append(msg % a if a else str(msg))

    def debug(self, *a, **k):
        pass


def test_limits():
    max_rt, stall, pulse = acp_client.runtime_limits({})
    check("没写时限时仍是 1800 秒，和旧配置一样", max_rt == 1800 and stall == 300 and pulse == 20)
    max_rt, stall, pulse = acp_client.runtime_limits({"max_runtime_sec": 0, "stall_sec": 0, "progress_heartbeat_sec": 0})
    check("max_runtime_sec=0 不设硬时限；stall 写成 0 仍保留卡死检测；心跳可关", max_rt == 0 and stall == 300 and pulse == 0)
    check("没有新输出才算卡死，总时长不截", acp_client.limit_reason(100, 0, 90, 300, 0) == "" and acp_client.limit_reason(400, 0, 399, 300, 0) == "")
    check("静默超过 stall_sec 报 stall", acp_client.limit_reason(500, 0, 100, 300, 0).startswith("stall"))
    check("只有 max_runtime_sec>0 才报总时长", acp_client.limit_reason(50, 0, 49, 300, 30).startswith("max_runtime") and acp_client.limit_reason(10, 0, 9, 300, 0) == "")
    bodies = acp_client.config_option_attempts("s", "model", "grok-4.7-high-fast")
    check("set_config_option 先带 type=id，失败再退回不带 type",
          bodies[0]["type"] == "id" and bodies[0]["value"] == "grok-4.7-high-fast" and "type" not in bodies[1] and bodies[1]["configId"] == "model")


SERVER = r"""
import json, sys, time
def send(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
    sys.stdout.buffer.flush()
for raw in iter(sys.stdin.buffer.readline, b""):
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1}})
    elif method == "authenticate":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "sessionId": "s",
            "modes": {"currentModeId": "ask", "availableModes": [{"id": "ask"}]},
            "configOptions": [],
        }})
    elif method == "session/prompt":
        time.sleep(1.2)
        send({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "s",
            "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "脉冲答案"}},
        }})
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        pass
"""


def test_pulse():
    tmp = tempfile.mkdtemp(prefix="acp_pulse_")
    path = os.path.join(tmp, "srv.py")
    open(path, "w", encoding="utf-8").write(SERVER)
    log = Log()
    stages = []
    pool = acp_client.Pool(
        lambda: [sys.executable, path],
        {"max_runtime_sec": 0, "stall_sec": 30, "progress_heartbeat_sec": 0.4, "model": ""},
        tmp, log,
    )
    try:
        out = pool.ask("k", "提问：你好", on_stage=lambda stage, **_k: stages.append(stage))
    finally:
        pool.close()
    text = "\n".join(log.lines)
    check("不设硬时限时，静默心跳不把还在答的题杀掉", out == "脉冲答案" and "acp fallback" not in text and "stall" not in text, text)
    check("长时间没有新输出会打 acp pulse，并改进度文案",
          any("acp pulse" in l for l in log.lines) and any(s and "没有新输出" in s for s in stages), text + str(stages))


def _sleep_child():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def test_restart_pause():
    tmp = tempfile.mkdtemp(prefix="acp_restart_")
    log = Log()
    pool = acp_client.Pool(lambda: [sys.executable, "-c", "import time; time.sleep(30)"], {"stall_sec": 30}, tmp, log)
    kids = []

    def start_locked():
        child = _sleep_child()
        kids.append(child)
        pool.proc = child
        pool.ready_flag = True

    pool._start_locked = start_locked
    old_sleep = time.sleep
    time.sleep = lambda _s: None
    try:
        for _ in range(5):
            pool._schedule_restart()
            if pool._restart_thread:
                pool._restart_thread.join(5)
        pool._schedule_restart()
    finally:
        time.sleep = old_sleep
        pool.close()
        for child in kids:
            if child.poll() is None:
                child.kill()
                try: child.wait(timeout=3)
                except Exception: pass
    text = "\n".join(log.lines)
    check("10 分钟内反复退出 5 次后暂停自动重启", text.count("acp restart\n") + text.count("acp restart") >= 5 and "acp restart paused" in text, text)


def test_idle_exit_restarts():
    tmp = tempfile.mkdtemp(prefix="acp_watch_")
    log = Log()
    pool = acp_client.Pool(lambda: [sys.executable, "-c", "import sys; sys.exit(0)"], {"stall_sec": 30}, tmp, log)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    pool.proc = proc
    pool.gen = 1
    pool.ready_flag = True
    try:
        pool._watch_proc(proc, 1)
        if pool._restart_thread:
            pool._restart_thread.join(5)
    finally:
        pool.close()
        if proc.poll() is None:
            proc.kill()
    text = "\n".join(log.lines)
    check("空闲时子进程退出会记日志并尝试拉起", "acp exited rc=" in text and "acp restart" in text, text)


def main():
    test_limits()
    test_pulse()
    test_restart_pause()
    test_idle_exit_restarts()
    passed = sum(1 for _, ok in results if ok)
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
