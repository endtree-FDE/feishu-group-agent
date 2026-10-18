# 运行电脑上的知识库同步。默认只进 staging，不碰正式 kb；--apply 才替换（会先备份成 kb_prev）。
# 用法:
#   python kb_sync.py agents/example.json --inbox          处理 kb_inbox/*.zip -> kb_staging/<日期>/（校验 + 二次过滤 + 报告）
#   python kb_sync.py agents/example.json --pull-docs      拉取配置 kb_sync.docs_tokens 里的飞书文档到 staging（bot 身份，只读）
#   python kb_sync.py agents/example.json --apply <日期>   把 staging/<日期> 合并进 kb（原子替换，保留 kb_prev）
import sys, os, json, zipfile, hashlib, shutil, datetime, subprocess, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); import guard
CFG = json.load(open(sys.argv[1], encoding="utf-8-sig")); A = sys.argv[2:]
BASE = os.path.abspath(CFG.get("base_dir") or os.path.dirname(os.path.abspath(sys.argv[1])))
KB = os.path.join(BASE, CFG.get("kb_dir", "kb")); INBOX = os.path.join(BASE, "kb_inbox"); STG = os.path.join(BASE, "kb_staging")
NAMES = guard.load_names(os.path.join(BASE, CFG.get("redact_file", "redact.txt"))); TODAY = datetime.date.today().strftime("%Y%m%d")
for d in (INBOX, STG): os.makedirs(d, exist_ok=True)
def lark_exe(): return [os.path.join(os.environ["APPDATA"], "npm", "lark-cli.cmd")] if os.name == "nt" else ["lark-cli"]
def put(dst_root, rel, text, rep):
    cands = guard.candidate_names(text); text, dropped = guard.drop_policy_paragraphs(text, guard.policy_rules(CFG, "kb"))
    text = guard.redact(text, NAMES)
    p = guard.safe_path(dst_root, rel)
    if not p:
        rep.append("- 跳过可疑路径 " + str(rel)); return set()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)
    if dropped: rep.append("- %s：按 policy.redlines 删除段落 %d 段" % (rel, dropped))
    return cands
def write_report(dst, rep, cands, extra=""):
    old = os.path.join(dst, "_REPORT.md"); prev = open(old, encoding="utf-8").read() if os.path.exists(old) else ""
    open(old, "w", encoding="utf-8").write(prev + "\n## %s\n%s\n" % (datetime.datetime.now().strftime("%H:%M"), extra) + ("\n".join(rep) or "- 无红线删除（未配置 policy.redlines 或无命中）") +
        "\n\n### 客户名候选（需用户确认后写入 redact.txt）\n" + "\n".join("- " + c for c in sorted(cands)) + "\n")
def diff_list(dst):
    lines = []
    for root, _, fs in os.walk(dst):
        for f in fs:
            if f.startswith("_"): continue
            rel = os.path.relpath(os.path.join(root, f), dst); old = os.path.join(KB, rel)
            if not os.path.exists(old): lines.append("- 新增 " + rel)
            elif open(old, "rb").read() != open(os.path.join(root, f), "rb").read(): lines.append("- 更新 " + rel)
    return "\n".join(lines) or "- 无变化"

if "--inbox" in A:
    dst = os.path.join(STG, TODAY); rep, cands = [], set(); n = 0
    for z in sorted(f for f in os.listdir(INBOX) if f.endswith(".zip")):
        zp = os.path.join(INBOX, z)
        with zipfile.ZipFile(zp) as zf:
            man = json.loads(zf.read("MANIFEST.json"))
            for rel, sha in man["files"].items():
                if guard.safe_path(dst, rel) is None: rep.append("- 跳过可疑路径 " + rel); continue
                b = zf.read(rel)
                if hashlib.sha256(b).hexdigest() != sha: rep.append("- 校验失败 " + rel); continue
                cands |= put(dst, rel, b.decode("utf-8", "replace"), rep); n += 1
        os.makedirs(os.path.join(INBOX, "done"), exist_ok=True); shutil.move(zp, os.path.join(INBOX, "done", z))
    if n: write_report(dst, rep, cands, "收件箱导入 %d 个文件\n\n### 与正式 kb 的差异\n%s\n" % (n, diff_list(dst)))
    print("inbox ->", dst if n else "(无新包)", n)

if "--pull-docs" in A:
    dst = os.path.join(STG, TODAY); rep, cands = [], set(); n = 0
    for tok in CFG.get("kb_sync", {}).get("docs_tokens", []):
        r = subprocess.run(lark_exe() + ["docs", "+fetch", "--doc", tok, "--doc-format", "markdown", "--profile", CFG["profile"], "--as", "bot"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        try: j = json.loads(r.stdout[r.stdout.find("{"):])
        except Exception: j = {"ok": False, "error": {"message": (r.stdout + r.stderr)[:200]}}
        if not j.get("ok"): rep.append("- 拉取失败 %s：%s" % (tok, str((j.get("error") or {}).get("message"))[:120])); continue
        d = j.get("data") or {}; title = re.sub(r'[\\/:*?"<>|]', "_", str(d.get("title") or tok))[:60]
        cands |= put(dst, "feishu_docs/%s.md" % title, d.get("markdown") or d.get("content") or "", rep); n += 1
    write_report(dst, rep, cands, "飞书文档拉取 %d 份" % n); print("pull-docs ->", dst, n)

if "--apply" in A:
    stamp = A[A.index("--apply") + 1]; src = os.path.join(STG, stamp); assert os.path.isdir(src), src
    new = KB + "_new"; shutil.rmtree(new, ignore_errors=True); shutil.copytree(KB, new, ignore=shutil.ignore_patterns(".attach"))
    for root, _, fs in os.walk(src):
        for f in fs:
            if f.startswith("_"): continue
            rel = os.path.relpath(os.path.join(root, f), src)
            dest = guard.safe_path(new, rel)
            if not dest: print("skip", rel); continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(root, f), dest)
    prev = KB + "_prev"; shutil.rmtree(prev, ignore_errors=True); os.rename(KB, prev); os.rename(new, KB)
    print("applied", stamp, "-> kb（旧版本在 kb_prev）")
