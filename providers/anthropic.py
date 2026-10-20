# Anthropic Messages API。只用标准库 urllib，必须流式。
# 密钥只从环境变量读。日志和报错里不出现密钥。
import json, os, queue, re, socket, threading, time, urllib.error, urllib.request

import acp_client
from providers.base import PartialText, Provider, emit
from providers.openai_compat import HttpFail, NetFail
from providers.settings import how_to_set, http_spec, key_required, note_usage

_RETRY_HTTP = {408, 409, 429, 500, 502, 503, 504}
_VERSION = "2023-06-01"


def messages_url(base):
    b = (base or "").strip().rstrip("/")
    if b.endswith("/messages"):
        return b
    if b.endswith("/v1"):
        return b + "/messages"
    return b + "/v1/messages"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log

    def _agent(self):
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
        text = re.sub(r"(?i)x-api-key\s*[:=]\s*\S+", "x-api-key: ***", text)
        return re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)

    def _max_tokens(self):
        raw = self._agent().get("max_tokens")
        if raw:
            return int(raw)
        return 4096

    def _body(self, prompt):
        spec = self._agent()
        body = {
            "model": self._model(),
            "max_tokens": self._max_tokens(),
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if spec.get("temperature") is not None:
            body["temperature"] = spec["temperature"]
        return body

    def _request(self, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-version": _VERSION,
            "User-Agent": "agentbot",
        }
        key = self._key()
        if key:
            headers["x-api-key"] = key
        return urllib.request.Request(messages_url(self._base()), data=data, headers=headers, method="POST")

    def _limits(self):
        a = self._agent()
        max_rt, stall, _pulse = acp_client.runtime_limits(a)
        return max_rt, stall, int(a.get("max_chars", 3500) or 3500)

    def _connect_timeout(self, stall):
        raw = self._agent().get("timeout_sec")
        if raw:
            return float(raw)
        return min(30.0, max(5.0, stall))

    def _take(self, obj, event_name, on_stage, state):
        typ = ""
        if isinstance(obj, dict):
            typ = str(obj.get("type") or event_name or "")
        if typ == "error" or (isinstance(obj, dict) and obj.get("error")):
            err = obj.get("error") if isinstance(obj, dict) else {}
            kind = str((err or {}).get("type") or "")
            code = 401 if "auth" in kind else int((err or {}).get("status") or 400)
            raise HttpFail(code)
        if typ == "message_start":
            usage = ((obj.get("message") or {}).get("usage") or {}) if isinstance(obj, dict) else {}
            if isinstance(usage, dict) and usage.get("input_tokens") is not None:
                state["input_tokens"] = usage.get("input_tokens")
            return False
        if typ == "message_delta":
            usage = obj.get("usage") if isinstance(obj, dict) else None
            if isinstance(usage, dict) and usage.get("output_tokens") is not None:
                state["output_tokens"] = usage.get("output_tokens")
            return False
        if typ != "content_block_delta" or not isinstance(obj, dict):
            return typ == "message_stop"
        delta = obj.get("delta") if isinstance(obj.get("delta"), dict) else {}
        kind = str(delta.get("type") or "")
        if kind == "thinking_delta":
            reason = delta.get("thinking") or ""
            if isinstance(reason, str) and reason:
                state["saw_reason"] = True
                emit(on_stage, think=reason)
                if not state["text"]:
                    emit(on_stage, stage="正在思考…")
            return False
        if kind == "text_delta":
            content = delta.get("text") or ""
            if not isinstance(content, str):
                content = str(content) if content else ""
            if content:
                if state["saw_reason"] and not state["reason_end"]:
                    state["reason_end"] = True
                    emit(on_stage, think="", end=True)
                state["text"] += content
                emit(on_stage, text=content)
        return False

    def _feed_line(self, line, on_stage, state):
        raw = line or ""
        s = raw.strip()
        if not s:
            return False
        if s.startswith("event:"):
            state["event"] = s[6:].strip()
            return False
        if not s.startswith("data:"):
            return False
        data = s[5:].strip()
        if data == "[DONE]":
            return True
        try:
            obj = json.loads(data)
        except Exception:
            return False
        return self._take(obj, state.get("event") or "", on_stage, state)

    def _stream_once(self, prompt, on_stage):
        max_rt, stall, max_chars = self._limits()
        req = self._request(self._body(prompt))
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
        state = {"text": "", "saw_reason": False, "reason_end": False, "event": ""}
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
                        self.log.info("anthropic pulse elapsed=%ds silent=%.0fs", int(now - t0), silent)
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
        usage = {}
        if state.get("input_tokens") is not None:
            usage["input_tokens"] = state.get("input_tokens")
        if state.get("output_tokens") is not None:
            usage["output_tokens"] = state.get("output_tokens")
        note_usage(usage)
        if why:
            self.log.error("anthropic stopped: %s events=%d partial_chars=%d", why, events, len(part))
            return PartialText(part[:max_chars])
        if state["saw_reason"] and not state["reason_end"]:
            emit(on_stage, think="", end=True)
        if not part:
            self.log.error("anthropic empty model=%s", self._model())
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
            self.log.error("anthropic missing base_url or model")
            return None
        if key_required(self.cfg) and (not self._env_name() or not self._key()):
            self.log.error("anthropic missing env %s", self._env_name() or "(api_key_env)")
            return None
        retries = int(self._agent().get("network_retries", 2) or 0)
        attempts = 1 + max(0, retries)
        for i in range(attempts):
            try:
                return self._stream_once(prompt, on_stage)
            except NetFail as e:
                msg = self._redact(e)
                self.log.error("anthropic network model=%s try=%d/%d err=%s", self._model(), i + 1, attempts, msg[:300])
                if i + 1 >= attempts:
                    return None
                time.sleep(10 * (i + 1))
            except HttpFail as e:
                if e.code in _RETRY_HTTP and i + 1 < attempts:
                    self.log.error("anthropic http=%s model=%s try=%d/%d", e.code, self._model(), i + 1, attempts)
                    time.sleep(10 * (i + 1))
                    continue
                self.log.error("anthropic http=%s model=%s", e.code, self._model())
                return None
        return None

    def _probe(self):
        body = self._body("ping")
        body["max_tokens"] = 1
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
        label = self.name or "anthropic"
        env_name = self._env_name()
        if not self._model():
            return [(label, False, "未配置 model.model")]
        if key_required(self.cfg):
            if not env_name:
                return [(label, False, "未配置 api_key_env（只写变量名，不写密钥）")]
            if not self._key():
                return [(label, False, "环境变量 %s 未设置。%s" % (env_name, how_to_set(env_name)))]
        ready = "环境变量 %s 已设置" % env_name if env_name else "本机地址不要求密钥"
        try:
            self._probe()
        except HttpFail as e:
            return [(label, False, "%s；短请求 HTTP %s" % (ready, e.code))]
        except Exception as e:
            return [(label, False, "%s；短请求失败 %s" % (ready, self._redact(e)[:200]))]
        return [(label, True, "%s；短请求通过 model=%s" % (ready, self._model()))]
