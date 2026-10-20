# 主人在私聊里开关功能。只改白名单里的几项布尔开关。
# 不改模型接口、密钥、私聊开放范围。那些仍要去改配置文件。
# 确认方式见 confirm_mode。默认点按钮，没开通卡片回调时用确认码。确认后备份、写入、检查，再按 stop 然后 start 重启。
import copy, datetime, hashlib, json, os, random, re, sqlite3, subprocess, sys, time
import confirm_mode, digest, guard, knowledge, lifecycle, memory_growth, outbound, retrospect, skill_evolve, usage
import crew

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ACTOR = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_CODE = re.compile(r"\d{6}")
_MENU = re.compile(r"^(?:/设置|/set|/settings|设置|功能开关|开关功能|看设置|打开设置)\s*$", re.I)
_INDEX = re.compile(r"^第\s*([1-9])\s*项$")
_DIGIT = re.compile(r"^([1-9])$")
_OFF = re.compile(r"不要开|别开|关掉|关闭|停用|关上")
_ON = re.compile(r"打开|开启|启用|开一下|开开")
_SENSITIVE = re.compile(
    r"(密钥|口令|令牌|app\s*secret|api\s*key|换模型|改模型|模型换成|把模型换|模型接口|模型提供|模型配置|"
    r"model\s*\.\s*provider|model_provider|openai_compatible|"
    r"私聊开放|开放私聊|"
    r"所有人都能私聊|允许所有人|加入白名单|改白名单|白名单加人)",
    re.I)
_CHANGE = re.compile(r"(打开|关掉|关闭|修改|换成|改成|设置|填上|写入|启用|停用)")

FEATURES = (
    {
        "id": "report",
        "n": 1,
        "title": "用量周报",
        "aliases": ("用量周报", "周报"),
        "use": "每周把用了多少次整理成一份合计，私聊发给你。",
        "key": "report.enabled",
    },
    {
        "id": "knowledge",
        "n": 2,
        "title": "知识检索",
        "aliases": ("知识检索", "知识库"),
        "use": "有人提问时，用这个群已经放进来的资料来回答，并写上来源。",
        "key": "knowledge.enabled",
    },
    {
        "id": "growth",
        "n": 3,
        "title": "记忆成长",
        "aliases": ("记忆成长", "记忆"),
        "use": "隔一段时间看看群里定过的事，提议记住。有人点头才写入。",
        "key": "memory.growth.enabled",
    },
    {
        "id": "digest",
        "n": 4,
        "title": "早间待办",
        "aliases": ("早间待办", "早晨摘要", "晨报", "待办"),
        "use": "到了早上，往群里发一条今天的日程和还没完成的任务。",
        "key": "digest.enabled",
    },
    {
        "id": "alerts",
        "n": 5,
        "title": "断线提醒",
        "aliases": ("断线提醒", "掉线提醒", "提醒"),
        "use": "机器人连不上时，私聊告诉主人。",
        "key": "alerts.enabled",
    },
    {
        "id": "skills",
        "n": 6,
        "title": "技能",
        "aliases": ("技能",),
        "use": "遇到选题、写稿这类事，按现成的步骤来做。",
        "key": "skills.enabled",
    },
    {
        "id": "retrospect",
        "n": 7,
        "title": "复盘写回",
        "aliases": ("复盘写回", "复盘"),
        "use": "一轮群里的事做完后，写一句短复盘。有人点头才记进这个群。",
        "key": "memory.retrospect.enabled",
    },
    {
        "id": "skill_evolve",
        "n": 8,
        "title": "技能改进",
        "aliases": ("技能改进", "自动改进"),
        "use": "根据用过的记录和复盘，提议技能的下一版。分数不下降并且你点头才替换。",
        "key": "skills.evolve.enabled",
    },
    {
        "id": "crew",
        "n": 9,
        "title": "主理人",
        "aliases": ("主理人", "专员"),
        "use": "复杂问题拆给查资料、读文档、起草。他们只能读，写入还是要你确认。",
        "key": "agent.crew.enabled",
    },
)
_BY_ID = {item["id"]: item for item in FEATURES}
_BY_N = {item["n"]: item for item in FEATURES}

GROUP_REDIRECT = "这个要主人在和我的私聊里改。请主人私聊我，发「设置」。群里不改开关，我也不在这里讲配置细节。"
NOT_OWNER = "只有主人能改这些开关。请让主人私聊我，发「设置」。"
RESTARTING = "好，按你确认的改。我现在重新启动，好了再私聊你一声。这中间可能暂时不回消息。"
DRILL = "确认码对了。当前这次只是演练，没有改动，也没有重新启动。"


def who(user):
    raw = str(user or "")
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _clean_id(value):
    text = str(value or "").strip()
    if not text or text.startswith("REPLACE_") or not _ID.match(text):
        return ""
    return text


def _add_ids(found, seq):
    for item in seq or []:
        oid = _clean_id(item)
        if oid and oid not in found:
            found.append(oid)


def manager_ids(cfg):
    """主人：用量周报或断线提醒的接收人，再加上配置里的写入管理员。"""
    cfg = cfg or {}
    found = []
    report = cfg.get("report") if isinstance(cfg.get("report"), dict) else {}
    alerts = cfg.get("alerts") if isinstance(cfg.get("alerts"), dict) else {}
    _add_ids(found, report.get("owner_open_ids"))
    _add_ids(found, alerts.get("owner_open_ids"))
    users = (cfg.get("write") or {}).get("allowed_users") if isinstance(cfg.get("write"), dict) else {}
    if isinstance(users, dict):
        _add_ids(found, list(users.keys()))
    return found


def is_manager(cfg, user):
    uid = str(user or "")
    return bool(uid) and uid in manager_ids(cfg)


def feature(feature_id):
    return _BY_ID.get(feature_id)


def is_on(cfg, feature_id):
    if feature_id == "report":
        return usage.enabled(cfg)
    if feature_id == "knowledge":
        return knowledge.enabled(cfg)
    if feature_id == "growth":
        return memory_growth.enabled(cfg)
    if feature_id == "digest":
        return digest.enabled(cfg)
    if feature_id == "alerts":
        raw = (cfg or {}).get("alerts")
        return isinstance(raw, dict) and bool(raw.get("enabled"))
    if feature_id == "skills":
        raw = (cfg or {}).get("skills")
        return isinstance(raw, dict) and bool(raw.get("enabled"))
    if feature_id == "retrospect":
        return retrospect.enabled(cfg)
    if feature_id == "skill_evolve":
        return skill_evolve.enabled(cfg)
    if feature_id == "crew":
        return crew.enabled(cfg)
    return False


def _ensure(node, key):
    cur = node.get(key)
    if not isinstance(cur, dict):
        cur = {}
        node[key] = cur
    return cur


