#!/usr/bin/env python3
# 端到端：假 lark-cli（事件流 + 按消息编号的 OpenAPI）和假模型，把 agentbot 整段跑一遍。
# 不连飞书、不使用真实群号或密钥。运行：python3 tests/sim_e2e.py
import base64, datetime, io, json, os, select, signal, subprocess, sys, tempfile, time, zipfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(FW, "tests", "sim_lark.py")
T = tempfile.mkdtemp(prefix="agentbot_sim_")
cfg = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
cfg["base_dir"] = T
cfg["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
cfg["p2p"] = {"allow_open_ids": ["user_anyone"], "open_to_all": True}
cfg["memory"] = {"enabled": False}
cfg["progress"] = {"enabled": False}
cfg["dry_run_live_reads"] = True
cfg["agent"] = dict(cfg.get("agent") or {}, max_runtime_sec=60, stall_sec=30)
cfg["agent"].pop("model", None)
cfg["write"]["allowed_users"] = {"user_admin": "管理员"}
os.makedirs(os.path.join(T, "kb"), exist_ok=True)
open(os.path.join(T, "redact.txt"), "w", encoding="utf-8").write("杭州某某科技有限公司\n")
CP = os.path.join(T, "c.json")
json.dump(cfg, open(CP, "w", encoding="utf-8"), ensure_ascii=False)
BIN = os.path.join(T, "bin")
os.makedirs(BIN)
os.chmod(SIM, 0o755)
os.symlink(SIM, os.path.join(BIN, "lark-cli"))
CH = cfg["chat_ids"][0]
BOT = cfg["bot_open_id"]
BN = cfg["bot_names"][0]
MENTION = [{"id": BOT, "key": "@_user_1", "name": BN}]
results = []

def check(name, cond, out=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        text = out if isinstance(out, str) else ""
        print(text[-2000:])

def prompts(o):
    return [json.loads(l.split("fake prompt: ", 1)[1]) for l in o.splitlines() if "fake prompt: " in l]

def ev(mid, text, **kw):
    e = {"message_id": mid, "chat_id": CH, "chat_type": "group", "message_type": "text",
         "sender_id": "user_sender", "sender_type": "user", "content": "@" + BN + " " + text, "mentions": MENTION}
    e.update(kw)
    return e

def run(events, scenario=None, name="case", extra_args=(), cfg_path=None, env_extra=None, timeout=60):
    """每行一个 JSON，原样交给 ingest。扁平消息和带 header 的信封都这样送。"""
    case = os.path.join(T, name)
    os.makedirs(case, exist_ok=True)
    events_path = os.path.join(case, "events.jsonl")
    open(events_path, "w", encoding="utf-8").write("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
    scen = os.path.join(case, "scenario.json")
    json.dump(scenario or {}, open(scen, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ)
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "")
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
    env.pop("AGENTBOT_FAKE_LARK", None)
    env["SIM_SCENARIO"] = scen
    env["SIM_LARK_LOG"] = os.path.join(case, "lark.log")
    env["SIM_LARK_STATE"] = os.path.join(case, "lark-state.json")
    for k, v in (env_extra or {}).items():
        if v is None: env.pop(k, None)
        else: env[k] = v
    r = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path or CP, "--dry-run", events_path, *extra_args],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout, cwd=FW)
    out = (r.stdout or "") + (r.stderr or "")
    logp = os.path.join(case, "lark.log")
    calls = []
    if os.path.isfile(logp):
        calls = [json.loads(l) for l in open(logp, encoding="utf-8") if l.strip()]
    return out, calls

def replied(o, mid):
    return ("[dry-run] reply %s" % mid) in o

o, _ = run([ev("sim_text", "今天天气如何")], name="text")
check("文字 @ 会回答", replied(o, "sim_text") and "agent start sim_text" in o, o)

o, _ = run([ev("sim_noat", "不 at", mentions=[], content="不 at")], name="noat")
check("群里未 @ 不回答", not replied(o, "sim_noat") and "agent start sim_noat" not in o, o)

o, _ = run([ev("sim_all", "所有人看一下", mentions=[{"id": "all", "key": "@_all", "name": "所有人"}],
               content="@所有人 所有人看一下")], name="atall")
check("@全员 不会把机器人叫起来", not replied(o, "sim_all") and "agent start sim_all" not in o, o)

o, _ = run([ev("sim_both", "也叫我", mentions=[{"id": "all", "name": "所有人"}] + MENTION)], name="atboth")
check("同时 @全员 和机器人时才回答", replied(o, "sim_both"), o)

o, _ = run([{"message_id": "sim_p2p", "chat_id": "chat_p2p_sim", "chat_type": "p2p", "message_type": "text",
             "sender_id": "user_anyone", "sender_type": "user", "content": "私聊直接问"}], name="p2p")
check("私聊不用 @ 也回答", replied(o, "sim_p2p"), o)

o, _ = run([ev("sim_bots", "两个机器人", mentions=MENTION + [{"id": "ou_otherbot", "name": "另一个机器人"}])], name="bots")
check("同时 @ 两个机器人时本机器人只答一次", o.count("[dry-run] reply sim_bots") == 1 and "ou_otherbot" not in o, o)

o, _ = run([ev("sim_edit", "第一版"), ev("sim_edit", "改过的第二版")], name="edit")
check("同一条消息再投递（编辑）不会答第二次", o.count("[dry-run] reply sim_edit") == 1, o)

file_scen = {
    "messages": {"sim_file": {"message_id": "sim_file", "chat_id": CH, "msg_type": "file",
                              "content": json.dumps({"file_key": "file_simkey1", "file_name": "说明.txt"}, ensure_ascii=False)}},
    "resources": {"sim_file": [{"name": "file_simkey1.txt", "text": "附件正文甲"}]},
}
o, calls = run([ev("sim_file", "这份讲什么", message_type="file",
                   content="@" + BN + " 这份讲什么")], scenario=file_scen, name="file")
ps = "\n".join(prompts(o))
check("当前消息的文件会下载，正文进提示词，file_key 不出现",
      replied(o, "sim_file") and "附件正文甲" in ps and "说明.txt" in ps and "file_simkey1" not in ps
      and any("--download-resources" in c for c in calls), o[-1500:])

png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
img_scen = {
    "messages": {"sim_img": {"message_id": "sim_img", "chat_id": CH, "msg_type": "image",
                             "content": json.dumps({"image_key": "img_simkey1"})}},
    "resources": {"sim_img": [{"name": "shot.png", "b64": png}]},
}
o, _ = run([ev("sim_img", "看这张图", message_type="image")], scenario=img_scen, name="image")
ps = "\n".join(prompts(o))
check("图片交给识图，不把 image_key 写进提示词",
      "图片" in ps and "已放在工作区" in ps and "img_simkey1" not in ps, o[-1500:])

post_body = json.dumps({"title": "说明", "content": [[{"tag": "text", "text": "帖子正文甲"}]]}, ensure_ascii=False)
post_scen = {"messages": {"sim_post": {"message_id": "sim_post", "chat_id": CH, "msg_type": "post", "content": post_body}}}
o, _ = run([ev("sim_post", "看这条", message_type="post")], scenario=post_scen, name="post")
ps = "\n".join(prompts(o))
check("富文本里的文字会交给模型", "帖子正文甲" in ps, o[-1500:])

card = json.dumps({"header": {"title": {"content": "卡片标题甲"}}, "elements": [{"tag": "div", "text": {"content": "卡片正文甲"}}]}, ensure_ascii=False)
card_scen = {"messages": {"sim_card": {"message_id": "sim_card", "chat_id": CH, "msg_type": "interactive", "content": card}}}
o, _ = run([ev("sim_card", "看卡片", message_type="interactive")], scenario=card_scen, name="card")
ps = "\n".join(prompts(o))
check("卡片标题和正文会交给模型", "卡片标题甲" in ps and "卡片正文甲" in ps, o[-1500:])

merge_scen = {
    "messages": {"sim_merge": {"message_id": "sim_merge", "chat_id": CH, "msg_type": "merge_forward", "content": "{}"}},
    "api": {"/open-apis/im/v1/messages/sim_merge": {"ok": True, "data": {"items": [
        {"msg_type": "text", "body": {"content": json.dumps({"text": "转发里的句子甲"}, ensure_ascii=False)}, "sender": {"name": "小李"}},
    ]}}},
}
o, calls = run([ev("sim_merge", "展开", message_type="merge_forward", content="[合并转发]")], scenario=merge_scen, name="merge")
ps = "\n".join(prompts(o))
check("合并转发展开一层", "转发里的句子甲" in ps and any(c[:2] == ["api", "GET"] for c in calls), o[-1500:])

def quiet(mid, mtype, content):
    o, calls = run([ev(mid, "看", message_type=mtype, content=content)], name=mtype)
    return o, calls

o, _ = quiet("sim_audio", "audio", json.dumps({"file_key": "file_audiokey1", "duration": 1200}))
check("语音只说明不能听，不调用模型，也不露出 file_key",
      "不能听" in o and replied(o, "sim_audio") and "agent start sim_audio" not in o and "file_audiokey1" not in o, o)

o, _ = quiet("sim_stk", "sticker", json.dumps({"file_key": "stk_secretkey1"}))
check("表情只说明没有文字，不露出 file_key",
      "表情" in o and "没有可读取的文字" in o and "agent start sim_stk" not in o and "stk_secretkey1" not in o, o)

o, _ = quiet("sim_user", "share_user", json.dumps({"user_id": "ou_shareuser1", "name": "外部用户"}))
check("联系人名片不展开，也不露出 open_id",
      "联系人名片" in o and "agent start sim_user" not in o and "ou_shareuser1" not in o, o)

o, _ = quiet("sim_chat", "share_chat", json.dumps({"chat_id": "oc_sharechat1"}))
check("群名片不打开别的群，也不主动发消息",
      "不会打开别的群" in o and "不会主动发消息" in o and "oc_sharechat1" not in o and "agent start sim_chat" not in o, o)

o, _ = run([ev("sim_sys", "邀请了某人", message_type="system",
               content=json.dumps({"text": "邀请了 ou_sysuser1"}, ensure_ascii=False))], name="system")
check("系统消息即使提到机器人也不回答",
      not replied(o, "sim_sys") and "agent start sim_sys" not in o and "ou_sysuser1" not in o and "skip system message sim_sys" in o, o)

recall = {"header": {"event_type": "im.message.recalled_v1", "event_id": "evt_recall_sim_1"},
          "event": {"message_id": "sim_recall", "chat_id": CH, "chat_type": "group", "message_type": "text",
                    "sender_id": "user_sender", "sender_type": "user", "mentions": MENTION,
                    "content": "@" + BN + " 撤回前的问题"}}
o, _ = run([recall], name="recall")
check("撤回事件不回答", "recall ignored" in o and not replied(o, "sim_recall") and "agent start sim_recall" not in o, o)

added = {"header": {"event_type": "im.chat.member.bot.added_v1", "event_id": "evt_add_sim_1"},
         "event": {"chat_id": "oc_newgroup1", "name": "新群", "operator_id": {"open_id": "ou_inviter1"}}}
o, _ = run([added], name="added")
check("进群事件不发消息", "reply " not in o and "ou_inviter1" not in o.split("chat event")[-1], o)

hist = {"commands": {"im +chat-messages-list": {"ok": True, "data": {"messages": [
    {"create_time": "2026-10-16 19:00", "content": "外部同事说了一句", "sender": {"name": "外**"}},
]}}}}
q = '<<FAKE_TOOL>>{"tool":"history","args":{}}<<END>> 群里最近说了什么'
o, _ = run([ev("sim_hist", q)], scenario=hist, name="hist")
ps = "\n".join(prompts(o))
check("历史里的已脱敏外部名字原样保留", "外**" in ps and "外部同事说了一句" in ps, o[-1500:])

rate = {"faults": {"im +chat-messages-list": [{"ok": False, "error": {"code": 99991400, "message": "frequency limit"}}]}}
o, calls = run([ev("sim_rate", q)], scenario=rate, name="rate")
ps = "\n".join(prompts(o))
check("飞书限流时告诉模型稍后再试", "飞书限流了，请稍后再试" in ps and "飞书接口调用失败" not in ps, o[-1500:])

jitter = {"faults": {"im +chat-messages-list": [
    {"ok": False, "error": {"message": "connection reset"}},
    {"ok": True, "data": {"messages": [{"create_time": "2026-10-16 19:00", "content": "抖动之后的句子", "sender": {"name": "小李"}}]}},
]}}
o, calls = run([ev("sim_jitter", q)], scenario=jitter, name="jitter", timeout=40)
ps = "\n".join(prompts(o))
n_list = sum(1 for c in calls if c[:2] == ["im", "+chat-messages-list"])
check("网络抖动会重试，成功后的历史仍交给模型",
      n_list >= 2 and "抖动之后的句子" in ps and "lark im +chat-messages-list retry" in o, o[-1500:])

long_cfg = json.loads(json.dumps(cfg))
long_cfg["agent"]["reply_chunk_chars"] = 80
long_path = os.path.join(T, "long.json")
json.dump(long_cfg, open(long_path, "w", encoding="utf-8"), ensure_ascii=False)
body = "甲" * 50 + "\n" + "乙" * 50 + "尾巴标记乙"
o, _ = run([ev("sim_long", "<<FAKE_REPLY>>" + body + "<<END>> 写长一点")], name="long", cfg_path=long_path)
check("超长回答拆成多条，每段带序号",
      "（1/2）" in o and "（2/2）" in o and "尾巴标记乙" in o, o[-800:])

o, _ = run([ev("sim_c1", "并发甲", sender_id="user_a"), ev("sim_c2", "并发乙", sender_id="user_b")],
           name="conc", extra_args=("--parallel",))
check("两个人同时 @ 都会得到回答", replied(o, "sim_c1") and replied(o, "sim_c2"), o[-800:])

def _blank_pdf_b64(pages):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")

def _docx_b64(paragraph):
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>' % paragraph)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return base64.b64encode(buf.getvalue()).decode("ascii")

def _file_msg(mid, name, key):
    return {"message_id": mid, "chat_id": CH, "msg_type": "file", "create_time": _now,
            "content": json.dumps({"file_key": key, "file_name": name}, ensure_ascii=False),
            "sender": {"name": "外**"}}

_now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
_pdf_id, _word_id, _more_id, _wiki_id = "om_extpdf1", "om_extword1", "om_extmore1", "om_wikimsg1"
_wiki_open = "https://example.feishu.cn/wiki/wikSIMCHAIN"
_wiki_shut = "https://example.feishu.cn/wiki/wikSIMFORBID"
_sum_cfg = json.loads(json.dumps(cfg))
_sum_cfg["agent"]["max_tool_rounds"] = 6
_sum_cfg.setdefault("tools", {}).setdefault("message_attachment", {})["max_per_answer"] = 2
_sum_path = os.path.join(T, "sum.json")
json.dump(_sum_cfg, open(_sum_path, "w", encoding="utf-8"), ensure_ascii=False)
_sum_seq = [
    {"tool": "history", "args": {}},
    {"tool": "docs_read", "args": {"doc": _wiki_open}},
    {"tool": "docs_read", "args": {"doc": _wiki_shut}},
    {"tool": "message_attachment", "args": {"message_id": _pdf_id}},
    {"tool": "message_attachment", "args": {"message_id": _word_id}},
    {"tool": "message_attachment", "args": {"message_id": _more_id}},
]
_sum_q = "<<FAKE_SEQ>>" + json.dumps(_sum_seq, ensure_ascii=False) + "<<END>> 总结群里最近的文件和文档"
_sum_scen = {
    "commands": {
        "im +chat-messages-list": {"ok": True, "data": {"messages": [
            _file_msg(_pdf_id, "杭州某某科技有限公司-报名.pdf", "file_extpdfkey1"),
            {"message_id": _wiki_id, "chat_id": CH, "msg_type": "post", "create_time": _now,
             "content": json.dumps({"content": [[{"tag": "a", "text": "报名说明", "href": _wiki_open}]]}, ensure_ascii=False),
             "sender": {"name": "外**"}},
            _file_msg(_word_id, "外部纪要.docx", "file_extwordkey1"),
            _file_msg(_more_id, "补充材料.txt", "file_extmorekey1"),
        ]}},
        "docs +fetch": {"ok": True, "data": {"document": {"content": "知识库正文链"}, "markdown": "不该出现的旧字段"}},
    },
    "faults": {"wiki +node-get": [
        {"ok": True, "data": {"node": {"obj_token": "doxSIM1", "obj_type": "docx", "title": "报名说明"}}},
        {"ok": False, "error": {"code": 131006, "message": "forbidden"}},
    ]},
    "messages": {
        _pdf_id: _file_msg(_pdf_id, "杭州某某科技有限公司-报名.pdf", "file_extpdfkey1"),
        _word_id: _file_msg(_word_id, "外部纪要.docx", "file_extwordkey1"),
        _more_id: _file_msg(_more_id, "补充材料.txt", "file_extmorekey1"),
    },
    "resources": {
        _pdf_id: [{"name": "scan.pdf", "b64": _blank_pdf_b64(12)}],
        _word_id: [{"name": "note.docx", "b64": _docx_b64("外部成员正文乙")}],
        _more_id: [{"name": "more.txt", "text": "不该下载的第三份"}],
    },
}
o, calls = run([ev("sim_sum", _sum_q)], scenario=_sum_scen, name="sum", cfg_path=_sum_path, timeout=90)
ps = "\n".join(prompts(o))
n_dl = sum(1 for c in calls if "--download-resources" in c)
n_wiki = sum(1 for c in calls if c[:2] == ["wiki", "+node-get"])
n_fetch = sum(1 for c in calls if c[:2] == ["docs", "+fetch"])
check("总结最近文件和文档：外部成员、扫描件、知识库、权限不足和下载上限一起走完",
      "[文件 某客户-报名.pdf id=%s]" % _pdf_id in ps and "外**" in ps and _wiki_open in ps
      and "知识库正文链" in ps and "不该出现的旧字段" not in ps
      and "没对应用开放" in ps and "飞书接口调用失败" not in ps
      and "扫描件，只看了前 10 页" in ps and "外部成员正文乙" in ps
      and "已经看了 2 个" in ps and "不该下载的第三份" not in ps
      and "杭州某某科技有限公司" not in ps
      and "file_extpdfkey1" not in ps and "file_extwordkey1" not in ps and "file_extmorekey1" not in ps
      and n_dl == 2 and "om_extmore1" not in [x for c in calls for x in c]
      and n_wiki == 2 and n_fetch == 1
      and "[fake] 已根据工具结果回答" in o, o[-2500:])

def run_consumer():
    case = os.path.join(T, "consumer")
    os.makedirs(case, exist_ok=True)
    live = ev("sim_live", "长连接上的问题")
    scen = {"streams": {"im.message.receive_v1": [live]}, "stream_hold": True}
    scen_path = os.path.join(case, "scenario.json")
    json.dump(scen, open(scen_path, "w", encoding="utf-8"), ensure_ascii=False)
    c = json.loads(json.dumps(cfg))
    c["base_dir"] = case
    cpath = os.path.join(case, "c.json")
    json.dump(c, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(case, "kb"), exist_ok=True)
    open(os.path.join(case, "redact.txt"), "w", encoding="utf-8").write("杭州某某科技有限公司\n")
    env = dict(os.environ)
    env["PATH"] = BIN + os.pathsep + env.get("PATH", "")
    env["AGENTBOT_FAKE_AGENT"] = "0"
    env["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
    env.pop("AGENTBOT_FAKE_LARK", None)
    env["SIM_SCENARIO"] = scen_path
    env["SIM_LARK_LOG"] = os.path.join(case, "lark.log")
    env["SIM_LARK_STATE"] = os.path.join(case, "lark-state.json")
    proc = subprocess.Popen([sys.executable, os.path.join(FW, "agentbot.py"), cpath],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            env=env, cwd=FW, start_new_session=True)
    buf = []
    deadline = time.time() + 25
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None: break
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None: break
                continue
            buf.append(line)
            if "reply sim_live ok=True" in line:
                break
    finally:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except OSError: pass
        try: proc.wait(timeout=5)
        except Exception:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except OSError: pass
    return "".join(buf)

o = run_consumer()
check("假事件流：长连接收到一条 @ 后真正发出回复", "reply sim_live ok=True" in o and "consumer im.message.receive_v1" in o, o[-2000:])

print("\n%d/%d passed" % (sum(r for _, r in results), len(results)))
sys.exit(0 if all(r for _, r in results) else 1)
