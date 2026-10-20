# OpenAI 兼容的 /chat/completions。只用标准库 urllib，必须流式。
# base_url 和 model 写在配置里。密钥只从环境变量读，不写进配置、日志或报错。
import json, os, queue, re, socket, threading, time, urllib.error, urllib.request

import acp_client
from providers.base import PartialText, Provider, emit
from providers.presets import preset_id
from providers.settings import how_to_set, http_spec, key_required, note_usage

_RETRY_HTTP = {408, 409, 429, 500, 502, 503, 504}


class HttpFail(Exception):
    def __init__(self, code):
        super().__init__("HTTP %s" % code)
        self.code = int(code)


class NetFail(Exception):
    pass


def chat_url(base):
    b = (base or "").strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        pid = preset_id(cfg)
        if pid:
            self.log.info("model preset %s", pid)

    def _agent(self):
        # 预设只补空着的地址、模型名和变量名。model.* 盖过预设。这里不读密钥。
        return http_spec(self.cfg)

    def _model(self):
        return (self._agent().get("model") or "").strip()

    def _base(self):
        return (self._agent().get("base_url") or "").strip()

    def _env_name(self):
        return (self._agent().get("api_key_env") or "").strip()

    def _key(self):
        name = self._env_name()
        if not name:
            return ""
        return os.environ.get(name) or ""

    def _redact(self, text):
        text = str(text or "")
        key = self._key()
        if key and key in text:
            text = text.replace(key, "***")
        return re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)

    def _request(self, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "agentbot",
        }
        key = self._key()
        if key:
            headers["Authorization"] = "Bearer " + key
        return urllib.request.Request(chat_url(self._base()), data=data, headers=headers, method="POST")

    def _limits(self):
        a = self._agent()
        max_rt, stall, _pulse = acp_client.runtime_limits(a)
        return max_rt, stall, int(a.get("max_chars", 3500) or 3500)

    def _feed_line(self, line, on_stage, state):
        s = (line or "").strip()
        if not s.startswith("data:"):
            return False
        data = s[5:].strip()
        if data == "[DONE]":
            return True
        try:
            obj = json.loads(data)
        except Exception:
            return False
        if isinstance(obj, dict) and obj.get("error"):
            raise HttpFail(obj.get("status") or 400)
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            state["usage"] = obj.get("usage")
        choices = obj.get("choices") if isinstance(obj, dict) else None
        if not choices or not isinstance(choices[0], dict):
            return False
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            delta = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
        reason = delta.get("reasoning_content") or ""
        if not reason and isinstance(delta.get("reasoning"), str):
            reason = delta.get("reasoning") or ""
        content = delta.get("content") or ""
        if isinstance(content, list):
            content = "".join((p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
        if not isinstance(reason, str):
            reason = ""
        if not isinstance(content, str):
            content = str(content) if content else ""
        if reason:
            state["saw_reason"] = True
            emit(on_stage, think=reason)
            if not state["text"]:
                emit(on_stage, stage="正在思考…")
        if content:
            if state["saw_reason"] and not state["reason_end"]:
                state["reason_end"] = True
                emit(on_stage, think="", end=True)
            state["text"] += content
            emit(on_stage, text=content)
        return False

    def _stream_once(self, prompt, on_stage):
        max_rt, stall, max_chars = self._limits()
        body = self._body([{"role": "user", "content": prompt}])
        req = self._request(body)
        connect_timeout = self._connect_timeout(stall)
        try:
            resp = urllib.request.urlopen(req, timeout=connect_timeout)
        except urllib.error.HTTPError as e:
            try: e.close()
            except Exception: pass
            raise HttpFail(e.code)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as e:
            raise NetFail(self._redact(getattr(e, "reason", e)))
        sock = self._socket_of(resp)
        q = queue.Queue(); stop = threading.Event(); done = threading.Event()
        def rd():
            try:
                while not stop.is_set():
                    line = resp.readline()
                    if not line:
                        q.put(None)
                        return
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", "replace")
                    q.put(line)
            except Exception as e:
                if not stop.is_set():
                    q.put(e)
            finally:
                q.put(None)
                done.set()
        threading.Thread(target=rd, daemon=True).start()
        state = {"text": "", "saw_reason": False, "reason_end": False}
        _pulse = acp_client.runtime_limits(self._agent())[2]
        t0 = last = last_pulse = time.time(); why = ""; events = 0
        try:
            while True:
                try:
                    item = q.get(timeout=1)
                except queue.Empty:
                    item = ""
                now = time.time()
                if isinstance(item, Exception):
                    if not why:
                        raise NetFail(self._redact(item))
                    break
                if item is None:
                    break
                if item:
                    last = now
                    events += 1
                    if self._feed_line(item, on_stage, state):
                        break
                if not why:
                    why = acp_client.limit_reason(now, t0, last, stall, max_rt)
                    if not why and acp_client.pulse_due(now, last_pulse, _pulse):
                        last_pulse = now
                        silent = now - last
                        self.log.info("openai pulse elapsed=%ds silent=%.0fs", int(now - t0), silent)
                        if silent >= _pulse and on_stage:
                            try: on_stage("已经 %d 秒没有新输出…" % int(silent))
                            except Exception: pass
                    if why:
                        stop.set()
                        self._cut(sock)
                        break
        finally:
            stop.set()
            self._cut(sock)
            done.wait(2)
            try:
                resp.close()
            except Exception:
                pass
        part = state["text"].strip()
        note_usage(state.get("usage"))
        if why:
            self.log.error("openai_compat stopped: %s events=%d partial_chars=%d", why, events, len(part))
            return PartialText(part[:max_chars])
        if state["saw_reason"] and not state["reason_end"]:
            emit(on_stage, think="", end=True)
        if not part:
            self.log.error("openai_compat empty model=%s", self._model())
            return None
        return part[:max_chars]

    @staticmethod
    def _socket_of(resp):
        try:
            return resp.fp.raw._sock
        except Exception:
            return None

    @staticmethod
    def _cut(sock):
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def complete(self, prompt, sess=None, on_stage=None):
        if not self._base() or not self._model():
            self.log.error("openai_compat missing base_url or model")
            return None
        if key_required(self.cfg) and (not self._env_name() or not self._key()):
            self.log.error("openai_compat missing env %s", self._env_name() or "(api_key_env)")
            return None
        retries = int(self._agent().get("network_retries", 2) or 0)
        attempts = 1 + max(0, retries)
        for i in range(attempts):
            try:
                return self._stream_once(prompt, on_stage)
            except NetFail as e:
                msg = self._redact(e)
                self.log.error("openai_compat network model=%s try=%d/%d err=%s", self._model(), i + 1, attempts, msg[:300])
                if i + 1 >= attempts:
                    return None
                time.sleep(10 * (i + 1))
            except HttpFail as e:
                if e.code in _RETRY_HTTP and i + 1 < attempts:
                    self.log.error("openai_compat http=%s model=%s try=%d/%d", e.code, self._model(), i + 1, attempts)
                    time.sleep(10 * (i + 1))
                    continue
                self.log.error("openai_compat http=%s model=%s", e.code, self._model())
                return None
        return None

    def _body(self, messages):
        spec = self._agent()
        body = {"model": self._model(), "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}}
        if spec.get("temperature") is not None:
            body["temperature"] = spec["temperature"]
        if spec.get("max_tokens"):
            body["max_tokens"] = spec["max_tokens"]
        return body

    def _connect_timeout(self, stall):
        raw = self._agent().get("timeout_sec")
        if raw:
            return float(raw)
        return min(30.0, max(5.0, stall))

    def _probe(self, max_tokens):
        body = {"model": self._model(), "messages": [{"role": "user", "content": "ping"}], "stream": True}
        if max_tokens:
            body["max_tokens"] = max_tokens
        req = self._request(body)
        try:
            resp = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            try: e.close()
            except Exception: pass
            raise HttpFail(e.code)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as e:
            raise NetFail(self._redact(getattr(e, "reason", e)))
        try:
            status = getattr(resp, "status", 200)
            if status >= 400:
                raise HttpFail(status)
            resp.read(512)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def check_items(self):
        label = self.name or "openai_compat"
        env_name = self._env_name()
        if not self._base():
            return [(label, False, "未配置 agent.base_url")]
        if not self._model():
            return [(label, False, "未配置 agent.model")]
        if key_required(self.cfg):
            if not env_name:
                return [(label, False, "未配置 agent.api_key_env（只写变量名，不写密钥）")]
            if not self._key():
                return [(label, False, "环境变量 %s 未设置。%s" % (env_name, how_to_set(env_name)))]
        ready = "环境变量 %s 已设置" % env_name if env_name else "本机地址不要求密钥"
        try:
            try:
                self._probe(1)
            except HttpFail as e:
                if e.code != 400:
                    raise
                self._probe(None)
        except HttpFail as e:
            return [(label, False, "%s；短请求 HTTP %s" % (ready, e.code))]
        except Exception as e:
            return [(label, False, "%s；短请求失败 %s" % (ready, self._redact(e)[:200]))]
        return [(label, True, "%s；短请求通过 model=%s" % (ready, self._model()))]
