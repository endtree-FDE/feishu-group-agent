#!/usr/bin/env python3
# 回答质量回归。用内存渠道把用例送进真正的回答流程。
# 不连接飞书，不发消息，也不拿机器人的单实例锁。
# 用法见 docs/回归测试.md。
import argparse, json, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import utf8io  # noqa: E402,F401  管道改为 UTF-8。PYTHONUTF8=0 时中文「通过」否则写不进管道，调用方读不到分数。
import guard  # noqa: E402
import grade  # noqa: E402
import report  # noqa: E402
import world  # noqa: E402

CHAT = "chat_eval"
P2P = "p2p_eval"
BOT = "bot_eval"
DEFAULT_REDACT = ["星河某某公司"]
DROP_ENV = (
    "AGENTBOT_FAKE_AGENT", "AGENTBOT_FAKE_SCRIPT", "AGENTBOT_FAKE_DUMP_PROMPT",
    "AGENTBOT_AGENT_CMD", "AGENTBOT_DRY_LIVE_WRITES", "AGENTBOT_FAKE_LARK",
    "AGENTBOT_FAKE_LARK_BY_ID", "AGENTBOT_FAKE_HOLD", "AGENTBOT_CATCHUP",
    "AGENTBOT_MEMORY_FIXTURE", "AGENTBOT_MEMORY_TRACE", "AGENTBOT_EVAL_PROMPT_LOG",
    "AGENTBOT_EVAL_NO_LARK", "AGENTBOT_APP_LOCK_DIR",
)


