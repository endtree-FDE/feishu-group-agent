# 可选的网页搜索。默认关。新模型没有自带联网时，用 web_search 走这里。
# 密钥只从环境变量读，不写进日志。
import json, urllib.error, urllib.parse, urllib.request

import guard
from providers.settings import TAVILY_ENV, TAVILY_URL, looks_like_secret, search_settings


class SearchError(Exception):
    def __init__(self, user_msg):
        super().__init__(user_msg)
        self.user_msg = user_msg


def enabled(cfg):
    return search_settings(cfg)["enabled"]


def _key(env_name):
    if not env_name:
        return ""
    import os
    return os.environ.get(env_name) or ""


def _redact(text, key, names):
    text = str(text or "")
    if key and key in text:
        text = text.replace(key, "***")
    return guard.redact(text, names or [])


def _public_base(url):
    raw = str(url or "").strip()
    if looks_like_secret(raw):
        raise SearchError("搜索地址里不要带密钥")
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:
        raise SearchError("搜索地址不是 http 地址")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SearchError("搜索地址不是 http 地址")
    if parsed.username or parsed.password:
        raise SearchError("搜索地址里不要带密钥")
    return raw.rstrip("/")


def _clip(text, limit=4000):
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _tavily(spec, query, names):
    env_name = spec["api_key_env"] or TAVILY_ENV
    key = _key(env_name)
    if not key:
        raise SearchError("环境变量 %s 未设置" % env_name)
    base = _public_base(spec["base_url"] or TAVILY_URL)
    body = {"api_key": key, "query": query, "max_results": spec["max_results"]}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/search", data=data, headers={
        "Content-Type": "application/json", "User-Agent": "agentbot",
    }, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=spec["timeout_sec"])
        raw = resp.read(200000)
        resp.close()
    except urllib.error.HTTPError as exc:
        try: exc.close()
        except Exception: pass
        raise SearchError("搜索请求失败 HTTP %s" % exc.code)
    except Exception:
        raise SearchError("搜索请求失败")
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise SearchError("搜索结果不是 JSON")
    rows = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or not rows:
        return "没有搜到公开网页。"
    lines = []
    for item in rows[: spec["max_results"]]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        lines.append("%s\n%s\n%s" % (title, url, snippet))
    text = "\n\n".join(line for line in lines if line.strip()) or "没有搜到公开网页。"
    return _clip(_redact(text, key, names))


def _searx(spec, query, names):
    base = _public_base(spec["base_url"])
    key = _key(spec["api_key_env"]) if spec["api_key_env"] else ""
    params = {"q": query, "format": "json"}
    url = base + "/search?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "agentbot", "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=spec["timeout_sec"])
        raw = resp.read(200000)
        resp.close()
    except urllib.error.HTTPError as exc:
        try: exc.close()
        except Exception: pass
        raise SearchError("搜索请求失败 HTTP %s" % exc.code)
    except Exception:
        raise SearchError("搜索请求失败")
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise SearchError("搜索结果不是 JSON")
    rows = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or not rows:
        return "没有搜到公开网页。"
    lines = []
    for item in rows[: spec["max_results"]]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or "").strip()
        lines.append("%s\n%s\n%s" % (title, link, snippet))
    text = "\n\n".join(line for line in lines if line.strip()) or "没有搜到公开网页。"
    return _clip(_redact(text, key, names))


def run(cfg, args, names=()):
    spec = search_settings(cfg)
    if not spec["enabled"]:
        raise SearchError("搜索没有打开")
    query = str((args or {}).get("query") or (args or {}).get("q") or "").strip()
    if not query:
        raise SearchError("搜索需要 query")
    query = guard.redact(query, names or [])[:300]
    if spec["backend"] == "tavily":
        return _tavily(spec, query, names)
    if spec["backend"] == "searxng":
        return _searx(spec, query, names)
    raise SearchError("不认识的搜索后端")
