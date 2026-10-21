#!/usr/bin/env python3
# 公开前复查：扫描已提交的历史，查找账号、群号、open_id、密钥和客户名。
# 只打印打码后的种类、路径和提交号。不改写历史，不调用 filter-repo。
import os, re, subprocess, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utf8io  # noqa: F401

ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, encoding="utf-8", errors="replace").strip()

# 这些是仓库里故意留下的占位和虚构例子，不算泄漏。
FIXTURE_PHONE = "1" + "3812345678"
EMAIL_OK = {
    "example.com", "example.org", "cursor.com", "github.com",
    "users.noreply.github.com", "larksuite.com",
}
# 模式拆开写，避免扫描脚本自己被密钥规则命中。
PATTERNS = {
    "open_id": r"ou_[A-Za-z0-9]{10,}",
    "union_id": r"on_[A-Za-z0-9]{10,}",
    "chat_id": r"oc_[A-Za-z0-9]{16,}",
    "app_id": r"cli_[A-Za-z0-9]{8,}",
    "message_id": r"om_[A-Za-z0-9]{10,}",
    "openai_key": r"sk-" + r"[A-Za-z0-9]{16,}",
    "aws_key": r"AK" + r"IA[0-9A-Z]{16}",
    "private_key": r"-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----",
    "phone": r"(^|[^0-9])(1[3-9][0-9]{9})([^0-9]|$)",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "secret_assign": r"(app[_-]?secret|api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*\S+",
}

def git(*args):
    p = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr

def mask(value):
    v = value.strip()
    if len(v) <= 8:
        return v[:2] + "***"
    return v[:3] + "…" + v[-2:] + " len=%d" % len(v)

def fixture(kind, value, path):
    if "REPLACE_" in value or "某某" in value or "placeholder" in value.lower() or "not_a_real" in value:
        return True
    norm = path.replace("\\", "/")
    if kind == "phone":
        return value == FIXTURE_PHONE
    if kind == "email":
        dom = value.split("@", 1)[-1].lower()
        return dom in EMAIL_OK
    if kind == "company":
        # 扫描器自己的正则，以及脱敏规则里的公司后缀，不是客户名。
        return "某某" in value or norm.endswith("guard.py") or norm.endswith("checks/history_scan.py")
    if kind in ("open_id", "union_id", "chat_id", "app_id", "message_id"):
        body = value.split("_", 1)[-1]
        # 飞书真实编号是十六进制。带 g–z 的是测试里起的名字，例如 om_chainfile1。
        if re.search(r"[g-zG-Z]", body):
            return True
    if kind == "secret_assign":
        # parts.password: 是 if 语句的冒号，不是赋值。匹配结果可能不含前面的点。
        if "password:" in value and "=" not in value.split("password", 1)[-1][:3]:
            return True
        if norm.endswith("checks/history_scan.py"):
            return True
        tail = re.split(r"[:=]", value, maxsplit=1)[-1].strip().strip("\"'")
        tail = re.sub(r"[\"')\]},]+$", "", tail)
        if tail.lower() in {"false", "true", "none", "null", "", "abc"}:
            return True
        # 日志打码留下的星号或「已隐藏」，不是写进仓库的密钥。
        if tail and set(tail) <= {"*"}:
            return True
        if tail in {"[已隐藏]", "已隐藏"}:
            return True
        if tail.startswith("REPLACE") or "environ" in tail or tail == "supersecretvalue123":
            return True
        if "api-key=" in value.lower() and "sk-" not in value.lower():
            return True
    return False

def add(found, kind, value, path, sha):
    if fixture(kind, value, path):
        return
    found[(kind, mask(value), path)].add(sha[:10])

def grep_commit(sha, pattern):
    # 树对象放在 -- 前面。模式用 -e，避免 -----BEGIN 被当成选项。
    # -- SHA 会被当成路径，扫不到那一次提交。
    code, out, err = git("grep", "-I", "-n", "-E", "-e", pattern, sha, "--")
    if code not in (0, 1):
        print(err.strip() or "git grep 失败")
        return None
    return out.splitlines()

def main():
    code, out, err = git("rev-list", "--all")
    if code != 0:
        print(err.strip() or "无法读取提交历史")
        return 2
    commits = [s for s in out.split() if s]
    found = defaultdict(set)
    company_rx = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,30}(?:有限公司|股份有限公司|集团有限公司)")
    for sha in commits:
        for kind, pat in PATTERNS.items():
            lines = grep_commit(sha, pat)
            if lines is None:
                return 2
            for line in lines:
                parts = line.split(":", 3)
                if len(parts) < 4:
                    continue
                _sha, path, _no, text = parts
                if kind == "phone":
                    m = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", text)
                    if m:
                        add(found, kind, m.group(1), path, sha)
                elif kind == "email":
                    for m in re.finditer(PATTERNS["email"], text):
                        add(found, kind, m.group(0), path, sha)
                else:
                    m = re.search(pat, text)
                    if m:
                        add(found, kind, m.group(0), path, sha)
        lines = grep_commit(sha, "有限公司|股份有限公司|集团有限公司")
        if lines is None:
            return 2
        for line in lines:
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            _sha, path, _no, text = parts
            for m in company_rx.finditer(text):
                add(found, "company", m.group(0), path, sha)
    # 提交说明里的同一类字符串。作者邮箱单独统计，不因此失败。
    code, msg, err = git("log", "--all", "--format=%H%x09%s")
    if code != 0:
        print(err.strip() or "无法读取提交说明")
        return 2
    for line in msg.splitlines():
        sha, _, subject = line.partition("\t")
        for kind, pat in PATTERNS.items():
            for m in re.finditer(pat, subject):
                val = m.group(1) if kind == "phone" and m.lastindex else m.group(0)
                add(found, kind, val, "COMMIT_MESSAGE", sha)
    code, authors, err = git("log", "--all", "--format=%ae")
    domains = defaultdict(int)
    if code == 0:
        for email in authors.splitlines():
            dom = email.split("@", 1)[-1].lower() if "@" in email else "?"
            domains[dom] += 1
    print("history-scan commits=%d" % len(commits))
    print("authors-by-domain " + ", ".join("%s=%d" % (d, n) for d, n in sorted(domains.items())))
    if not found:
        print("file-review=0")
        print("OK 只报告，未改写历史")
        return 0
    print("file-review=%d" % len(found))
    for (kind, masked, path), shas in sorted(found.items()):
        sample = ",".join(sorted(shas)[:6])
        print("REVIEW %s path=%s value=%s commits=%s" % (kind, path, masked, sample))
    return 1

if __name__ == "__main__":
    sys.exit(main())