def mutate(data, feature_id, turn_on):
    """只动白名单里的开关。别的键原样留下。"""
    if feature_id not in _BY_ID:
        raise ValueError("unknown feature")
    data = copy.deepcopy(data if isinstance(data, dict) else {})
    on = bool(turn_on)
    if feature_id == "report":
        _ensure(data, "report")["enabled"] = on
    elif feature_id == "knowledge":
        _ensure(data, "knowledge")["enabled"] = on
    elif feature_id == "digest":
        _ensure(data, "digest")["enabled"] = on
    elif feature_id == "alerts":
        _ensure(data, "alerts")["enabled"] = on
    elif feature_id == "skills":
        _ensure(data, "skills")["enabled"] = on
    elif feature_id == "growth":
        mem = _ensure(data, "memory")
        if on:
            mem["enabled"] = True
        _ensure(mem, "growth")["enabled"] = on
    elif feature_id == "retrospect":
        mem = _ensure(data, "memory")
        if on:
            mem["enabled"] = True
        _ensure(mem, "retrospect")["enabled"] = on
    elif feature_id == "skill_evolve":
        block = _ensure(data, "skills")
        if on:
            block["enabled"] = True
        _ensure(block, "evolve")["enabled"] = on
    elif feature_id == "crew":
        block = _ensure(data, "agent")
        _ensure(block, "crew")["enabled"] = on
        if on:
            _ensure(data, "features")["tools"] = True
    return data


def _state_on(text):
    return "开着" if text else "关着"


def _kb_has_files(base, cfg):
    kb = (cfg or {}).get("kb_dir") or "kb"
    path = kb if os.path.isabs(str(kb)) else os.path.join(base or "", str(kb))
    if not os.path.isdir(path):
        return False
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            if name == "请把资料放在这里.md":
                continue
            return True
    return False


def _alert_owners(cfg):
    raw = (cfg or {}).get("alerts")
    found = []
    if isinstance(raw, dict):
        _add_ids(found, raw.get("owner_open_ids"))
    return found


def happens(cfg, base, feature_id, turn_on):
    """打开或关掉之后，会不会主动发消息、发给谁。"""
    if not turn_on:
        return "关掉之后，这项不再主动做上面说的事。"
    if feature_id == "report":
        line = "打开之后：每周私聊主人一份合计。不在群里发，也不写出别人问过的原话。"
        if not usage.owner_ids(cfg):
            line += "还没指定主人的话，暂时不会发出去。"
        return line
    if feature_id == "knowledge":
        return "打开之后：不会主动发消息。只有有人提问时才拿资料来答。"
    if feature_id == "growth":
        spec = memory_growth.settings(cfg)
        if spec.get("deliver") == "group":
            line = "打开之后：会把「要不要记住」发在本群里，审批人点头才记下。不点头不会记。"
        else:
            line = "打开之后：会私聊审批人，请他们点头。不点头不会记，也不会把草稿发到群里。"
        if not spec.get("approvers"):
            line += "还没指定谁来点头的话，不会自动提议。"
        if not ((cfg or {}).get("memory") or {}).get("enabled"):
            line += "长期记忆会一起打开，不然它记不住。"
        return line
    if feature_id == "digest":
        line = "打开之后：会主动发到群里，每个群每天最多一条，不点名所有人。"
        chats = digest.settings(cfg).get("chats") or []
        if not chats:
            line += "还没指定发到哪些群的话，暂时不会发。"
        return line
    if feature_id == "alerts":
        line = "打开之后：连不上时私聊主人。不在群里发。"
        if not _alert_owners(cfg) and not usage.owner_ids(cfg):
            line += "还没指定主人的话，只会记在本机，不会发私聊。"
        return line
    if feature_id == "skills":
        return "打开之后：不会主动发消息。有人问到相关的事，才按步骤回答。"
    if feature_id == "retrospect":
        spec = memory_growth.settings(cfg)
        if spec.get("deliver") == "group":
            line = "打开之后：群里一轮做完，会在这个群里发一句短复盘。点头才记进这个群，不点头不记。别的群看不到。私聊里的对话不写进群记忆。"
        else:
            line = "打开之后：群里一轮做完，会私聊审批人一句短复盘。点头才记进这个群，不点头不记，也不会把草稿发到群里。别的群看不到。私聊里的对话不写进群记忆。"
        if not spec.get("approvers"):
            line += "还没指定谁来点头的话，不会自动提议。"
        if not ((cfg or {}).get("memory") or {}).get("enabled"):
            line += "长期记忆会一起打开，不然它记不住。"
        return line
    if feature_id == "skill_evolve":
        line = "打开之后：不会自己换成新版本。你私聊发「改进技能」，它先对照固定题目。分数没有更高就不换。分数更高，还要你在这个私聊里确认。也可以发「回滚技能」再加名字，确认后回到上一版。"
        if not ((cfg or {}).get("skills") or {}).get("enabled"):
            line += "技能会一起打开，不然没有步骤可改。"
        return line
    if feature_id == "crew":
        line = "打开之后：比较长或同时要做几件事的问题，会先查资料、读文档，再起草。短句还是原来的答法。专员不能写入，写入仍要你确认。"
        opt = crew.settings(cfg)
        line += "每一步大约 %d 轮、%d 分钟，整题大约 %d 轮、%d 分钟。" % (
            opt["step_rounds"], max(1, opt["step_sec"] // 60), opt["total_rounds"], max(1, opt["total_sec"] // 60))
        if not ((cfg or {}).get("features") or {}).get("tools"):
            line += "工具会一起打开，否则专员读不了资料。"
        return line
    return ""


def prepare(cfg, base, feature_id):
    if feature_id == "knowledge":
        if _kb_has_files(base, cfg):
            return "知识库文件夹里已经有资料，打开后就可以被搜到。"
        return "要先把资料放进知识库文件夹（一般就在机器人旁边，文件夹名字是 kb）。现在里面还没有资料，打开了也搜不到。"
    if feature_id == "growth":
        return "不用先准备一份稿子。它只会提议，有人点头才记住。"
    if feature_id == "digest":
        return "日历或任务表要先接上。没接上的那一项会说暂时没读到，不会编造。"
    if feature_id == "skills":
        return "技能文件夹里要已经有步骤说明。没有的话，打开了也不会多出新本事。"
    if feature_id == "retrospect":
        return "不用先写稿。它只根据刚结束的这一轮写一句，有人点头才记住。"
    if feature_id == "skill_evolve":
        return "技能文件夹里要已经有步骤说明。没有失败记录或复盘时，发「改进技能」也不会改文件。"
    if feature_id == "crew":
        return "不用先准备。只有比较复杂的问题才会拆开。"
    if feature_id == "report":
        return "不用先准备资料。"
    if feature_id == "alerts":
        return "不用先准备资料。"
    return ""


def _plain_status(on):
    return "开" if on else "关"


def focus_feature(cfg):
    for item in FEATURES:
        if not is_on(cfg, item["id"]):
            return item
    return FEATURES[0]


def detail_text(cfg_path):
    lines = ["下面是给懂配置的同事看的名字。平时不用看。", ""]
    for item in FEATURES:
        lines.append("%d. %s：%s" % (item["n"], item["title"], item["key"]))
    name = os.path.basename(cfg_path or "") or "你的配置文件"
    lines.append("")
    lines.append("文件名：%s" % name)
    lines.append("密钥、模型接口、谁可以私聊，都不能在对话里改。")
    return "\n".join(lines)


def menu_text(cfg, base):
    item = focus_feature(cfg)
    on = is_on(cfg, item["id"])
    lines = [
        "你是主人，可以在这里开关功能。一次只改一项。",
        "",
        "先看这一项：%d. %s（现在%s）。" % (item["n"], item["title"], _state_on(on)),
        item["use"],
        happens(cfg, base, item["id"], not on),
        "准备：" + prepare(cfg, base, item["id"]),
        "",
        "想改它就回复 %d。想看全部，看上面那张卡片。" % item["n"],
        confirm_mode.menu_line(cfg, base),
        "看配置名字请回复「详细」。",
    ]
    return "\n".join(lines)


def card(cfg, base, cfg_path):
    elements = [{
        "tag": "div",
        "text": {"tag": "plain_text", "content": "一次只改一项。点下面的按钮，或回复序号。点了没反应就回复数字。"},
    }]
    for item in FEATURES:
        on = is_on(cfg, item["id"])
        body = "\n".join([
            "%d. %s（现在%s）" % (item["n"], item["title"], _state_on(on)),
            item["use"],
            happens(cfg, base, item["id"], not on),
            "准备：" + prepare(cfg, base, item["id"]),
        ])
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": body}})
        elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "第 %d 项" % item["n"]},
            "type": "primary" if item["n"] == focus_feature(cfg)["n"] else "default",
            "behaviors": [{"type": "callback", "value": {"switch": item["id"], "op": "flip"}}],
        })
    elements.append({"tag": "div", "text": {"tag": "plain_text", "content": confirm_mode.menu_line(cfg, base)}})
    elements.append({
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {"title": {"tag": "plain_text", "content": "详细"}},
        "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": detail_text(cfg_path)}}],
    })
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "summary": {"content": "功能开关"}},
        "header": {"title": {"tag": "plain_text", "content": "功能开关"}, "template": "blue"},
        "body": {"elements": elements},
    }


