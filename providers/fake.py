# 离线测试用的假模型。原来写在 agentbot.run_agent 里的 AGENTBOT_FAKE_AGENT 分支。
import json, os, re, time

from providers.base import Provider, emit


def _scripted_steps(prompt):
    """测试用：<<FAKE_SEQ>>[...]<<END>> 按已经出现的工具结果条数取下一步。
    <<FAKE_SEQ_FORCE>> 即使上一轮已经要求直接回答，也继续吐出工具行，用来确认多出来的调用不会被执行。"""
    for marker, force in (("<<FAKE_SEQ_FORCE>>", True), ("<<FAKE_SEQ>>", False)):
        i = prompt.find(marker)
        if i < 0: continue
        j = prompt.find("<<END>>", i)
        if j < 0: return None, False
        try: data = json.loads(prompt[i + len(marker):j])
        except Exception: return None, False
        if isinstance(data, list): return data, force
    return None, False

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
        steps, force = _scripted_steps(prompt)
        if steps is not None:
            done = prompt.count("工具结果（")
            stop = "不要再调用工具" in prompt
            if done < len(steps) and (force or not stop):
                prefix = "先说到这里\n" if force else ""
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
        return [("model_provider", False, "不认识 %s（可用 cursor、openai_compat、fake）" % self.name)]
