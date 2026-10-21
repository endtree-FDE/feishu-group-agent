# 技能自动改进。不连飞书。客户名只出现在输入里，技能文件里必须变成「某客户」。
import json, os, re, shutil, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import feature_switch, skill_evolve, skills

results = []
NAME = "杭州某某科技有限公司"
PHONE = "13812345678"
SKILL = """---
name: post-mortem
description: 做创作复盘。
---

# 复盘

按步骤写。
"""


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


def equal(_text):
    return (1, 1)


def higher(text):
    if "自动补记" in (text or ""):
        return (2, 2)
    return (1, 2)


def worse(text):
    if "自动补记" in (text or ""):
        return (0, 1)
    return (1, 1)


def plant(base):
    folder = os.path.join(base, "skills", "post-mortem")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "SKILL.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SKILL)
    return path


def cfg_on(base):
    return {
        "skills": {"enabled": True, "dir": "skills", "evolve": {"enabled": True, "cases": ["help-shortcut"]}},
        "write": {"allowed_users": {"user_owner": "主人"}},
        "base_dir": base,
    }


def owner(base):
    return {"user": "user_owner", "chat_id": "chat_owner", "chat_type": "p2p", "message_id": "om_evolve1", "scope": ""}


def code_of(text):
    matched = re.search(r"确认\s*(\d{6})", text or "")
    return matched.group(1) if matched else ""


check("旧配置没写自动改进时关着",
      not skill_evolve.enabled({})
      and not skill_evolve.enabled({"skills": {"enabled": True}})
      and not skill_evolve.enabled({"skills": {"enabled": False, "evolve": {"enabled": True}}})
      and skill_evolve.enabled({"skills": {"enabled": True, "evolve": {"enabled": True}}}))

base = tempfile.mkdtemp(prefix="agentbot_evolve_")
try:
    path = plant(base)
    cfg = cfg_on(base)
    info = skill_evolve.describe(cfg, base, "post-mortem")
    check("没有 version 的技能按第 1 版，原文件不动",
          info and info["version"] == 1 and "version:" not in open(path, encoding="utf-8").read())
    row = {
        "ok": False, "skills": ["post-mortem"],
        "steps": [{"kind": "tool", "name": "web_search", "ok": False, "result": NAME + " app_secret=abc " + PHONE}],
        "result": "没答成",
    }
    noted = skill_evolve.observe(cfg, base, row, [NAME], ["交给 " + NAME + " app_secret=abc " + PHONE + " /home/ubuntu/x"])
    raw_signal = open(skill_evolve._signal_path(base), encoding="utf-8").read()
    check("失败和复盘会记成信号，客户名和密钥不进信号",
          noted == ["post-mortem"] and NAME not in raw_signal and PHONE not in raw_signal
          and "app_secret=abc" not in raw_signal and "/home/ubuntu" not in raw_signal and "某客户" in raw_signal,
          raw_signal)
    off = {"skills": {"enabled": True, "dir": "skills", "evolve": {"enabled": False}}}
    before = open(path, encoding="utf-8").read()
    check("关掉时不记信号也不改文件",
          skill_evolve.observe(off, base, row, [NAME]) == [] and open(path, encoding="utf-8").read() == before)

    blocked = skill_evolve.review(cfg, base, owner(base), [NAME], runner=worse)
    after_block = open(path, encoding="utf-8").read()
    check("分数变低时不替换",
          blocked and "没有替换" in blocked["text"] and after_block == SKILL, blocked["text"] if blocked else "")

    same = skill_evolve.review(cfg, base, owner(base), [NAME], runner=equal)
    check("分数一样高时不替换",
          same and "没有替换" in same["text"] and "确认" not in same["text"]
          and open(path, encoding="utf-8").read() == SKILL
          and not os.path.isfile(skill_evolve._candidate_path(base, "post-mortem")),
          same["text"] if same else "")

    offered = skill_evolve.review(cfg, base, owner(base), [NAME], runner=higher)
    offer_text = offered["text"] if offered else ""
    code = code_of(offer_text)
    pending = open(path, encoding="utf-8").read()
    candidate = ""
    cand = skill_evolve._candidate_path(base, "post-mortem")
    if os.path.isfile(cand):
        candidate = open(cand, encoding="utf-8").read()
    check("分数更高才发出确认，文件还是原样",
          code and "第 2 版" in offer_text and pending == SKILL
          and "自动补记" in candidate and "version: 2" in candidate
          and NAME not in candidate and "某客户" in candidate and "app_secret=abc" not in candidate,
          offer_text + "\n" + candidate[:400])
    extras = [name for name in os.listdir(os.path.join(base, "skills", "post-mortem")) if name != "SKILL.md"]
    stranger = dict(owner(base), user="user_other", message_id="om_evolve2")
    denied = feature_switch.try_confirm(cfg, base, os.path.join(base, "bot.json"), code, stranger, apply=True)
    check("别人确认不会改文件，也没有新脚本",
          denied and "本人" in denied["text"] and open(path, encoding="utf-8").read() == SKILL and extras == [],
          denied["text"] if denied else "")
    accepted = feature_switch.try_confirm(cfg, base, os.path.join(base, "bot.json"), code, owner(base), apply=True)
    body = open(path, encoding="utf-8").read()
    meta = skill_evolve._read_json(skill_evolve._meta_path(base))
    saved = (meta.get("skills") or {}).get("post-mortem") or {}
    check("主人确认后才换成第 2 版，基线里留着第 1 版",
          accepted and accepted.get("reload_skills") and "第 2 版" in accepted["text"]
          and "自动补记" in body and "version: 2" in body and NAME not in body
          and saved.get("version") == 2 and saved.get("sha256") == skill_evolve._sha(body)
          and any(int(row.get("version") or 0) == 1 and "自动补记" not in (row.get("text") or "") for row in saved.get("history") or []),
          accepted["text"] if accepted else "")
    again = skill_evolve.handle_text(cfg, base, "回滚技能 post-mortem", owner(base), [NAME], owner=True, runner=equal)
    back_code = code_of(again["text"] if again else "")
    still = open(path, encoding="utf-8").read()
    check("回滚也要先确认，确认前文件不变",
          back_code and "第 1 版" in again["text"] and still == body, again["text"] if again else "")
    restored = feature_switch.try_confirm(cfg, base, os.path.join(base, "bot.json"), back_code, owner(base), apply=True)
    rolled = open(path, encoding="utf-8").read()
    check("确认回滚后回到上一版",
          restored and "回到第 1 版" in restored["text"] and "自动补记" not in rolled and "按步骤写" in rolled,
          restored["text"] if restored else rolled[:300])

    group = skill_evolve.handle_text(cfg, base, "改进技能", dict(owner(base), chat_type="group", chat_id="chat_alpha"), [NAME], owner=True)
    other = skill_evolve.handle_text(cfg, base, "改进技能", stranger, [NAME], owner=False)
    check("群里和旁人不能改进技能",
          group and "私聊" in group["text"] and other and "只有主人" in other["text"])

    turned = feature_switch.mutate({"skills": {"enabled": False, "dir": "skills"}}, "skill_evolve", True)
    check("从设置打开时会一并打开技能，目录不动",
          turned["skills"]["enabled"] is True and turned["skills"]["evolve"]["enabled"] is True
          and turned["skills"]["dir"] == "skills" and feature_switch.is_on(turned, "skill_evolve"))
    switched_off = feature_switch.mutate(turned, "skill_evolve", False)
    check("关掉自动改进不关掉已经开着的技能",
          switched_off["skills"]["evolve"]["enabled"] is False and switched_off["skills"]["enabled"] is True)

    menu_base = os.path.join(base, "menu")
    os.makedirs(menu_base, exist_ok=True)
    proposed = feature_switch.handle_text(
        {"write": {"allowed_users": {"user_owner": "主人"}}, "skills": {"enabled": False}},
        menu_base, "第8项", owner(base))
    check("设置里能点到技能改进", proposed and "自动优化步骤" in proposed["text"], proposed["text"] if proposed else "")