def welcome_text(cfg):
    return "\n".join([
        "你好，我是团队助手。",
        "",
        "我可以回答群里 @ 我的问题。还有几项默认关着，打开才会主动干活：",
        "· 断线提醒：连不上时私聊你",
        "· 用量周报：每周私聊你一份用量合计",
        "· 早间待办：早上往群里发今天的日程（这项会在群里发消息）",
        "",
        "建议先打开「断线提醒」和「用量周报」。它们只私聊你，不打扰群。",
        "现在回复「设置」，我发一张清单。一次只改一项。",
    ])


def looks_like_command(text):
    q = guard.normalize(text or "").strip()
    if not q:
        return False
    if _MENU.match(q) or _INDEX.match(q) or _DIGIT.match(q):
        return True
    if confirm_mode.refusing_off(q) or confirm_mode.asking_mode(q):
        return True
    if q in ("详细", "详情"):
        return True
    if _sensitive_change(q):
        return True
    if _howto(q) or _toggle_target(q):
        return True
    return False


def _sensitive_change(text):
    if not _SENSITIVE.search(text):
        return False
    return bool(_CHANGE.search(text) or re.search(r"改|换|填|允许|开放", text))


def _find_feature(text):
    best, best_len = None, 0
    for item in FEATURES:
        for alias in item["aliases"]:
            if alias in text and len(alias) > best_len:
                best, best_len = item, len(alias)
    return best


def _howto(text):
    if not re.search(r"怎么|如何|怎样|咋", text):
        return False
    item = _find_feature(text)
    if item and re.search(r"开|关|用|设置", text):
        return True
    if re.search(r"打开|开启|关掉|关闭|设置", text) and re.search(r"功能|开关", text):
        return True
    return False


def _toggle_target(text):
    item = _find_feature(text)
    if not item:
        return None
    if _OFF.search(text):
        return item, False
    if _ON.search(text):
        return item, True
    return None


def _ctx_ok(ctx):
    ctx = ctx or {}
    user = str(ctx.get("user") or "")
    chat = str(ctx.get("chat_id") or "")
    mid = str(ctx.get("message_id") or "")
    return bool(_ACTOR.match(user) and _ACTOR.match(chat) and _ACTOR.match(mid))


def _menu_path(base):
    return os.path.join(base, "state", "switch_menu.json")


def _load_menu(base, now):
    path = _menu_path(base)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if now - float(data.get("ts") or 0) > 1800:
        return None
    return data


def _save_menu(base, ctx, now):
    os.makedirs(os.path.join(base, "state"), exist_ok=True)
    payload = {
        "user": who(ctx.get("user")),
        "chat_id": who(ctx.get("chat_id")),
        "scope": str(ctx.get("scope") or ""),
        "ts": now,
    }
    _write_json(_menu_path(base), payload)


def _menu_matches(menu, ctx):
    if not menu:
        return False
    return (menu.get("user") == who(ctx.get("user"))
            and menu.get("chat_id") == who(ctx.get("chat_id"))
            and str(menu.get("scope") or "") == str(ctx.get("scope") or ""))


def _reply(text, card_body=None, restart=False, patch=False):
    out = {"text": text, "card": card_body, "restart": bool(restart)}
    if patch:
        out["patch"] = True
    return out


def _refuse_place(ctx, cfg):
    if (ctx or {}).get("chat_type") != "p2p":
        return _reply(GROUP_REDIRECT)
    if not is_manager(cfg, (ctx or {}).get("user")):
        return _reply(NOT_OWNER)
    if not _ctx_ok(ctx):
        return _reply("不能改：缺少发送人或会话。请再到私聊里发一次「设置」。")
    return None


def _sensitive_text():
    return "\n".join([
        "这项不能在对话里改。",
        "请让懂配置的同事直接改你电脑上的配置文件。",
        "密钥、模型配置、谁可以私聊我，都在那份文件里，不会通过聊天修改。",
        "回复「详细」可以看文件名，里面没有密钥。",
    ])


def handle_text(cfg, base, text, ctx, names=(), now=None, cfg_path=""):
    """不是开关对话时返回 None。调用方再交给别的命令或模型。"""
    now = time.time() if now is None else float(now)
    q = guard.normalize(text or "").strip()
    ctx = ctx or {}
    if not q:
        return None
    if _sensitive_change(q) and not _MENU.match(q):
        blocked = _refuse_place(ctx, cfg)
        if blocked:
            _audit(base, "refuse", user=ctx.get("user"), chat=ctx.get("chat_type"), names=names)
            return blocked
        _audit(base, "refuse_sensitive", user=ctx.get("user"), names=names)
        return _reply(_sensitive_text())
    if confirm_mode.refusing_off(q) and not _MENU.match(q):
        blocked = _refuse_place(ctx, cfg)
        if blocked:
            _audit(base, "refuse", user=ctx.get("user"), chat=ctx.get("chat_type"), names=names)
            return blocked
        _audit(base, "refuse_off", user=ctx.get("user"), names=names)
        return _reply("确认不能关掉。可以换成点按钮、确认码，或两种一起用。回复「确认方式」看现在的做法。")
    if confirm_mode.asking_mode(q):
        blocked = _refuse_place(ctx, cfg)
        if blocked:
            _audit(base, "refuse", user=ctx.get("user"), chat=ctx.get("chat_type"), names=names)
            return blocked
        picked = confirm_mode.mode_choice(q)
        if not picked:
            return _reply(confirm_mode.mode_help(cfg, base))
        if picked == confirm_mode.configured_mode(cfg):
            return _reply("确认方式现在已经是%s。\n回复「设置」可以看功能开关。" % confirm_mode.plain_mode(picked))
        return _propose_mode(cfg, base, ctx, picked, names, now)
    if _MENU.match(q) or _howto(q):
        blocked = _refuse_place(ctx, cfg)
        if blocked:
            _audit(base, "refuse", user=ctx.get("user"), chat=ctx.get("chat_type"), names=names)
            return blocked
        _save_menu(base, ctx, now)
        _audit(base, "menu", user=ctx.get("user"), names=names)
        return _reply(menu_text(cfg, base), card(cfg, base, cfg_path))
    if q in ("详细", "详情"):
        blocked = _refuse_place(ctx, cfg)
        if blocked:
            return blocked
        return _reply(detail_text(cfg_path))
    chosen = None
    indexed = _INDEX.match(q)
    digit = _DIGIT.match(q)
    toggled = _toggle_target(q)
    if indexed:
        chosen = _BY_N.get(int(indexed.group(1)))
    elif toggled:
        chosen = toggled[0]
    elif digit:
        menu = _load_menu(base, now)
        if _menu_matches(menu, ctx):
            chosen = _BY_N.get(int(digit.group(1)))
        else:
            return None
    else:
        return None
    if not chosen:
        return None
    blocked = _refuse_place(ctx, cfg)
    if blocked:
        _audit(base, "refuse", user=ctx.get("user"), feature=chosen["id"], names=names)
        return blocked
    if toggled and chosen["id"] == toggled[0]["id"]:
        turn_on = toggled[1]
    else:
        turn_on = not is_on(cfg, chosen["id"])
    if is_on(cfg, chosen["id"]) == turn_on:
        word = "开着" if turn_on else "关着"
        return _reply("「%s」现在已经是%s。\n回复「设置」看下一项。" % (chosen["title"], word))
    return _propose(cfg, base, ctx, chosen, turn_on, names, now)


