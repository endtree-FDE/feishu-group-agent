# 测试用假 cursor-agent：按提示词里的标记输出 stream-json（--stream-partial-output 格式，与 2026.10.16 版实测一致）
import json, sys, time
prompt = sys.argv[-1]; SID = "fake-session"
def out(e): sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n"); sys.stdout.flush()
def ts(): return int(time.time() * 1000)
def delta(t): out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}, "session_id": SID, "timestamp_ms": ts()})
def flush(t, before_tool=True):
    e = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}, "session_id": SID}
    if before_tool: e["model_call_id"] = "mc-1"; e["timestamp_ms"] = ts()
    out(e)
def tool(path):
    tc = {"readToolCall": {"args": {"path": path}}}
    out({"type": "tool_call", "subtype": "started", "call_id": "c1", "tool_call": tc, "model_call_id": "mc-1", "session_id": SID, "timestamp_ms": ts()})
    out({"type": "tool_call", "subtype": "completed", "call_id": "c1", "tool_call": tc, "model_call_id": "mc-1", "session_id": SID, "timestamp_ms": ts()})
def think(t): out({"type": "thinking", "subtype": "delta", "text": t, "session_id": SID, "timestamp_ms": ts()})
def result(all_text): out({"type": "result", "subtype": "success", "is_error": False, "result": all_text, "session_id": SID})
out({"type": "system", "subtype": "init", "session_id": SID, "model": "fake"})
out({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt[:50]}]}, "session_id": SID})
think("想一想"); time.sleep(0.3)
if "工具结果（" in prompt:      # 工具二次作答
    delta("根据工具结果："); delta("周五交报名"); flush("根据工具结果：周五交报名", False); result("根据工具结果：周五交报名")
elif "<<STREAM_TOOL>>" in prompt:  # 首轮输出工具行（流式下 <<TOOL>> 解析不能破）
    for t in ['<<TOOL>>{"tool":"history",', '"args":{}}']: delta(t)
    flush('<<TOOL>>{"tool":"history","args":{}}', False); result('<<TOOL>>{"tool":"history","args":{}}')
elif "<<STREAM_NORMAL>>" in prompt:
    pre = "我先查一下知识库。"; delta("我先查"); delta("一下知识库。"); flush(pre); time.sleep(1.5)
    tool("D:\\bots\\example\\kb\\杭州某某科技有限公司周报.md"); time.sleep(1.5)
    think("看看 D:\\bots\\example\\kb\\杭州某某科技有限公司周报.md 和 kb/资料/赛程.md 里的要点"); think("\n顺便看看薪资表"); think("\n整理答案结构"); time.sleep(1.2); a = ["哈", "哈", "答案第一段，", "同一段", "\n\n答案第二段"]
    for t in a: delta(t); time.sleep(0.4)
    delta("同一段")  # 与上一段累计文本无关的重复增量，不能被当成冲刷吞掉
    full = "".join(a) + "同一段"; flush(full, False); result(pre + full)
elif "<<STREAM_STALL>>" in prompt:
    delta("已经写出的部分："); delta("先看第一步，"); delta("杭州某某科技有限公司要先报名")
    time.sleep(60)
elif "<<STREAM_STALL_TOOL>>" in prompt:
    delta('<<TOOL>>{"tool":"hist'); time.sleep(60)
elif "<<STREAM_STALL_EMPTY>>" in prompt:
    time.sleep(60)
elif "<<STREAM_CHATTY>>" in prompt:   # 一直有输出：不算卡死，只受防卡死总上限约束
    for i in range(200): think("."); time.sleep(0.2)
else:
    delta("普通回答"); flush("普通回答", False); result("普通回答")
