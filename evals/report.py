# 报告只保留检查结果和短摘录。不写提示词全文，不写配置里的编号。
import json, os


def diff_baseline(current, baseline):
    """对照已保存的通过/失败。跳过的检查不算回归。"""
    old = {}
    for case in (baseline or {}).get("cases") or []:
        checks = {}
        for item in case.get("checks") or []:
            checks[item.get("id")] = item
        old[case.get("id")] = {"passed": case.get("passed"), "checks": checks}
    new = {}
    for case in (current or {}).get("cases") or []:
        new[case.get("id")] = case
    regressions, fixes, added, removed = [], [], [], []
    for cid, case in new.items():
        prev = old.get(cid)
        if prev is None:
            added.append(cid)
            continue
        for item in case.get("checks") or []:
            before = (prev.get("checks") or {}).get(item.get("id"))
            if before is None:
                if not item.get("pass") and not item.get("skipped"):
                    regressions.append({"id": cid, "check": item.get("id"), "note": "新检查未通过"})
                continue
            was = bool(before.get("pass")) and not before.get("skipped")
            now = bool(item.get("pass")) and not item.get("skipped")
            if was and not now:
                regressions.append({"id": cid, "check": item.get("id"), "note": "以前通过，现在没有"})
            elif (not was) and now and not before.get("skipped"):
                fixes.append({"id": cid, "check": item.get("id")})
    for cid in old:
        if cid not in new:
            removed.append(cid)
    return {"regressions": regressions, "fixes": fixes, "added": added, "removed": removed}


def _line(ok, text):
    return ("- 通过 " if ok else "- 失败 ") + text


def to_markdown(report, diff):
    lines = []
    lines.append("# 回答质量回归")
    lines.append("")
    lines.append("套件 %s，模型 %s，模式 %s。共 %d 条，通过 %d，失败 %d。" % (
        report.get("suite") or "",
        report.get("provider") or "",
        report.get("mode") or "",
        report.get("total") or 0,
        report.get("passed") or 0,
        report.get("failed") or 0,
    ))
    lines.append("")
    lines.append("这次没有连接飞书，没有主动发消息，也没有占用机器人的单实例锁。渠道是内存适配器。")
    lines.append("")
    if report.get("lock_note"):
        lines.append(report["lock_note"])
        lines.append("")
    if diff is None:
        lines.append("没有对照基线。")
    else:
        regs = diff.get("regressions") or []
        lines.append("相对基线：回归 %d，修复 %d，新增用例 %d，移除用例 %d。" % (
            len(regs), len(diff.get("fixes") or []), len(diff.get("added") or []), len(diff.get("removed") or [])))
        for item in regs:
            lines.append("- 回归 `%s` / `%s`：%s" % (item.get("id"), item.get("check"), item.get("note") or ""))
        for cid in diff.get("added") or []:
            lines.append("- 新增用例 `%s`" % cid)
        for cid in diff.get("removed") or []:
            lines.append("- 基线里有、这次没有跑 `%s`" % cid)
    lines.append("")
    for case in report.get("cases") or []:
        lines.append("## %s" % case.get("id"))
        lines.append("")
        lines.append(case.get("title") or "")
        lines.append("")
        lines.append("结果：%s。" % ("通过" if case.get("passed") else "失败"))
        for item in case.get("checks") or []:
            if item.get("skipped"):
                lines.append("- 跳过 `%s`：%s" % (item.get("id"), item.get("detail") or ""))
            else:
                lines.append(_line(item.get("pass"), "`%s` %s" % (item.get("id"), item.get("detail") or "")))
            excerpt = item.get("reply_excerpt")
            if excerpt and not item.get("pass"):
                lines.append("")
                lines.append("回复摘录：")
                lines.append("")
                lines.append("```text")
                lines.append(excerpt)
                lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def baseline_view(report):
    """基线只存通过与否，不存回复正文，避免把某次模型措辞写进仓库。"""
    cases = []
    for case in report.get("cases") or []:
        checks = []
        for item in case.get("checks") or []:
            checks.append({"id": item.get("id"), "pass": bool(item.get("pass")), "skipped": bool(item.get("skipped"))})
        cases.append({"id": case.get("id"), "passed": bool(case.get("passed")), "checks": checks})
    return {
        "schema_version": 1,
        "suite": report.get("suite"),
        "provider": report.get("provider"),
        "mode": report.get("mode"),
        "cases": cases,
    }


def write_reports(directory, report, diff):
    os.makedirs(directory, exist_ok=True)
    json_path = os.path.join(directory, "report.json")
    md_path = os.path.join(directory, "report.md")
    payload = dict(report)
    payload["diff"] = diff
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(report, diff))
    return json_path, md_path