def subst(obj, mapping):
    if isinstance(obj, str):
        for key, val in mapping.items():
            obj = obj.replace("{{%s}}" % key, val)
        return obj
    if isinstance(obj, list):
        return [subst(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: subst(v, mapping) for k, v in obj.items()}
    return obj


def origin_base(cfg, config_path):
    """配置里的 base_dir。写成 . 且文件在 agents/ 下时，指仓库或机器人根目录。"""
    raw = cfg.get("base_dir")
    cfg_dir = os.path.dirname(os.path.abspath(config_path))
    if not raw or str(raw).strip() in (".", "./"):
        if os.path.basename(cfg_dir).lower() == "agents":
            return os.path.dirname(cfg_dir)
        return cfg_dir
    if os.path.isabs(str(raw)):
        return str(raw)
    return os.path.abspath(os.path.join(cfg_dir, str(raw)))


def _scrub(obj):
    """配置副本里去掉密钥样子的值。变量名留着，真正的密钥仍在环境变量里。"""
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            low = str(key).lower()
            if low in ("api_key", "app_secret", "secret", "password", "access_token"):
                continue
            out[key] = _scrub(val)
        return out
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    if isinstance(obj, str):
        return guard.redact(obj, ())
    return obj


def sandbox_config(src, origin, skills_dir):
    cfg = _scrub(json.loads(json.dumps(src, ensure_ascii=False)))
    cfg["channel"] = "memory"
    cfg["base_dir"] = origin  # 调用方会改成沙箱绝对路径
    cfg["kb_dir"] = "kb"
    cfg["dry_run_live_reads"] = False
    cfg["bot_open_id"] = BOT
    cfg["chat_ids"] = [CHAT]
    cfg["profile"] = "eval_profile"
    cfg["redact_file"] = "redact.txt"
    cfg.pop("conflict_pidfiles", None)
    chats = cfg.get("chats") if isinstance(cfg.get("chats"), dict) else {}
    chats["auto_join"] = False
    cfg["chats"] = chats
    progress = cfg.get("progress") if isinstance(cfg.get("progress"), dict) else {}
    progress["enabled"] = False
    cfg["progress"] = progress
    for key in ("alerts", "digest"):
        block = cfg.get(key) if isinstance(cfg.get(key), dict) else {}
        block["enabled"] = False
        cfg[key] = block
    catchup = cfg.get("catchup") if isinstance(cfg.get("catchup"), dict) else {}
    catchup["enabled"] = False
    cfg["catchup"] = catchup
    rate = cfg.get("rate_limit") if isinstance(cfg.get("rate_limit"), dict) else {}
    rate["min_gap_per_user"] = 0
    rate["max_per_hour"] = 10000
    rate["max_commands_per_min"] = 10000
    cfg["rate_limit"] = rate
    feat = cfg.get("features") if isinstance(cfg.get("features"), dict) else {}
    for key in ("p2p", "tools", "attachments", "vision", "memory"):
        feat[key] = True
    cfg["features"] = feat
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    try:
        rounds = int(agent.get("max_tool_rounds", 4))
    except (TypeError, ValueError):
        rounds = 4
    agent["max_tool_rounds"] = max(4, rounds)
    cfg["agent"] = agent
    skills = cfg.get("skills") if isinstance(cfg.get("skills"), dict) else {}
    skills["dir"] = skills_dir
    if "enabled" not in skills:
        skills["enabled"] = False
    cfg["skills"] = skills
    p2p = cfg.get("p2p") if isinstance(cfg.get("p2p"), dict) else {}
    p2p["allow_open_ids"] = ["user_member"]
    p2p["open_to_all"] = False
    p2p["allow_group_members"] = False
    cfg["p2p"] = p2p
    write = cfg.get("write") if isinstance(cfg.get("write"), dict) else {}
    write["enabled"] = True if "enabled" not in write else write["enabled"]
    write["allowed_users"] = {"user_admin": "小李"}
    resources = write.get("resources") if isinstance(write.get("resources"), dict) else {}
    resources.update({
        "folder_token": "fld_eval",
        "docs_append_tokens": [],
        "calendar_id": "cal_eval",
        "base_token": "bsc_eval",
        "table_id": "tbl_eval",
    })
    write["resources"] = resources
    cfg["write"] = write
    tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    base_read = tools.get("base_read") if isinstance(tools.get("base_read"), dict) else {}
    base_read["base_token"] = "bsc_eval"
    base_read["table_id"] = "tbl_eval"
    tools["base_read"] = base_read
    cal = tools.get("calendar_read") if isinstance(tools.get("calendar_read"), dict) else {}
    cal["calendar_id"] = "cal_eval"
    tools["calendar_read"] = cal
    docs = tools.get("docs_read") if isinstance(tools.get("docs_read"), dict) else {}
    docs["allow_tokens"] = []
    tools["docs_read"] = docs
    cfg["tools"] = tools
    sync = cfg.get("kb_sync") if isinstance(cfg.get("kb_sync"), dict) else {}
    sync["docs_tokens"] = []
    cfg["kb_sync"] = sync
    return cfg


def apply_setup(cfg, setup):
    setup = setup or {}
    if isinstance(setup.get("p2p"), dict):
        cfg.setdefault("p2p", {}).update(setup["p2p"])
    if isinstance(setup.get("write"), dict):
        write = cfg.setdefault("write", {})
        for key, val in setup["write"].items():
            if key == "resources" and isinstance(val, dict):
                write.setdefault("resources", {}).update(val)
            else:
                write[key] = val
    if isinstance(setup.get("skills"), dict):
        skills = cfg.setdefault("skills", {})
        for key, val in setup["skills"].items():
            if key == "dir":
                continue
            skills[key] = val
    if isinstance(setup.get("features"), dict):
        cfg.setdefault("features", {}).update(setup["features"])
    return cfg


def resolve_skills(src, origin):
    raw = ((src.get("skills") or {}).get("dir") or "skills")
    if os.path.isabs(str(raw)) and os.path.isdir(raw):
        return os.path.abspath(raw)
    cand = os.path.join(origin, str(raw))
    if os.path.isdir(cand):
        return os.path.abspath(cand)
    return os.path.join(ROOT, "skills")


def load_suite(path):
    data = json.load(open(path, encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise SystemExit("用例文件须是 JSON 对象")
    cases = list(data.get("cases") or [])
    base = os.path.dirname(os.path.abspath(path))
    for rel in data.get("includes") or []:
        extra = load_suite(os.path.join(base, rel))
        cases.extend(extra.get("cases") or [])
    data["cases"] = cases
    return data


def select_cases(suite, tag, ids):
    want = set(ids or [])
    out = []
    for case in suite.get("cases") or []:
        if want and case.get("id") not in want:
            continue
        if tag and tag not in (case.get("tags") or []):
            continue
        out.append(case)
    return out


def write_redact(path, names):
    lines = []
    for name in names or []:
        text = str(name).strip()
        if text and text not in lines:
            lines.append(text)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def build_event(turn, bot_name):
    mid = str(turn.get("message_id") or "")
    chat_type = turn.get("chat_type") or "group"
    chat_id = P2P if chat_type == "p2p" else CHAT
    text = str(turn.get("text") or "")
    ev = {
        "message_id": mid,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": turn.get("message_type") or "text",
        "sender_id": turn.get("sender") or "user_member",
        "sender_type": "user",
        "content": text,
        "create_time": str(int(time.time() * 1000)),
    }
    if turn.get("root_id"):
        ev["root_id"] = turn["root_id"]
    if chat_type != "p2p":
        ev["content"] = "@_user_1 " + text
        ev["mentions"] = [{"id": BOT, "key": "@_user_1", "name": bot_name}]
    return ev


def build_fixture(turn, setup, files_dir):
    data = {"caps": list(setup.get("caps") or []), "members": {}, "messages": [], "files": {}}
    members = setup.get("members") if isinstance(setup.get("members"), dict) else {}
    for cid, ids in members.items():
        if isinstance(ids, list):
            data["members"][str(cid)] = [str(x) for x in ids]
    now = str(int(time.time() * 1000))
    for item in setup.get("history") or []:
        if not isinstance(item, dict):
            continue
        data["messages"].append({
            "message_id": str(item.get("id") or "hist_eval"),
            "chat_id": str(item.get("chat_id") or CHAT),
            "chat_type": "group",
            "message_type": "text",
            "sender_id": str(item.get("sender") or "user_member"),
            "sender_type": "user",
            "content": str(item.get("text") or ""),
            "create_time": now,
        })
    spec = turn.get("file")
    if isinstance(spec, dict):
        path = world.materialize(spec, files_dir)
        mid = str(turn.get("message_id") or "")
        name = os.path.basename(path)
        kind = str(spec.get("kind") or "txt").lower()
        mtype = "image" if kind == "png" else "file"
        turn["message_type"] = turn.get("message_type") or mtype
        data["files"][mid] = [{"name": name, "path": path}]
        content = {"file_name": name, "file_key": "file_eval"}
        if kind == "png":
            content = {"file_name": name, "image_key": "img_eval"}
        data["messages"].append({
            "message_id": mid,
            "chat_id": P2P if (turn.get("chat_type") == "p2p") else CHAT,
            "chat_type": turn.get("chat_type") or "group",
            "message_type": turn.get("message_type") or mtype,
            "sender_id": turn.get("sender") or "user_member",
            "sender_type": "user",
            "content": json.dumps(content, ensure_ascii=False),
            "create_time": now,
        })
    return data


def read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def turn_reply(trace_path):
    parts = []
    pushes = []
    for row in read_jsonl(trace_path):
        if row.get("op") == "send_text":
            parts.append(str(row.get("text") or ""))
        elif row.get("op") == "push_text":
            pushes.append(row)
    return "\n".join(parts), pushes


def turn_notes(path):
    prompts, stages = [], []
    for row in read_jsonl(path):
        kind = row.get("kind")
        text = str(row.get("text") or "")
        if kind == "prompt":
            prompts.append(text)
        elif kind == "stage":
            stages.append(text)
    return "\n\n".join(prompts), "\n".join(stages)


def lock_mtime(base):
    path = os.path.join(base, "state", "bot.lock")
    if not os.path.isfile(path):
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def sandbox_locked(case_dir, app_dir):
    hits = []
    bot_lock = os.path.join(case_dir, "state", "bot.lock")
    if os.path.isfile(bot_lock):
        hits.append(bot_lock)
    if os.path.isdir(app_dir):
        for name in os.listdir(app_dir):
            hits.append(os.path.join(app_dir, name))
    return "；".join(hits)


def run_turn(turn, cfg_path, case_dir, env, timeout):
    events = os.path.join(case_dir, turn["id"] + ".jsonl")
    with open(events, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event": turn["event"]}, ensure_ascii=False) + "\n")
    trace = os.path.join(case_dir, turn["id"] + ".trace.jsonl")
    notes = os.path.join(case_dir, turn["id"] + ".prompt.jsonl")
    for path in (trace, notes):
        if os.path.isfile(path):
            os.remove(path)
    child = dict(env)
    child["AGENTBOT_MEMORY_TRACE"] = trace
    child["AGENTBOT_EVAL_PROMPT_LOG"] = notes
    cmd = [sys.executable, os.path.join(ROOT, "agentbot.py"), cfg_path, "--dry-run", events]
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, env=child, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        code = proc.returncode
        log = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        code = 124
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        log = out + err + "\n超时\n"
    reply, pushes = turn_reply(trace)
    prompts, stages = turn_notes(notes)
    return {"reply": reply, "log": log, "prompts": prompts, "stages": stages, "code": code, "push": pushes}


def prepare_case(case, src_cfg, skills_dir, case_dir, redact_names):
    os.makedirs(case_dir, exist_ok=True)
    cfg = sandbox_config(src_cfg, case_dir, skills_dir)
    cfg["base_dir"] = case_dir
    apply_setup(cfg, case.get("setup") or {})
    names = list(redact_names)
    extra = (case.get("setup") or {}).get("redact")
    if isinstance(extra, list):
        names = [str(x) for x in extra if str(x).strip()]
    write_redact(os.path.join(case_dir, "redact.txt"), names)
    kb = os.path.join(case_dir, "kb")
    os.makedirs(kb, exist_ok=True)
    for item in (case.get("setup") or {}).get("kb") or []:
        if not isinstance(item, dict):
            continue
        rel = os.path.basename(str(item.get("name") or "notes.txt"))
        with open(os.path.join(kb, rel), "w", encoding="utf-8") as f:
            f.write(str(item.get("text") or ""))
    cfg_path = os.path.join(case_dir, "agent.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    fake = case.get("fake")
    script = ""
    if isinstance(fake, dict):
        script = os.path.join(case_dir, "script.json")
        with open(script, "w", encoding="utf-8") as f:
            json.dump(fake, f, ensure_ascii=False, indent=2)
            f.write("\n")
        step = script + ".step"
        if os.path.isfile(step):
            os.remove(step)
    return cfg, cfg_path, script, names


def case_env(base_env, mode, script, fixture, app_dir):
    env = dict(base_env)
    for key in DROP_ENV:
        env.pop(key, None)
    env["AGENTBOT_EVAL_NO_LARK"] = "1"
    env["AGENTBOT_APP_LOCK_DIR"] = app_dir
    env["AGENTBOT_MEMORY_FIXTURE"] = fixture
    env["PYTHONUTF8"] = env.get("PYTHONUTF8") or "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if mode == "fake":
        env["AGENTBOT_FAKE_AGENT"] = "0"
        if script:
            env["AGENTBOT_FAKE_SCRIPT"] = script
    return env


def run_case(case, src_cfg, skills_dir, work, mode, provider_name, timeout, judge, redact_names, bot_name):
    case = subst(case, {"bot": bot_name})
    case_dir = os.path.join(work, "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(case.get("id") or "case")))
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir, ignore_errors=True)
    cfg, cfg_path, script, names = prepare_case(case, src_cfg, skills_dir, case_dir, redact_names)
    app_dir = os.path.join(case_dir, "applocks")
    os.makedirs(app_dir, exist_ok=True)
    files_dir = os.path.join(case_dir, "files")
    turns = case.get("turns") or []
    if len(turns) > 1:
        root_id = "topic_" + str(case.get("id") or "case")
    else:
        root_id = ""
    code = ""
    turn_out = {}
    logs, prompts, stages, codes, pushes = [], [], [], [], []
    base_env = os.environ.copy()
    for index, raw in enumerate(turns, 1):
        turn = dict(raw)
        turn["id"] = str(turn.get("id") or ("t%d" % index))
        pause = float(turn.get("pause_sec") or 0)
        if pause > 0:
            time.sleep(pause)
        text = subst(str(turn.get("text") or ""), {"code": code, "bot": bot_name})
        if "{{code}}" in text or "{{" in text and "}}" in text and "{{code}}" in str(raw.get("text") or "") and not code:
            turn_out[turn["id"]] = {"reply": "", "log": "确认码没有从上一条回复里取到\n", "prompts": "", "stages": ""}
            logs.append(turn_out[turn["id"]]["log"])
            codes.append(1)
            continue
        turn["text"] = text
        turn["message_id"] = "eval_%s_%s" % (case.get("id") or "case", turn["id"])
        if root_id:
            turn["root_id"] = root_id
        fixture = build_fixture(turn, case.get("setup") or {}, files_dir)
        fix_path = os.path.join(case_dir, turn["id"] + ".fixture.json")
        with open(fix_path, "w", encoding="utf-8") as f:
            json.dump(fixture, f, ensure_ascii=False)
        turn["event"] = build_event(turn, bot_name)
        env = case_env(base_env, mode, script, fix_path, app_dir)
        got = run_turn(turn, cfg_path, case_dir, env, timeout)
        turn_out[turn["id"]] = got
        logs.append(got["log"])
        prompts.append(got["prompts"])
        stages.append(got["stages"])
        codes.append(got["code"])
        pushes.extend(got["push"])
        found = grade.extract_code(got["reply"])
        if found:
            code = found
    case = subst(case, {"code": code, "bot": bot_name})
    locked = sandbox_locked(case_dir, app_dir)
    result = {
        "turns": turn_out,
        "last_reply": (turn_out.get((turns and str((turns[-1] or {}).get("id") or "t%d" % len(turns))) or "") or {}).get("reply") or "",
        "log": "\n".join(logs),
        "prompts": "\n\n".join(prompts),
        "stages": "\n".join(stages),
        "codes": codes,
        "push_text": pushes,
        "locked": locked,
    }
    if not result["last_reply"] and turn_out:
        result["last_reply"] = list(turn_out.values())[-1].get("reply") or ""
    checks, passed = grade.grade_case(case, result, mode, provider_name, cfg)
    if judge:
        item = grade.judge_case(case, result, cfg, names)
        checks.append(item)
        if not item.get("pass") and not item.get("skipped"):
            passed = False
    return {
        "id": case.get("id"),
        "title": case.get("title") or "",
        "tags": case.get("tags") or [],
        "passed": passed,
        "checks": checks,
    }, names


def scrub_report(report, names):
    if not names:
        return report

    def walk(obj):
        if isinstance(obj, str):
            return guard.redact(obj, names)
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    return walk(report)


def main(argv):
    parser = argparse.ArgumentParser(description="回答质量回归。不连接飞书，不占用单实例锁。")
    parser.add_argument("--config", help="机器人的 JSON 配置。会复制到沙箱，不改原文件。")
    parser.add_argument("--suite", default=os.path.join(HERE, "cases", "suite.json"))
    parser.add_argument("--baseline", default="")
    parser.add_argument("--out", default=os.path.join(HERE, "out"))
    parser.add_argument("--provider", choices=("fake", "config"), default="config",
                        help="fake 用仓库里的脚本；config 用配置里的模型。CI 请传 fake。")
    parser.add_argument("--tag", default="")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--judge", action="store_true", help="打开模型评分。默认关。")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--self-check", action="store_true", help="只检查评分器，不启动机器人。")
    parser.add_argument("--use-config-redact", action="store_true",
                        help="额外使用原配置的 redact.txt。报告里这些名字会打码。")
    args = parser.parse_args(argv)
    if args.self_check:
        err = grade.self_check()
        if err:
            print(err)
            return 2
        print("self-check ok")
        if not args.config:
            return 0
    if not args.config:
        print("需要 --config，或只跑 --self-check")
        return 2
    src_path = os.path.abspath(args.config)
    if not os.path.isfile(src_path):
        print("找不到配置 " + src_path)
        return 2
    src = json.load(open(src_path, encoding="utf-8-sig"))
    if not src.get("system_prompt") or not isinstance(src.get("texts"), dict):
        print("配置里需要 system_prompt 和 texts")
        return 2
    origin = origin_base(src, src_path)
    skills_dir = resolve_skills(src, origin)
    suite = load_suite(args.suite)
    cases = select_cases(suite, args.tag, args.id)
    if not cases:
        print("没有选中的用例")
        return 2
    bot_name = (src.get("bot_names") or ["小助手"])[0]
    names = list(DEFAULT_REDACT)
    if args.use_config_redact:
        raw = os.path.join(origin, src.get("redact_file") or "redact.txt")
        names.extend(guard.load_names(raw))
    before = lock_mtime(origin)
    work = os.path.join(os.path.abspath(args.out), "work")
    os.makedirs(work, exist_ok=True)
    mode = "fake" if args.provider == "fake" else "config"
    provider_name = "fake" if mode == "fake" else str(src.get("model_provider") or "cursor")
    rows = []
    seen_names = list(names)
    for case in cases:
        row, used = run_case(case, src, skills_dir, work, mode, provider_name, args.timeout, args.judge, names, bot_name)
        rows.append(row)
        for name in used:
            if name not in seen_names:
                seen_names.append(name)
        mark = "通过" if row["passed"] else "失败"
        print("%s %s" % (mark, row["id"]))
    passed = sum(1 for row in rows if row["passed"])
    current = {
        "schema_version": 1,
        "suite": suite.get("id") or os.path.basename(args.suite),
        "provider": provider_name,
        "mode": mode,
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "cases": rows,
    }
    after = lock_mtime(origin)
    if before != after:
        current["lock_note"] = "警告：原配置目录的 state/bot.lock 修改时间变了。这次日志里如果没有 lock acquired，多半是机器人自己重启过，不是回归测试写的。"
    diff = None
    if args.baseline and os.path.isfile(args.baseline) and not args.update_baseline:
        base = json.load(open(args.baseline, encoding="utf-8-sig"))
        diff = report.diff_baseline(current, base)
    elif args.baseline and not os.path.isfile(args.baseline) and not args.update_baseline:
        current["lock_note"] = (current.get("lock_note") or "") + " 没有找到基线文件，本次不对照。"
    if args.use_config_redact:
        current = scrub_report(current, seen_names)
        if diff:
            diff = scrub_report(diff, seen_names)
    json_path, md_path = report.write_reports(os.path.abspath(args.out), current, diff)
    if args.update_baseline:
        if not args.baseline:
            print("更新基线需要 --baseline")
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(args.baseline)), exist_ok=True)
        view = report.baseline_view(current)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(view, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("baseline " + args.baseline)
    print("%d/%d passed" % (passed, len(rows)))
    print(md_path)
    print(json_path)
    if diff and diff.get("regressions"):
        print("回归 %d" % len(diff["regressions"]))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
