# 测试用假 ACP 服务端：stdio 上收 JSON-RPC，按提示词里的标记回话。
# 带 -p 时改走一次性 stream-json，用来验证常驻失败后回退到原来的 -p，而且不会把回退也当成 ACP。
import json, os, sys, time

TRACE = os.environ.get("AGENTBOT_ACP_TRACE")
PERM_ID = 8000


def trace(obj):
    if not TRACE:
        return
    with open(TRACE, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


def send(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def oneshot(prompt):
    """和 fake_stream_agent 同一类 stream-json，让 AgentStream 能取出最终文字。"""
    def out(e):
        sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    if "工具结果（skill_read）" in prompt:
        a = "按技能回答：BODY_PROGRESS"
    elif "工具结果（" in prompt:
        a = "根据工具结果：周五交报名"
    else:
        a = "一次性回退答案"
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": a}]}, "timestamp_ms": int(time.time() * 1000)})
    out({"type": "result", "subtype": "success", "is_error": False, "result": a})


def text_of(params):
    parts = []
    for b in (params or {}).get("prompt") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text") or "")
    return "".join(parts)


def chunk(sid, text, thought=False):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": sid,
        "update": {
            "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
            "content": {"type": "text", "text": text},
        },
    }})


def tool_read(sid):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": sid,
        "update": {
            "sessionUpdate": "tool_call",
            "toolCallId": "call_read_1",
            "name": "read_file",
            "kind": "read",
            "status": "in_progress",
            "title": "Reading note",
            "locations": [{"path": "kb/周报.md"}],
            "rawInput": {"path": "kb/周报.md"},
        },
    }})


def finish(req_id, sid, answer, thought=False, read=False, pause=0):
    if thought:
        chunk(sid, "想一想\n整理答案结构", thought=True)
    if read:
        tool_read(sid)
    if pause:
        time.sleep(pause)
    if answer:
        mid = max(1, len(answer) // 2)
        chunk(sid, answer[:mid])
        chunk(sid, answer[mid:])
    send({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})


def answer_for(text):
    if "工具结果（skill_read）" in text:
        return "按技能回答：BODY_PROGRESS"
    if "工具结果（" in text:
        return "根据工具结果：周五交报名"
    if "<<ACP_TOOL>>" in text:
        return '<<TOOL>>{"tool":"history","args":{}}'
    if "<<ACP_SKILL>>" in text:
        return '<<TOOL>>{"tool":"skill_read","name":"progress-table"}'
    return "常驻答案"


def handle_prompt(req_id, sid, text, wait):
    trace({"event": "prompt", "sessionId": sid, "text": text})
    # 人设里就有「工具结果」四个字。只有工具回注（带括号）才不算这一题本身。
    follow = "工具结果（" in text
    if "<<ACP_CRASH>>" in text and not follow:
        trace({"event": "crash"})
        os._exit(1)
    if "<<ACP_PROTO>>" in text and not follow:
        send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": "simulated protocol error"}})
        return
    if "<<ACP_STALL>>" in text and not follow:
        chunk(sid, "卡住前的片段")
        time.sleep(120)
        return
    if "<<ACP_SLOW>>" in text and not follow:
        for _ in range(80):
            chunk(sid, "。", thought=True)
            time.sleep(0.25)
        finish(req_id, sid, "慢答案")
        return
    if "<<ACP_PERM>>" in text or "<<ACP_PERM_BARE>>" in text:
        global PERM_ID
        PERM_ID += 1
        bare = "<<ACP_PERM_BARE>>" in text
        opts = [{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"}]
        if not bare:
            opts.append({"optionId": "reject-once", "name": "Reject", "kind": "reject_once"})
        send({"jsonrpc": "2.0", "id": PERM_ID, "method": "session/request_permission", "params": {
            "sessionId": sid,
            "toolCall": {"toolCallId": "call_edit_1", "title": "写文件", "kind": "edit", "status": "pending"},
            "options": opts,
        }})
        wait[PERM_ID] = (req_id, sid)
        return
    if "<<ACP_PROGRESS>>" in text:
        finish(req_id, sid, "常驻答案", thought=True, read=True, pause=2.2)
        return
    finish(req_id, sid, answer_for(text))


def on_perm(msg, wait):
    item = wait.pop(msg.get("id"), None)
    if not item:
        return
    req_id, sid = item
    outcome = ((msg.get("result") or {}).get("outcome") or {})
    trace({"event": "permission_result", "outcome": outcome})
    rejected = outcome.get("outcome") == "cancelled" or outcome.get("optionId") in ("reject-once", "reject-always")
    finish(req_id, sid, "已拒绝权限请求" if rejected else "权限被允许")


def _model_arg():
    if "--model" in sys.argv:
        i = sys.argv.index("--model")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "grok-4.7-high-fast"


def config_options():
    """session/new 里的模型配置项。用 AGENTBOT_ACP_CONFIG_CASE 切换形状，默认和原来一样。"""
    case = os.environ.get("AGENTBOT_ACP_CONFIG_CASE") or ""
    model = _model_arg()
    if case == "none":
        return []
    if case == "empty":
        return [{
            "id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "auto", "options": [],
        }]
    if case == "same":
        return [{
            "id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": model,
            "options": [{"value": model, "name": "grok"}],
        }]
    if case == "unlisted":
        return [{
            "id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "other-model",
            "options": [{"value": "other-model", "name": "Other"}],
        }]
    if case == "grouped":
        return [{
            "configId": "cursor_model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "auto",
            "options": [{
                "group": "recommended", "name": "Recommended",
                "options": [{"value": "model-value-id", "name": model}],
            }],
        }]
    if case == "invalid":
        return [{
            "id": "model", "name": "Model", "category": "model", "type": "select",
            "currentValue": "auto",
            "options": [{"value": model, "name": "grok"}],
        }]
    return [{
        "id": "model", "name": "Model", "category": "model", "type": "select",
        "currentValue": "auto",
        "options": [{"value": "grok-4.7-high-fast", "name": "grok"}],
    }]


def serve():
    if os.environ.get("AGENTBOT_ACP_FAIL_INIT"):
        os._exit(2)
    sessions = 0
    wait = {}
    for raw in iter(sys.stdin.buffer.readline, b""):
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if "method" not in msg and "id" in msg:
            on_perm(msg, wait)
            continue
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        trace({"event": "rpc", "method": method, "id": mid, "params": params})
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": False, "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False}},
                "agentInfo": {"name": "fake-acp", "version": "0"},
                "authMethods": [{"id": "cursor_login", "name": "Cursor Login"}],
            }})
        elif method == "authenticate":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/new":
            sessions += 1
            sid = "sess-%s-%d" % (os.getpid(), sessions)
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": sid,
                "modes": {
                    "currentModeId": "agent",
                    "availableModes": [
                        {"id": "agent", "name": "Agent"},
                        {"id": "plan", "name": "Plan"},
                        {"id": "ask", "name": "Ask"},
                    ],
                },
                "configOptions": config_options(),
            }})
        elif method == "session/set_mode":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/set_config_option":
            if os.environ.get("AGENTBOT_ACP_CONFIG_CASE") == "invalid":
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid params"}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {"configOptions": []}})
        elif method == "session/cancel":
            pass
        elif method == "session/prompt":
            handle_prompt(mid, params.get("sessionId"), text_of(params), wait)
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    trace({"event": "argv", "argv": sys.argv[1:]})
    if "-p" in sys.argv:
        oneshot(sys.argv[-1])
    else:
        serve()
