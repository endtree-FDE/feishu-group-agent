# 资料电脑：把研究笔记目录和新闻目录（只取若干天内条目的摘要）打包成 kb_bundle_YYYYMMDD.zip，先过一遍隐私过滤。
# 用法: python3 make_bundle.py --out ./bundles [--research ./research] [--news ./news] [--redact redact.txt]
import argparse, os, re, sys, zipfile, hashlib, json, datetime, html
from email.utils import parsedate_to_datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import guard

ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--research", default="./research")
ap.add_argument("--news", default="./news"); ap.add_argument("--redact", default=""); ap.add_argument("--days", type=int, default=7)
A = ap.parse_args(); names = guard.load_names(A.redact)
EXT = {".md", ".csv", ".txt"}; MAXB = 200 * 1024
files, report, cands = {}, [], set()

def add(rel, text):
    cands.update(guard.candidate_names(text))
    text = guard.redact(text, names)  # 只做隐私脱敏，不按话题删段落
    files[rel] = text

for root, _, fs in os.walk(A.research):
    for f in fs:
        p = os.path.join(root, f); rel = os.path.relpath(p, A.research).replace(os.sep, "/")  # 与 kb 目录结构一致
        if os.path.splitext(f)[1].lower() in EXT and os.path.getsize(p) <= MAXB:
            add(rel, open(p, encoding="utf-8", errors="replace").read())

cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=A.days); news = []
for f in sorted(os.listdir(A.news)) if os.path.isdir(A.news) else []:
    if not f.endswith(".xml"): continue
    src = os.path.splitext(f)[0]; x = open(os.path.join(A.news, f), encoding="utf-8", errors="replace").read()
    for it in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", x, re.S)[:200]:
        t = re.search(r"<title[^>]*>(.*?)</title>", it, re.S); d = re.search(r"<(?:pubDate|published|updated|dc:date)>(.*?)<", it, re.S)
        l = re.search(r"<link[^>]*?(?:href=\"([^\"]+)\"[^>]*/?>|>(.*?)</link>)", it, re.S)
        if not t: continue
        try:
            dt = parsedate_to_datetime(d.group(1).strip()) if d else None
        except Exception:
            try: dt = datetime.datetime.fromisoformat(d.group(1).strip().replace("Z", "+00:00"))
            except Exception: dt = None
        if dt and dt.tzinfo is None: dt = dt.replace(tzinfo=datetime.timezone.utc)
        if not dt or dt < cut: continue
        title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", t.group(1))).strip()
        link = (l.group(1) or l.group(2) or "").strip() if l else ""
        news.append((dt, src, title, link))
news.sort(reverse=True)
if news:
    body = "# 近 %d 天行业新闻标题（自动汇总，%s）\n\n" % (A.days, datetime.date.today()) + "\n".join(
        "- %s [%s] %s %s" % (dt.astimezone().strftime("%m-%d"), s, t, l) for dt, s, t, l in news[:400])
    add("news/news_digest.md", body)

os.makedirs(A.out, exist_ok=True); stamp = datetime.date.today().strftime("%Y%m%d")
zp = os.path.join(A.out, "kb_bundle_%s.zip" % stamp)
manifest = {"created": datetime.datetime.now().isoformat(timespec="seconds"), "files": {}}
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for rel, text in sorted(files.items()):
        b = text.encode("utf-8"); z.writestr(rel, b); manifest["files"][rel] = hashlib.sha256(b).hexdigest()
    z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    z.writestr("REPORT.md", "# 打包报告\n\n" + ("\n".join("- " + r for r in report) or "- 无") +
               "\n\n# 客户名候选（请用户确认后写入 redact.txt；本工具不会自动脱敏这些名字）\n\n" + "\n".join("- " + c for c in sorted(cands)))
print(zp, len(files), "files;", len(news), "news items;", len(cands), "candidates")
