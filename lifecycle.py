# 优雅停机：停的时候把还没答完的题留在 state，重启再补。
# 不在这里结束进程。谁可以停，由 proc_identity 和 scripts/stop.ps1 决定。
import json, os, threading, time
import guard

SHUTDOWN = threading.Event()
_NOTES = {}
_LOCK = threading.Lock()


def should_mark_answered(shutting_down, finished):
    """答完了就记成已回答。停机时这题还没走完，留着 received，下次启动补答。"""
    if finished:
        return True
    if shutting_down:
        return False
    return True


def wait_sec(cfg):
    try:
        value = float((cfg or {}).get("shutdown_wait_sec", 15))
    except (TypeError, ValueError):
        return 15.0
    if value < 0:
        return 15.0
    return value


def request_path(state_dir):
    return os.path.join(state_dir, "shutdown.request")


def inflight_path(state_dir):
    return os.path.join(state_dir, "inflight.json")


def request_shutdown(state_dir=None):
    SHUTDOWN.set()
    if not state_dir:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(request_path(state_dir), "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def shutdown_requested(state_dir):
    if SHUTDOWN.is_set():
        return True
    if state_dir and os.path.isfile(request_path(state_dir)):
        SHUTDOWN.set()
        return True
    return False


def clear_request(state_dir):
    SHUTDOWN.clear()
    try:
        os.remove(request_path(state_dir))
    except OSError:
        pass


def _flush(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    path = inflight_path(state_dir)
    payload = {"messages": list(_NOTES.values())}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def touch(state_dir, mid, stage, names=()):
    """只记消息编号、阶段和开始时间。不记问题原文。阶段文字过一遍脱敏。"""
    key = str(mid)[:80]
    stage = guard.redact(stage or "", names)[:80]
    with _LOCK:
        prev = _NOTES.get(key) or {}
        _NOTES[key] = {"mid": key, "stage": stage, "started": prev.get("started") or time.time()}
        _flush(state_dir)


def drop(state_dir, mid):
    key = str(mid)[:80]
    with _LOCK:
        if key not in _NOTES:
            return
        _NOTES.pop(key, None)
        _flush(state_dir)


def reset_notes():
    """测试用。正式进程不调用。"""
    with _LOCK:
        _NOTES.clear()
