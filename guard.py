# 隐私与内容策略：客户名脱敏（redact.txt）、敏感信息打码（手机号/证件号/邮箱/密钥/本地路径）；
# 话题红线不内置，只按配置 policy.redlines 执行（默认空 = 从不拦截）。agentbot 与 kb_sync 共用。
import os, re

_SENSITIVE = [(re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已隐藏]"),
              (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[证件号已隐藏]"),
              (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱已隐藏]"),
              (re.compile(r"(?i)(secret|token|password|passwd|api[_-]?key)\s*[:=]\s*\S+"), r"\1=[已隐藏]"),
              (re.compile(r"(?i)(?<![A-Za-z])[A-Z]:[\\/][^\s，。；、：）)》」\"'`]*"), "[本地路径已隐藏]")]
# 零宽、软连字符、双向文字控制符。先剥掉再匹配，避免在客户名中间插一个看不见的字就绕过。
_INVIS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u202a-\u202e\u2066-\u2069]")
_FLEX = {}

def normalize(text):
    """去掉零宽和双向控制符，并把全角英文字母、数字折成半角。中文标点保持原样。"""
    out = []
    for ch in _INVIS.sub("", text or ""):
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19:
            out.append(chr(o - 0xFF10 + ord("0")))
        elif 0xFF21 <= o <= 0xFF3A:
            out.append(chr(o - 0xFF21 + ord("A")))
        elif 0xFF41 <= o <= 0xFF5A:
            out.append(chr(o - 0xFF41 + ord("a")))
        else:
            out.append(ch)
    return "".join(out)

def load_names(path):
    if not path or not os.path.exists(path): return []
    out = []
    for l in open(path, encoding="utf-8-sig"):
        l = l.split("#")[0].strip()
        if not l: continue
        # 一行可以写「中文名|拼音或别名」，两边都隐藏。没有竖线时和以前一样，整行是一个名字。
        for piece in re.split(r"[|｜]", l):
            piece = piece.strip()
            if piece: out.append(piece)
    return sorted(set(out), key=len, reverse=True)

def policy_rules(cfg, stage=None):
    """读取配置里的话题红线。新格式 policy.redlines=[{name,keywords,action,apply_to,reply}]；兼容旧键 taboo_keywords。默认空。
    stage: input / output / progress / memory / kb；规则没写 apply_to 时对所有阶段生效。"""
    rules = [r for r in ((cfg.get("policy") or {}).get("redlines") or []) if r.get("keywords")]
    if cfg.get("taboo_keywords"): rules.append({"name": "taboo_keywords", "keywords": list(cfg["taboo_keywords"]), "action": "refuse"})
    return [r for r in rules if stage is None or stage in (r.get("apply_to") or [stage])]

def policy_hit(text, rules):
    """返回第一条命中的规则（没有规则或没命中返回 None）。关键词按去掉零宽、折全角字母数字之后的文字匹配。"""
    t = normalize(text or "")
    for r in rules or ():
        if any(k and normalize(k) in t for k in r.get("keywords", ())): return r
    return None

def _flex_name(name):
    """6 个字及以上的名字，允许字与字之间插入少量空格或分隔符（拆字）。短名字不开，避免误伤。"""
    rx = _FLEX.get(name)
    if rx is None:
        sep = r"[\s\u3000._·•]{0,2}"
        rx = re.compile(sep.join(re.escape(ch) for ch in name))
        _FLEX[name] = rx
    return rx

def redact(text, names, placeholder="某客户"):
    text = normalize(text)
    for n in names or ():
        n2 = normalize(n)
        if not n2: continue
        if n2.isascii():
            text = re.compile(re.escape(n2), re.I).sub(placeholder, text)
        else:
            text = text.replace(n2, placeholder)
        if len(n2) >= 6:
            text = _flex_name(n2).sub(placeholder, text)
    for rx, rep in _SENSITIVE:
        text = rx.sub(rep, text)
    return text

def neutralize(text):
    """不可信正文里的工具标记改成看不出的样子，避免模型把文档或网页里的指令原样抄成一次调用。"""
    t = text or ""
    for a, b in (("<<TOOL>>", "〈TOOL〉"), ("<<MEM>>", "〈MEM〉"), ("<<FAKE_TOOL>>", "〈FAKE_TOOL〉"), ("<<END>>", "〈END〉")):
        t = t.replace(a, b)
    return t

def embeds_secret(text, names=()):
    """网址或搜索词里如果藏了客户名、手机号、邮箱、密钥，返回 True。"""
    raw = text or ""
    if not raw: return False
    cleaned = redact(raw, names, placeholder="某客户")
    if cleaned != normalize(raw): return True
    return False

def safe_path(root, rel):
    """把相对路径解析到 root 里面。绝对路径、盘符、.. 都返回 None。"""
    if rel is None: return None
    rel = str(rel).replace("\\", "/").strip()
    if not rel or "\x00" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel): return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts): return None
    root_r = os.path.realpath(root)
    dest = os.path.realpath(os.path.join(root_r, *parts))
    try:
        if os.path.commonpath([root_r, dest]) != root_r: return None
    except ValueError:
        return None
    return dest

def drop_policy_paragraphs(text, rules):
    """删掉命中红线的段落；没有规则时原样返回。"""
    if not rules: return text, 0
    kept, dropped = [], 0
    for para in re.split(r"\n\s*\n", text):
        if policy_hit(para, rules): dropped += 1
        else: kept.append(para)
    return "\n\n".join(kept), dropped

_CAND = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9·]{2,16}(?:（[\u4e00-\u9fa5]{2,6}）)?(?:有限公司|股份有限公司|集团|公司|工作室|事务所|研究院)")
_STOP = re.compile(r"[的了和与在是有为从到把被对等及或都也就要不归给能可让需将挂这那该某些全已未再后前新人号成个注册转约定导出存支持普通方向夫妻双人小团队广告工具国内同一主办执行策划手环抖音]|XXX")
def candidate_names(text):
    """粗筛可能的客户/机构名，只作为候选供用户确认，不会自动加入脱敏清单。"""
    c = set(m.group(0) for m in _CAND.finditer(text) if not _STOP.search(re.sub(r"(有限公司|股份有限公司|集团|公司|工作室|事务所|研究院)$", "", m.group(0))) and len(m.group(0)) >= 5)
    for m in re.finditer(r"客户[：:「“\"]\s*([\u4e00-\u9fa5A-Za-z0-9]{2,12})", text): c.add(m.group(1))
    return c
