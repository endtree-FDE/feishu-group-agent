# 离线测试用的假模型。原来写在 agentbot.run_agent 里的 AGENTBOT_FAKE_AGENT 分支。
import json, os, re, time

from providers.base import Provider, emit


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
