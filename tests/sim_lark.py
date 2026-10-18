#!/usr/bin/env python3
# 端到端模拟用的假 lark-cli。按 SIM_SCENARIO 里的脚本回答，不连接飞书。
# event consume 把脚本里的事件打成一行一个 JSON，然后保持打开，避免消费进程立刻退出重连。
import json, os, sys, time

try:
    import fcntl
except ImportError:
    fcntl = None

argv = sys.argv[1:]
logp = os.environ.get("SIM_LARK_LOG")
statep = os.environ.get("SIM_LARK_STATE") or ""
scenario = {}
path = os.environ.get("SIM_SCENARIO")
if path and os.path.isfile(path):
    with open(path, encoding="utf-8") as f:
        scenario = json.load(f)

def log_call():
    if not logp: return
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv, ensure_ascii=False) + "\n")

def emit(obj, code=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    raise SystemExit(code)

def flag(name):
    if name not in argv: return ""
    i = argv.index(name)
    return argv[i + 1] if i + 1 < len(argv) else ""

def take_script(key):
    """同一条命令可以按调用次数返回不同结果。用完最后一条就一直用最后一条。"""
    seq = (scenario.get("faults") or {}).get(key)
    if not isinstance(seq, list) or not seq or not statep: return None
    os.makedirs(os.path.dirname(statep), exist_ok=True)
    with open(statep, "a+", encoding="utf-8") as f:
        if fcntl: fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try: state = json.loads(raw) if raw.strip() else {}
        except Exception: state = {}
        if not isinstance(state, dict): state = {}
        n = int(state.get(key) or 0)
        state[key] = n + 1
        f.seek(0); f.truncate(); f.write(json.dumps(state, ensure_ascii=False)); f.flush()
    return seq[n] if n < len(seq) else seq[-1]

log_call()

if argv[:2] == ["event", "consume"]:
    key = argv[2] if len(argv) > 2 else ""
    streams = scenario.get("streams") or {}
    lines = streams.get(key) or []
    for line in lines:
        sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    if scenario.get("stream_hold", True):
        try:
            while True: time.sleep(3600)
        except KeyboardInterrupt:
            pass
    raise SystemExit(0)

if argv[:2] == ["im", "+messages-mget"]:
    mid = flag("--message-ids")
    msg = dict(((scenario.get("messages") or {}).get(mid) or {"message_id": mid, "msg_type": "text", "content": ""}))
    if "--download-resources" in argv:
        folder = os.path.join(os.getcwd(), "lark-im-resources", "x")
        os.makedirs(folder, exist_ok=True)
        for item in (scenario.get("resources") or {}).get(mid) or []:
            name = os.path.basename(str(item.get("name") or "file.bin"))
            if not name or name in (".", ".."): continue
            dest = os.path.join(folder, name)
            if "text" in item:
                with open(dest, "w", encoding="utf-8") as f: f.write(item.get("text") or "")
            elif "b64" in item:
                import base64
                with open(dest, "wb") as f: f.write(base64.b64decode(item["b64"]))
    emit({"ok": True, "data": {"messages": [msg]}})

if argv[:2] == ["api", "GET"]:
    url = argv[2] if len(argv) > 2 else ""
    found = (scenario.get("api") or {}).get(url)
    emit(found or {"ok": True, "data": {}})

key = " ".join(argv[:2])
scripted = take_script(key)
if scripted is not None:
    if scripted.get("_stderr"):
        sys.stderr.write(str(scripted.get("_stderr")))
    emit({k: v for k, v in scripted.items() if k != "_stderr"}, 0 if scripted.get("ok", True) else 0)

found = (scenario.get("commands") or {}).get(key)
if found is not None:
    emit(found)
emit({"ok": True, "data": {}})