finally:
    shutil.rmtree(base, ignore_errors=True)

scored = skill_evolve.score_skills(os.path.join(FW, "skills"), ["help-shortcut"], timeout=90)
check("真的会跑 evals/run.py，单题分数可读", scored == (1, 1), str(scored))

run = tempfile.mkdtemp(prefix="agentbot_evolve_run_")
try:
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = run
    src["model_provider"] = "fake"
    src["progress"] = {"enabled": False}
    src["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    src["bot_open_id"] = "ou_placeholder"
    src["bot_names"] = ["小助手"]
    src["p2p"] = {"allow_open_ids": ["user_owner"], "open_to_all": True}
    src["write"]["allowed_users"] = {"user_owner": "主人"}
    src["skills"] = {"enabled": True, "dir": "skills", "preload": False, "evolve": {"enabled": True, "cases": ["help-shortcut"]}}
    os.makedirs(os.path.join(run, "skills", "post-mortem"), exist_ok=True)
    live = os.path.join(run, "skills", "post-mortem", "SKILL.md")
    with open(live, "w", encoding="utf-8") as handle:
        handle.write(SKILL)
    skill_evolve.observe(src, run, {
        "ok": False, "skills": ["post-mortem"],
        "steps": [{"kind": "tool", "name": "skill_read", "ok": False}],
    }, [NAME])
    path = os.path.join(run, "bot.json")
    json.dump(src, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    ask = {"message_id": "evmid1", "chat_id": "chat_owner", "chat_type": "p2p", "message_type": "text",
           "sender_id": "user_owner", "sender_type": "user", "content": "改进技能"}
    events = os.path.join(run, "events.jsonl")
    open(events, "w", encoding="utf-8").write(json.dumps({"event": ask}, ensure_ascii=False) + "\n")
    env = dict(os.environ)
    env["AGENTBOT_FAKE_AGENT"] = "0"
    proc = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), path, "--dry-run", events],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    kept = open(live, encoding="utf-8").read()
    check("私聊发改进技能时分数没有更高就不替换，演练不改文件",
          proc.returncode == 0 and "没有替换" in out and "确认" not in out and "第 2 版" not in out
          and kept == SKILL and NAME not in out,
          out[-1200:])
finally:
    shutil.rmtree(run, ignore_errors=True)

loaded = skills.SkillSet({"skills": {"enabled": True, "dir": os.path.join(FW, "skills")}}, FW)
check("原有技能仍然能加载", any(item.name == "post-mortem" for item in loaded.valid), loaded.status())

print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
