# 离线测试用的假模型。原来写在 agentbot.run_agent 里的 AGENTBOT_FAKE_AGENT 分支。
import json, os, re, time

from providers.base import Provider, emit


def _script_file_step():
    """测试用：AGENTBOT_FAKE_SCRIPT 指向 {"rounds":[...]}。每一项是一轮模型输出。
    字符串原样返回；对象或对象列表拼成多行 <<TOOL>>。轮次记在同名 .step 文件里。"""
    path = os.environ.get("AGENTBOT_FAKE_SCRIPT")
    if not path: return None
    try: data = json.load(open(path, encoding="utf-8"))
    except Exception: return "[fake] 脚本读不了"
    rounds = data.get("rounds") if isinstance(data, dict) else data
    if not isinstance(rounds, list): return "[fake] 脚本不是列表"
    state = path + ".step"
    try: n = int(open(state, encoding="utf-8").read().strip() or "0")
    except Exception: n = 0
    try: open(state, "w", encoding="utf-8").write(str(n + 1))
    except Exception: pass
    if n >= len(rounds): return "[fake] 已根据工具结果回答"
    item = rounds[n]
    if isinstance(item, str): return item
    if isinstance(item, dict): item = [item]
    if not isinstance(item, list): return "[fake] 已根据工具结果回答"
    lines = []
    for call in item:
        if isinstance(call, str): lines.append(call)
        elif isinstance(call, dict): lines.append("<<TOOL>>" + json.dumps(call, ensure_ascii=False))
    return "\n".join(lines)

def _scripted_steps(prompt):
    """测试用：<<FAKE_SEQ>>[...]<<END>> 按已经出现的工具结果条数取下一步。
    <<FAKE_SEQ_FORCE>> 即使上一轮已经要求直接回答，也继续吐出带前缀的工具行，用来确认多出来的调用不会被执行。
    <<FAKE_SEQ_BARE>> 同样忽略停止语，但只吐工具行、不带前缀，用来确认剥掉之后不会变成空回答。"""
    for marker, mode in (("<<FAKE_SEQ_BARE>>", "bare"), ("<<FAKE_SEQ_FORCE>>", "force"), ("<<FAKE_SEQ>>", "normal")):
        i = prompt.find(marker)
        if i < 0: continue
        j = prompt.find("<<END>>", i)
        if j < 0: return None, "normal"
        try: data = json.loads(prompt[i + len(marker):j])
        except Exception: return None, "normal"
        if isinstance(data, list): return data, mode
    return None, "normal"

class FakeProvider(Provider):
    name = "fake"

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log

    def complete(self, prompt, sess=None, on_stage=None):
        emit(on_stage, stage="正在思考…")
        delay = os.environ.get("AGENTBOT_FAKE_AGENT")
        if delay:
            time.sleep(float(delay))
        if os.environ.get("AGENTBOT_FAKE_DUMP_PROMPT"):
            self.log.info("fake prompt: %s", json.dumps(prompt, ensure_ascii=False))
        scripted = _script_file_step()
        if scripted is not None: return scripted
        steps, mode = _scripted_steps(prompt)
        if steps is not None:
            done = prompt.count("工具结果（")
            stop = "不要再调用工具" in prompt
            if done < len(steps) and (mode != "normal" or not stop):
                prefix = "先说到这里\n" if mode == "force" else ""
                return prefix + "<<TOOL>>" + json.dumps(steps[done], ensure_ascii=False)
            return "[fake] 已根据工具结果回答"
        m = re.search(r"<<FAKE_TOOL>>(\{.*?\})<<END>>", prompt)
        if m and "工具结果（" not in prompt:
            return "<<TOOL>>" + m.group(1)
        long = re.search(r"<<FAKE_REPLY>>([\s\S]*?)<<END>>", prompt)
        if long and "工具结果（" not in prompt:
            return long.group(1)
        # 测试用：如果用户要求照搬工具结果里的 <<TOOL>>，而结果里真有，就照搬。正文被消掉标记时不照搬。
        if "<<COPY_TOOL>>" in prompt and "工具结果（" in prompt:
            region = prompt.split("工具结果（", 1)[1]
            copied = re.search(r"<<TOOL>>\s*(\{.*?\})", region)
            if copied: return "<<TOOL>>" + copied.group(1)
            return "[fake] 未照搬"
        return "[fake] " + prompt.rsplit("提问：", 1)[-1][:200]

    def refresh_memory(self, chat_id, block):
        self.log.info("memory snapshot refreshed %s", chat_id)

    def check_items(self):
        return [("fake", True, "测试模式，不连接模型")]


class UnknownProvider(Provider):
    def __init__(self, name, log):
        self.name = name or "unknown"
        self.log = log

    def complete(self, prompt, sess=None, on_stage=None):
        self.log.error("unknown model_provider %s", self.name)
        return None

    def check_items(self):
        return [("model_provider", False, "不认识 %s（可用 cursor、openai_compatible、anthropic、fake，或预设 deepseek、dashscope、zhipu、pangu）" % self.name)]
