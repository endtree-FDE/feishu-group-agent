# 测试用的 OpenAI 兼容假服务。只在本机回流式 /chat/completions，不连接真的模型。
import json, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _drop(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def _begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _delta(self, delta):
        line = "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]}, ensure_ascii=False) + "\n\n"
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    def _done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _chunks(self, parts, key="content"):
        self._begin()
        for part in parts:
            self._delta({key: part})
        self._done()

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        body = self._read_body()
        model = str(body.get("model") or "")
        messages = body.get("messages") or []
        content = "\n".join((m.get("content") or "") for m in messages if isinstance(m, dict) and isinstance(m.get("content"), str))
        auth = self.headers.get("Authorization") or ""
        self.server.hits.append({"model": model, "auth": auth, "ping": content.strip() == "ping"})
        if model == "deny-auth":
            self._json(401, {"error": {"message": "invalid api key", "echo": auth}})
            return
        if model == "flaky-net":
            self.server.flaky += 1
            if self.server.flaky == 1:
                self._drop()
                return
            self._chunks(["重试成功的", "答案"])
            return
        if content.strip() == "ping":
            self._chunks(["p"])
            return
        if "<<OA_REASON>>" in content:
            self._begin()
            self._delta({"reasoning_content": "整理"})
            self._delta({"reasoning_content": "答案结构"})
            import time
            time.sleep(1.6)
            self._delta({"content": "推理后的答案"})
            self._done()
            return
        if "<<OA_STALL>>" in content:
            self._begin()
            try:
                self._delta({"content": "已经写出的部分：先看第一步，杭州某某科技有限公司要先报名"})
                import time
                time.sleep(30)
                self._done()
            except Exception:
                pass
            return
        if "<<OA_SKILL>>" in content and "工具结果（skill_read）" not in content:
            self._chunks(['<<TOOL>>{"tool":"skill_read","name":"progress-table"}'])
            return
        if "工具结果（skill_read）" in content:
            self._chunks(["按技能回答：BODY_PROGRESS"])
            return
        if "<<OA_TOOL>>" in content and "工具结果（" not in content:
            self._chunks(['<<TOOL>>{"tool":"history","args":{}}'])
            return
        if "工具结果（" in content:
            self._chunks(["根据工具结果：周五交报名"])
            return
        self._chunks(["流式", "答案", "甲"])


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.hits = []
    httpd.flaky = 0
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