def _propose(cfg, base, ctx, item, turn_on, names, now):
    summary = "%s「%s」" % ("打开" if turn_on else "关掉", item["title"])
    return _offer(cfg, base, ctx, "feature_switch", item["id"], bool(turn_on), summary, names, now,
                  lambda code, plan: _switch_text(cfg, base, item, turn_on, code, plan))


def _propose_mode(cfg, base, ctx, mode, names, now):
    summary = "把确认方式改成%s" % confirm_mode.plain_mode(mode)
    return _offer(cfg, base, ctx, "confirm_mode", "confirm_mode", False, summary, names, now,
                  lambda code, plan: _mode_text(mode, code, plan), set_mode=mode)


def _switch_text(cfg, base, item, turn_on, code, plan):
    verb = "打开" if turn_on else "关掉"
    if plan["mode"] == "code":
        return "\n".join([
            "你想%s「%s」。" % (verb, item["title"]),
            "",
            "现在是%s。" % _state_on(is_on(cfg, item["id"])),
            happens(cfg, base, item["id"], turn_on),
            "准备：" + prepare(cfg, base, item["id"]),
            "",
            "请只回复这一句：",
            "确认 %s" % code,
            "",
            "不想改了就回复：取消 %s" % code,
            "这串数字大约 %d 分钟内有效，而且只有你在这个私聊里能用。" % max(1, plan["ttl"] // 60),
        ])
    body = "\n".join([
        "你想%s「%s」。" % (verb, item["title"]),
        "现在是%s。" % _state_on(is_on(cfg, item["id"])),
        happens(cfg, base, item["id"], turn_on),
        "准备：" + prepare(cfg, base, item["id"]),
    ])
    return confirm_mode.button_text(body, confirm_mode.impact("feature_switch"), plan["mode"], code, plan["ttl"])


def _mode_text(mode, code, plan):
    summary = "把以后的确认方式改成%s" % confirm_mode.plain_mode(mode)
    if plan["mode"] == "code":
        return "\n".join([
            summary + "。",
            "确认不能关掉。",
            "",
            "请只回复这一句：",
            "确认 %s" % code,
            "",
            "不想改了就回复：取消 %s" % code,
        ])
    return confirm_mode.button_text(summary, confirm_mode.impact("confirm_mode"), plan["mode"], code, plan["ttl"])


def _offer(cfg, base, ctx, kind, feature_id, turn_on, summary, names, now, render, set_mode="", payload_extra=None):
    plan = confirm_mode.plan(cfg, base, high=True)
    token = plan["token"]
    ah = confirm_mode.action_hash(kind, [{"feature": feature_id, "on": bool(turn_on), "mode": set_mode}],
                                  ctx.get("user"), ctx.get("chat_id"), str(ctx.get("scope") or ""))
    extra = {"mode": plan["mode"], "token": token, "action_hash": ah, "button_ok": False,
             "code_ok": False, "fallback": plan["fallback"], "expires": now + plan["ttl"]}
    if set_mode:
        extra["set_mode"] = set_mode
    if isinstance(payload_extra, dict):
        for key in ("skill", "sha256", "version", "op"):
            if key in payload_extra:
                extra[key] = payload_extra[key]
    try:
        code = _reserve(base, ctx, feature_id, turn_on, now, cfg, extra=extra)
    except RuntimeError:
        return _reply("暂时发不出确认码，请再试一次。")
    text = guard.redact(render(code, plan), names)
    card_body = None
    if plan["mode"] != "code" and token:
        card_body = confirm_mode.build_card(text, token, ah)
    _audit(base, "propose", user=ctx.get("user"), feature=feature_id, on=turn_on if kind == "feature_switch" else None, names=names)
    return _reply(text, card_body)


def _db(base):
    os.makedirs(os.path.join(base, "state"), exist_ok=True)
    conn = sqlite3.connect(os.path.join(base, "state", "switches.db"), check_same_thread=False)
    conn.execute("create table if not exists pending_switches(code text primary key, ts real, payload text)")
    conn.execute("create table if not exists used_switches(code text primary key, ts real)")
    conn.execute("create table if not exists used_switch_tokens(token text primary key, ts real)")
    return conn


def _ttl(cfg):
    raw = ((cfg or {}).get("write") or {}).get("confirm_ttl_sec", 300)
    try:
        num = int(raw)
    except (TypeError, ValueError):
        return 300
    if num < 30:
        return 300
    return num


def _foreign_code(base, code):
    path = os.path.join(base, "state", "state.db")
    if not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("select 1 from pending_writes where code=?", (code,)).fetchone()
            used = conn.execute("select 1 from used_writes where code=?", (code,)).fetchone()
        finally:
            conn.close()
        return bool(row or used)
    except sqlite3.Error:
        return False


def _reserve(base, ctx, feature_id, turn_on, now, cfg, extra=None):
    conn = _db(base)
    digits = confirm_mode.code_digits(cfg)
    span = 10 ** digits
    try:
        conn.execute("delete from pending_switches where ts < ?", (now - _pending_ttl(cfg, extra),))
        for _ in range(8):
            code = "%0*d" % (digits, random.SystemRandom().randint(0, span - 1))
            used = conn.execute("select 1 from used_switches where code=?", (code,)).fetchone()
            if used or _foreign_code(base, code):
                continue
            payload = {
                "user": who(ctx.get("user")),
                "chat_id": who(ctx.get("chat_id")),
                "scope": str(ctx.get("scope") or ""),
                "feature": feature_id,
                "on": bool(turn_on),
                "ts": now,
            }
            if extra:
                payload.update(extra)
            try:
                conn.execute("insert into pending_switches(code, ts, payload) values(?,?,?)",
                             (code, now, json.dumps(payload, ensure_ascii=False)))
                conn.commit()
                return code
            except sqlite3.IntegrityError:
                continue
    finally:
        conn.close()
    raise RuntimeError("no code")


def _pending_ttl(cfg, extra):
    if isinstance(extra, dict) and extra.get("expires"):
        try:
            return max(30, int(float(extra["expires"]) - time.time()) + 5)
        except (TypeError, ValueError):
            pass
    return _ttl(cfg)


def _take_pending(base, code, now, cfg):
    conn = _db(base)
    try:
        row = conn.execute("select ts, payload from pending_switches where code=?", (code,)).fetchone()
        used = conn.execute("select 1 from used_switches where code=?", (code,)).fetchone()
        if used or not row:
            return None, "missing"
        ts, payload = row
        try:
            rec = json.loads(payload)
        except ValueError:
            return None, "missing"
        if not isinstance(rec, dict):
            return None, "missing"
        exp = rec.get("expires")
        try:
            too_old = now > float(exp) if exp else (now - float(ts or 0) > _ttl(cfg))
        except (TypeError, ValueError):
            too_old = True
        if too_old:
            conn.execute("delete from pending_switches where code=?", (code,))
            conn.commit()
            return None, "expired"
        return rec, ""
    finally:
        conn.close()


def _store_pending(base, code, rec):
    conn = _db(base)
    try:
        conn.execute("update pending_switches set payload=? where code=?",
                     (json.dumps(rec, ensure_ascii=False), code))
        conn.commit()
        return True
    finally:
        conn.close()


def _token_used(base, token):
    token = str(token or "")
    if not token:
        return False
    conn = _db(base)
    try:
        row = conn.execute("select 1 from used_switch_tokens where token=?", (token,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _mark_used(base, code, now, token=""):
    conn = _db(base)
    try:
        conn.execute("insert or ignore into used_switches(code, ts) values(?,?)", (code, now))
        if token:
            conn.execute("insert or ignore into used_switch_tokens(token, ts) values(?,?)", (token, now))
        conn.execute("delete from pending_switches where code=?", (code,))
        conn.commit()
        row = conn.execute("select 1 from used_switches where code=?", (code,)).fetchone()
        return bool(row)
    finally:
        conn.close()


def _same_actor(rec, ctx, from_card=False):
    if rec.get("user") != who(ctx.get("user")) or rec.get("chat_id") != who(ctx.get("chat_id")):
        return False
    want = str(rec.get("scope") or "")
    got = str(ctx.get("scope") or "")
    if from_card and not got:
        return True
    return got == want


def try_confirm(cfg, base, cfg_path, code, ctx, cancel=False, names=(), now=None, apply=True, check=None):
    """码不是我们发的则返回 None，交给原来的写入确认。"""
    now = time.time() if now is None else float(now)
    code = str(code or "")
    if not confirm_mode.code_fullmatch(cfg, code):
        return None
    ctx = ctx or {}
    if not _ctx_ok(ctx):
        rec, why = _take_pending(base, code, now, cfg)
        if rec is None and why == "missing":
            return None
        return _reply("不能确认：缺少发送人或原消息。")
    rec, why = _take_pending(base, code, now, cfg)
    if why == "missing" and rec is None:
        return None
    if why == "expired":
        _audit(base, "expired", user=ctx.get("user"), names=names)
        return _reply("这个确认码过期了。回复「设置」再来一次。")
    if not _same_actor(rec, ctx):
        _audit(base, "deny", user=ctx.get("user"), feature=rec.get("feature"), names=names)
        return _reply("只有你本人在原来的私聊里才能确认。")
    if not is_manager(cfg, ctx.get("user")) or ctx.get("chat_type") != "p2p":
        _audit(base, "deny", user=ctx.get("user"), feature=rec.get("feature"), names=names)
        return _reply(NOT_OWNER if ctx.get("chat_type") == "p2p" else GROUP_REDIRECT)
    mode = rec.get("mode") or "code"
    if mode == "button" and not cancel:
        return _reply("这次请点卡片上的「确认」。回复数字不算。")
    if mode == "button" and cancel:
        return _reply("这次请点卡片上的「取消」。")
    if mode == "button_and_code" and not cancel and not rec.get("button_ok"):
        rec["code_ok"] = True
        if not _store_pending(base, code, rec):
            return _reply("确认没能记下，请稍后再试。")
        return _reply("确认码已记下。请再点卡片上的「确认」。")
    return _accept_switch(cfg, base, cfg_path, code, rec, ctx, cancel, names, now, apply, check)


def _find_switch_token(base, token, now, cfg):
    if _token_used(base, token):
        return "", None, "used"
    conn = _db(base)
    try:
        rows = conn.execute("select code, ts, payload from pending_switches").fetchall()
    finally:
        conn.close()
    for code, ts, payload in rows:
        try:
            rec = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("token") != token:
            continue
        exp = rec.get("expires")
        try:
            too_old = now > float(exp) if exp else (now - float(ts or 0) > _ttl(cfg))
        except (TypeError, ValueError):
            too_old = True
        if too_old:
            _drop_switch(base, code)
            return code, rec, "expired"
        return code, rec, ""
    return "", None, "missing"


def _drop_switch(base, code):
    conn = _db(base)
    try:
        conn.execute("delete from pending_switches where code=?", (code,))
        conn.commit()
    finally:
        conn.close()


def try_click(cfg, base, cfg_path, token, op, given_hash, ctx, names=(), now=None, apply=True, check=None, message_id=""):
    """不是开关确认按钮时返回 None。"""
    now = time.time() if now is None else float(now)
    token = str(token or "")
    if not token:
        return None
    ctx = ctx or {}
    code, rec, why = _find_switch_token(base, token, now, cfg)
    if why == "missing" and rec is None:
        return None
    if why == "used":
        return _reply("已经处理过，不能再点一次。", confirm_mode.done_card("已处理", "这次已经点过了。"), patch=True)
    if why == "expired":
        _audit(base, "expired", user=ctx.get("user"), names=names)
        return _reply("这次确认已经过期。回复「设置」再来一次。", confirm_mode.done_card("已过期", "时间过了，没有改。"), patch=True)
    if not _ctx_ok(dict(ctx, message_id=ctx.get("message_id") or message_id or "card")):
        return _reply("不能确认：缺少发送人或会话。")
    if not _same_actor(rec, ctx, True):
        _audit(base, "deny", user=ctx.get("user"), feature=rec.get("feature"), names=names)
        return _reply("只有你本人在原来的私聊里才能确认。")
    if str(given_hash or "") != str(rec.get("action_hash") or ""):
        _audit(base, "deny", user=ctx.get("user"), feature=rec.get("feature"), names=names, reason="tamper")
        return _reply("卡片内容和要改的事对不上，没有改。")
    if not is_manager(cfg, ctx.get("user")):
        _audit(base, "deny", user=ctx.get("user"), feature=rec.get("feature"), names=names)
        return _reply(NOT_OWNER)
    cancel = op == "no"
    mode = rec.get("mode") or "code"
    if mode == "code":
        return _reply("这次请回复确认码，点按钮不算。")
    if mode == "button_and_code" and not cancel and not rec.get("code_ok"):
        rec["button_ok"] = True
        if not _store_pending(base, code, rec):
            return _reply("确认没能记下，请稍后再试。")
        return _reply("按钮已记下。请再回复确认码。",
                      confirm_mode.done_card("还差确认码", "按钮已记下。请在这个私聊里回复确认码。"), patch=True)
    ctx = dict(ctx, chat_type="p2p", message_id=ctx.get("message_id") or message_id or "card")
    result = _accept_switch(cfg, base, cfg_path, code, rec, ctx, cancel, names, now, apply, check)
    title = "已取消" if cancel else ("已确认" if result.get("restart") or "演练" in (result.get("text") or "") else "没做成")
    result["card"] = confirm_mode.done_card(title, result.get("text") or title)
    result["patch"] = True
    return result


def _accept_switch(cfg, base, cfg_path, code, rec, ctx, cancel, names, now, apply, check):
    if not _mark_used(base, code, now, rec.get("token") or ""):
        return _reply("确认没能记下，请稍后再试。")
    if rec.get("feature") == "confirm_mode":
        return _finish_mode(cfg, base, cfg_path, rec, ctx, cancel, names, apply, check)
    if rec.get("feature") in ("skill_evolve", "skill_rollback"):
        return skill_evolve.accept(cfg, base, rec, cancel, names, apply)
    item = feature(rec.get("feature"))
    if not item:
        return _reply("这项不能在对话里改。回复「设置」看能改的功能。")
    if cancel:
        _audit(base, "cancel", user=ctx.get("user"), feature=item["id"], names=names)
        return _reply("已取消。配置没有改。")
    if not apply:
        _audit(base, "drill", user=ctx.get("user"), feature=item["id"], names=names)
        return _reply(DRILL)
    result = commit_toggle(cfg_path, base, item["id"], bool(rec.get("on")), names=names, check=check, user=ctx.get("user"))
    if not result.get("ok"):
        return _reply(result.get("text") or _fail_text("check"))
    _write_job(base, cfg_path, result.get("backup") or "", ctx, item, bool(rec.get("on")))
    later = "「%s」已经%s。我重新启动好了。私聊再发「设置」可以看现在的状态。" % (
        item["title"], "打开" if rec.get("on") else "关掉")
    _write_result(base, ctx.get("user"), later)
    _audit(base, "applied", user=ctx.get("user"), feature=item["id"], on=bool(rec.get("on")), names=names)
    return _reply(RESTARTING, restart=True)


def _finish_mode(cfg, base, cfg_path, rec, ctx, cancel, names, apply, check):
    mode = rec.get("set_mode") or ""
    if mode not in confirm_mode.MODES:
        return _reply("这种方式不能用。确认不能关掉。")
    if cancel:
        _audit(base, "cancel", user=ctx.get("user"), feature="confirm_mode", names=names)
        return _reply("已取消。确认方式没有改。")
    if not apply:
        _audit(base, "drill", user=ctx.get("user"), feature="confirm_mode", names=names)
        return _reply(DRILL)
    result = commit_confirm_mode(cfg_path, base, mode, names=names, check=check, user=ctx.get("user"))
    if not result.get("ok"):
        return _reply(result.get("text") or _fail_text("check"))
    item = {"id": "confirm_mode", "title": "确认方式"}
    _write_job(base, cfg_path, result.get("backup") or "", ctx, item, False)
    _write_result(base, ctx.get("user"), "确认方式已经改成%s。我重新启动好了。" % confirm_mode.plain_mode(mode))
    _audit(base, "applied", user=ctx.get("user"), feature="confirm_mode", names=names)
    return _reply("好，按你确认的改确认方式。我现在重新启动，好了再私聊你一声。", restart=True)


def commit_confirm_mode(cfg_path, base, mode, names=(), check=None, user=""):
    if mode not in confirm_mode.MODES:
        return {"ok": False, "text": "确认不能关掉。"}
    if not _safe_cfg(base, cfg_path):
        _audit(base, "refuse_path", user=user, feature="confirm_mode", names=names, ok=False)
        return {"ok": False, "text": _fail_text("read")}
    try:
        backup = backup_config(cfg_path, base)
    except OSError:
        _audit(base, "backup_failed", user=user, feature="confirm_mode", names=names, ok=False)
        return {"ok": False, "text": _fail_text("backup")}
    try:
        original = open(cfg_path, "rb").read()
        data = json.loads(original.decode("utf-8-sig"))
    except (OSError, ValueError):
        _audit(base, "read_failed", user=user, feature="confirm_mode", names=names, ok=False)
        return {"ok": False, "text": _fail_text("read")}
    if not isinstance(data, dict):
        return {"ok": False, "text": _fail_text("read")}
    new = confirm_mode.write_mode(data, mode)
    tmp = cfg_path + ".writing"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(new, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, cfg_path)
    except OSError:
        _restore_quiet(cfg_path, backup)
        return {"ok": False, "text": _fail_text("read")}
    checker = check or _default_check
    try:
        errors, _hints = checker(cfg_path)
    except Exception as exc:
        _restore_quiet(cfg_path, backup)
        _audit(base, "check_failed", user=user, feature="confirm_mode", names=names, ok=False, reason=type(exc).__name__)
        return {"ok": False, "text": _fail_text("check")}
    if errors:
        _restore_quiet(cfg_path, backup)
        _audit(base, "rollback", user=user, feature="confirm_mode", names=names, ok=False, reason="check")
        return {"ok": False, "text": humanize_check(errors)}
    _audit(base, "write", user=user, feature="confirm_mode", names=names)
    return {"ok": True, "backup": backup, "text": ""}


def _backup_dir(base):
    return os.path.join(base, "state", "config-backups")


def _safe_cfg(base, cfg_path):
    if not cfg_path or not os.path.isfile(cfg_path):
        return False
    base_r = os.path.realpath(base)
    cfg_r = os.path.realpath(cfg_path)
    try:
        if os.path.commonpath([base_r, cfg_r]) != base_r:
            return False
    except ValueError:
        return False
    return cfg_r.lower().endswith(".json")


def _prune_backups(folder, keep=5):
    names = []
    try:
        for name in os.listdir(folder):
            if name.startswith("config-") and name.endswith(".json"):
                names.append(name)
    except OSError:
        return
    names.sort()
    for name in names[:-keep]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def backup_config(cfg_path, base):
    folder = _backup_dir(base)
    os.makedirs(folder, exist_ok=True)
    raw = open(cfg_path, "rb").read()
    stamp = time.strftime("%Y%m%d%H%M%S")
    path = os.path.join(folder, "config-%s.json" % stamp)
    if os.path.exists(path):
        path = os.path.join(folder, "config-%s-%s.json" % (stamp, who(str(time.time()))))
    with open(path, "wb") as handle:
        handle.write(raw)
    latest = os.path.join(folder, "latest.json")
    with open(latest, "wb") as handle:
        handle.write(raw)
    _prune_backups(folder)
    return path


def restore_file(cfg_path, backup_path):
    raw = open(backup_path, "rb").read()
    tmp = cfg_path + ".restore"
    with open(tmp, "wb") as handle:
        handle.write(raw)
    os.replace(tmp, cfg_path)
    return raw


def _default_check(path):
    from checks.check_config import check_file
    return check_file(path)


def _scrub(text, names):
    text = guard.redact(str(text or ""), names or ())
    text = _CODE.sub("[确认码]", text)
    text = re.sub(r"(?i)sk-[A-Za-z0-9_\-]{8,}", "[密钥]", text)
    return text[:300]


def _audit(base, action, user="", feature="", on=None, chat="", names=(), ok=True, reason=""):
    if not base:
        return
    folder = os.path.join(base, "logs")
    try:
        os.makedirs(folder, exist_ok=True)
        row = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "feature": feature or "",
            "ok": bool(ok),
            "user": who(user),
            "chat": "p2p" if chat == "p2p" else ("group" if chat == "group" else ""),
            "reason": _scrub(reason, names),
        }
        if on is not None:
            row["on"] = bool(on)
        path = os.path.join(folder, "feature_switch.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _fail_text(reason):
    if reason == "backup":
        why = "备份没做成，所以我没有动原来的配置。"
        nxt = "请让懂配置的同事看一下磁盘空间。然后再发「设置」。"
    elif reason == "read":
        why = "配置文件读不了，所以没有改。"
        nxt = "请让懂配置的同事看一下那份文件是不是坏了。"
    elif reason == "check":
        why = "改完之后的检查没通过。"
        nxt = "我已经改回原来的样子。先别连续试。请让懂配置的同事看一下配置文件，或再发「设置」看现在的状态。"
    elif reason == "still_running":
        why = "原来的程序还在运行。为了避免开出两个，我没有再启动一个。"
        nxt = "我已经改回原来的样子。等它自己退出后，再发「设置」试一次。"
    elif reason == "start_failed":
        why = "重新启动没有成功。"
        nxt = "我已经改回原来的样子。请让懂配置的同事看一下启动脚本，或稍后再发「设置」。"
    elif reason == "spawn":
        why = "没能发起重新启动。"
        nxt = "我已经改回原来的样子。请稍后再发「设置」。"
    else:
        why = "这次没有改成。"
        nxt = "我已经尽量改回原来的样子。请再发「设置」看现在的状态。"
    return "没改成。\n\n原因：%s\n下一步：%s" % (why, nxt)


def _title_for_path(path):
    path = str(path or "")
    for item in FEATURES:
        if path.startswith(item["key"].split(".")[0]) or item["key"] in path:
            return item["title"]
    return ""


def humanize_check(errors):
    titles = []
    for path, _msg in errors or []:
        title = _title_for_path(path)
        if title and title not in titles:
            titles.append(title)
    if titles:
        why = "、".join(titles) + "这一项检查没通过。"
    else:
        why = "改完之后的检查没通过。"
    return "没改成。\n\n原因：%s\n下一步：我已经改回原来的样子。先别连续试。请让懂配置的同事看一下配置文件，或再发「设置」。" % why


def commit_toggle(cfg_path, base, feature_id, turn_on, names=(), check=None, user=""):
    if feature_id not in _BY_ID:
        return {"ok": False, "text": _sensitive_text()}
    if not _safe_cfg(base, cfg_path):
        _audit(base, "refuse_path", user=user, feature=feature_id, names=names, ok=False)
        return {"ok": False, "text": _fail_text("read")}
    try:
        backup = backup_config(cfg_path, base)
    except OSError:
        _audit(base, "backup_failed", user=user, feature=feature_id, names=names, ok=False)
        return {"ok": False, "text": _fail_text("backup")}
    _audit(base, "backup", user=user, feature=feature_id, names=names)
    try:
        original = open(cfg_path, "rb").read()
        data = json.loads(original.decode("utf-8-sig"))
    except (OSError, ValueError):
        _audit(base, "read_failed", user=user, feature=feature_id, names=names, ok=False)
        return {"ok": False, "text": _fail_text("read")}
    if not isinstance(data, dict):
        return {"ok": False, "text": _fail_text("read")}
    new = mutate(data, feature_id, turn_on)
    tmp = cfg_path + ".writing"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(new, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, cfg_path)
    except OSError:
        _restore_quiet(cfg_path, backup)
        _audit(base, "write_failed", user=user, feature=feature_id, names=names, ok=False)
        return {"ok": False, "text": _fail_text("read")}
    checker = check or _default_check
    try:
        errors, _hints = checker(cfg_path)
    except Exception as exc:
        _restore_quiet(cfg_path, backup)
        _audit(base, "check_failed", user=user, feature=feature_id, names=names, ok=False, reason=type(exc).__name__)
        return {"ok": False, "text": _fail_text("check")}
    if errors:
        _restore_quiet(cfg_path, backup)
        _audit(base, "rollback", user=user, feature=feature_id, names=names, ok=False, reason="check")
        return {"ok": False, "text": humanize_check(errors)}
    _audit(base, "write", user=user, feature=feature_id, on=turn_on, names=names)
    return {"ok": True, "backup": backup, "text": ""}


def _restore_quiet(cfg_path, backup):
    try:
        restore_file(cfg_path, backup)
    except OSError:
        pass


def _job_path(base):
    return os.path.join(base, "state", "switch_job.json")


def _result_path(base):
    return os.path.join(base, "state", "switch_result.json")


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _write_job(base, cfg_path, backup, ctx, item, turn_on):
    _write_json(_job_path(base), {
        "cfg": cfg_path,
        "backup": backup,
        "user": ctx.get("user") or "",
        "feature": item["id"],
        "on": bool(turn_on),
    })


def _write_result(base, user, text):
    _write_json(_result_path(base), {"user": user or "", "text": text})


def abandon(base, cfg_path=None):
    """启动重启失败时改回备份，并删掉「已经成功」的待发送结果。"""
    path = _job_path(base)
    try:
        with open(path, encoding="utf-8") as handle:
            job = json.load(handle)
    except (OSError, ValueError):
        job = {}
    backup = job.get("backup") if isinstance(job, dict) else ""
    target = cfg_path or (job.get("cfg") if isinstance(job, dict) else "")
    if backup and target and os.path.isfile(backup) and os.path.isfile(target):
        try:
            restore_file(target, backup)
        except OSError:
            pass
    for extra in (path, _result_path(base)):
        try:
            os.remove(extra)
        except OSError:
            pass
    _audit(base, "rollback", feature=(job or {}).get("feature") or "", ok=False, reason="spawn")
    return _fail_text("spawn")


def reloader_argv(base, cfg_path):
    script = os.path.join(base, "scripts", "reload.ps1")
    if os.name == "nt" and os.path.isfile(script):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Config", cfg_path]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_switch.py"),
            "--reload", "--base", base, "--config", cfg_path]


def launch_reloader(base, cfg_path, popen=None):
    argv = reloader_argv(base, cfg_path)
    popen = popen or subprocess.Popen
    kwargs = {"cwd": base, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    popen(argv, **kwargs)
    _audit(base, "restart", ok=True)


def execute_reload(env, restore=None):
    """先停，确认没有本目录的接收进程，再启动恰好一个。还有进程就不要再启动。"""
    env.stop()
    env.wait_until_dead()
    if env.our_pids():
        if restore:
            restore()
        return {"ok": False, "started": 0, "reason": "still_running", "rolled_back": bool(restore)}

    def once():
        if env.our_pids():
            raise RuntimeError("refusing second receiver")
        env.start()

    try:
        once()
    except Exception:
        rolled = bool(restore)
        if restore:
            restore()
        if env.our_pids():
            return {"ok": False, "started": 0, "reason": "start_failed", "rolled_back": rolled}
        try:
            once()
        except Exception:
            return {"ok": False, "started": 0, "reason": "start_failed", "rolled_back": rolled}
        if len(list(env.our_pids())) > 1:
            return {"ok": False, "started": 1, "reason": "not_single", "rolled_back": rolled}
        return {"ok": False, "started": 1, "reason": "restarted_old", "rolled_back": rolled}
    if len(list(env.our_pids())) > 1:
        return {"ok": False, "started": 1, "reason": "not_single", "rolled_back": False}
    return {"ok": True, "started": 1, "reason": "restarted", "rolled_back": False}


class LiveEnv:
    """给 --reload 用。只写停止请求并等待，不扩大 stop.ps1 的结束范围。Windows 正式路径走 reload.ps1。"""

    def __init__(self, base, wait_sec=20):
        self.base = base
        self.wait_sec = wait_sec
        self.poll = 0.5
        self._started = False

    def our_pids(self):
        return find_our_pids(self.base)

    def stop(self):
        lifecycle.request_shutdown(os.path.join(self.base, "state"))

    def wait_until_dead(self):
        deadline = time.time() + self.wait_sec
        while self.our_pids() and time.time() < deadline:
            time.sleep(self.poll)

    def start(self):
        if self.our_pids():
            raise RuntimeError("still running")
        cfg = ""
        job = _read_json(_job_path(self.base))
        if isinstance(job, dict):
            cfg = job.get("cfg") or ""
        if not cfg or not os.path.isfile(cfg):
            raise RuntimeError("no config")
        subprocess.Popen([sys.executable, os.path.join(self.base, "agentbot.py"), cfg],
                         cwd=self.base, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        self._started = True


def find_our_pids(base):
    found = []
    base_r = os.path.realpath(base)
    proc = "/proc"
    if os.path.isdir(proc):
        for name in os.listdir(proc):
            if not name.isdigit():
                continue
            try:
                raw = open(os.path.join(proc, name, "cmdline"), "rb").read()
            except OSError:
                continue
            cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
            if "agentbot.py" not in cmd:
                continue
            if base_r in cmd or base in cmd:
                found.append(int(name))
        return found
    for rel in ("bot.pid", os.path.join("state", "bot.lock")):
        path = os.path.join(base, rel)
        try:
            line = open(path, encoding="utf-8").readline().strip()
        except OSError:
            continue
        if line.isdigit():
            pid = int(line)
            if pid not in found:
                found.append(pid)
    return found


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _welcome_state(base):
    return _read_json(os.path.join(base, "state", "guide.json"))


def _mark_welcome(base, token):
    path = os.path.join(base, "state", "guide.json")
    data = _welcome_state(base)
    sent = [x for x in (data.get("sent") or []) if isinstance(x, str)]
    if token and token not in sent:
        sent.append(token)
    _write_json(path, {"sent": sent, "at": time.time()})


def welcome_sent(base, user):
    token = who(user)
    return bool(token) and token in (_welcome_state(base).get("sent") or [])


def push_targets(cfg):
    """刚装好时只私聊主人，不把每个管理员都叫一遍，也不往群里发。"""
    cfg = cfg or {}
    found = []
    report = cfg.get("report") if isinstance(cfg.get("report"), dict) else {}
    alerts = cfg.get("alerts") if isinstance(cfg.get("alerts"), dict) else {}
    _add_ids(found, report.get("owner_open_ids"))
    _add_ids(found, alerts.get("owner_open_ids"))
    return found


def push_welcome(cfg, base, names, send):
    text = guard.redact(welcome_text(cfg), names)
    sent_now = []
    for uid in push_targets(cfg):
        if welcome_sent(base, uid):
            continue
        key = outbound.idem_key("welcome|" + who(uid))
        result = send("user", uid, text, key)
        if result in ("ok", "unknown", True):
            _mark_welcome(base, who(uid))
            sent_now.append(who(uid))
            _audit(base, "welcome", user=uid, names=names)
    return sent_now


def maybe_welcome_user(cfg, base, user, chat_type, names, send):
    if chat_type != "p2p" or not is_manager(cfg, user):
        return False
    if welcome_sent(base, user):
        return False
    text = guard.redact(welcome_text(cfg), names)
    key = outbound.idem_key("welcome|" + who(user))
    result = send("user", user, text, key)
    if result in ("ok", "unknown", True):
        _mark_welcome(base, who(user))
        _audit(base, "welcome", user=user, names=names)
        return True
    return False


def deliver_result(base, send):
    path = _result_path(base)
    data = _read_json(path)
    user = data.get("user") or ""
    text = data.get("text") or ""
    if not user or not text:
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return False
    key = outbound.idem_key("switch-result|" + who(user))
    result = send("user", user, text, key)
    if result in ("ok", "unknown", True):
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.remove(_job_path(base))
        except OSError:
            pass
        _audit(base, "result", user=user)
        return True
    return False


def card_event(obj):
    """卡片按钮。没订阅这个事件时不会进来。普通消息返回 None。"""
    if not isinstance(obj, dict):
        return None
    header = obj.get("header") if isinstance(obj.get("header"), dict) else {}
    kind = str(header.get("event_type") or obj.get("event_type") or "")
    if "card.action" not in kind:
        return None
    event = obj.get("event") if isinstance(obj.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    item = feature(str(value.get("switch") or ""))
    if not item:
        return None
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    user = str(operator.get("open_id") or "")
    chat = str(context.get("open_chat_id") or "")
    mid = str(context.get("open_message_id") or header.get("event_id") or "")
    if not (_ACTOR.match(user) and _ACTOR.match(chat) and _ACTOR.match(mid)):
        return None
    return {
        "message_id": mid,
        "chat_id": chat,
        "chat_type": "p2p",
        "sender_id": user,
        "sender_type": "user",
        "message_type": "text",
        "content": "第%d项" % item["n"],
        "mentions": [],
    }


def reload_main(argv):
    base, cfg = "", ""
    if "--base" in argv:
        i = argv.index("--base")
        base = argv[i + 1] if i + 1 < len(argv) else ""
    if "--config" in argv:
        i = argv.index("--config")
        cfg = argv[i + 1] if i + 1 < len(argv) else ""
    if not base or not cfg:
        print("reload needs --base and --config")
        return 2
    job = _read_json(_job_path(base))
    backup = job.get("backup") if isinstance(job, dict) else ""

    def restore():
        if backup and os.path.isfile(backup):
            restore_file(cfg, backup)

    env = LiveEnv(base)
    result = execute_reload(env, restore=restore if backup else None)
    if result.get("ok"):
        return 0
    user = job.get("user") if isinstance(job, dict) else ""
    if user:
        _write_result(base, user, _fail_text(result.get("reason") or "start_failed"))
    _audit(base, "reload_failed", user=user, ok=False, reason=result.get("reason") or "")
    return 1


def _flag(argv, name):
    if name not in argv:
        return ""
    i = argv.index(name)
    return argv[i + 1] if i + 1 < len(argv) else ""


def reload_failed_main(argv):
    """重启没做成。改回备份，并留下一句私聊，等下次启动再发给主人。"""
    base = _flag(argv, "--base")
    reason = _flag(argv, "--reason") or "still_running"
    if reason not in ("still_running", "start_failed", "spawn"):
        reason = "still_running"
    if not base:
        return 2
    job = _read_json(_job_path(base))
    backup = job.get("backup") or ""
    cfg = job.get("cfg") or ""
    if backup and cfg and _safe_cfg(base, cfg) and _safe_cfg(base, backup):
        try:
            restore_file(cfg, backup)
        except OSError:
            pass
    user = job.get("user") or ""
    if user:
        _write_result(base, user, _fail_text(reason))
    _audit(base, "reload_failed", user=user, ok=False, reason=reason)
    return 0


def main(argv):
    if "--reload-failed" in argv:
        return reload_failed_main(argv)
    if "--reload" in argv:
        return reload_main(argv)
    print("这个文件由机器人在私聊里调用。")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
