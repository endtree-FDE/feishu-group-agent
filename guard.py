# 隐私与内容策略：客户名脱敏（redact.txt）、敏感信息打码（手机号/证件号/邮箱/密钥/本地路径）；
# 话题红线不内置，只按配置 policy.redlines 执行（默认空 = 从不拦截）。agentbot 与 kb_sync 共用。
import os, re

_SENSITIVE = [(re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已隐藏]"),
              (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[证件号已隐藏]"),
              (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱已隐藏]"),
              (re.compile(r"(?i)(secret|token|password|passwd|api[_-]?key)\s*[:=]\s*\S+"), r"\1=[已隐藏]"),
              (re.compile(r"(?i)(?<![A-Za-z])[A-Z]:[\\/][^\s，。；、：）)》」\"'`]*"), "[本地路径已隐藏]")]

def load_names(path):
    if not path or not os.path.exists(path): return []
    out = []
    for l in open(path, encoding="utf-8-sig"):
        l = l.split("#")[0].strip()
        if l: out.append(l)
    return sorted(set(out), key=len, reverse=True)

def policy_rules(cfg, stage=None):
    """读取配置里的话题红线。新格式 policy.redlines=[{name,keywords,action,apply_to,reply}]；兼容旧键 taboo_keywords。默认空。
    stage: input / output / progress / memory / kb；规则没写 apply_to 时对所有阶段生效。"""
    rules = [r for r in ((cfg.get("policy") or {}).get("redlines") or []) if r.get("keywords")]
    if cfg.get("taboo_keywords"): rules.append({"name": "taboo_keywords", "keywords": list(cfg["taboo_keywords"]), "action": "refuse"})
    return [r for r in rules if stage is None or stage in (r.get("apply_to") or [stage])]

def policy_hit(text, rules):
    """返回第一条命中的规则（没有规则或没命中返回 None）。"""
    t = text or ""
    for r in rules or ():
        if any(k and k in t for k in r.get("keywords", ())): return r
    return None

def redact(text, names, placeholder="某客户"):
    for n in names:
        text = text.replace(n, placeholder)
    for rx, rep in _SENSITIVE:
        text = rx.sub(rep, text)
    return text

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
