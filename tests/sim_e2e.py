#!/usr/bin/env python3
# 离线模拟：一题里读两份 PDF、一份 Word 和群历史；工具结果超预算时压缩且不报错。
# 不连接飞书，不使用真实账号、群号或密钥。python3 tests/sim_e2e.py
import json, os, re, shutil, subprocess, sys, tempfile, time, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import utf8io  # noqa: F401
FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)
        if detail:
            print(str(detail)[-2500:])


def ensure_pypdf():
    try:
        import pypdf  # noqa: F401
        return
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pypdf"])


def text_pdf(path, line):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    writer.add_blank_page(width=400, height=200)
    page = writer.pages[-1]
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })
    content = DecodedStreamObject()
    content.set_data(("BT /F1 12 Tf 36 100 Td (%s) Tj ET" % line).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(path)


def mini_docx(path, paragraph):
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>" % paragraph
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def prompts_of(text):
    found = []
    for line in text.splitlines():
        if "fake prompt: " not in line:
            continue
        raw = line.split("fake prompt: ", 1)[1]
        try:
            found.append(json.loads(raw))
        except Exception:
            found.append(raw)
    return found


def load_cfg(base, **agent):
    cfg = json.load(open(os.path.join(ROOT, "agents", "example.json"), encoding="utf-8-sig"))
    cfg["base_dir"] = base
    cfg["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
    cfg["progress"] = {"enabled": False}
    cfg.setdefault("memory", {})["enabled"] = False
    cfg.setdefault("agent", {}).pop("max_tool_rounds", None)
    cfg["agent"].pop("model", None)
    for key, value in agent.items():
        if value is None:
            cfg["agent"].pop(key, None)
        else:
            cfg["agent"][key] = value
    os.makedirs(os.path.join(base, "kb"), exist_ok=True)
    open(os.path.join(base, "redact.txt"), "w", encoding="utf-8").write("")
    return cfg


def stage(base, mid, filename, src):
    folder = os.path.join(base, "tmp", "att_" + re.sub(r"\W", "", mid)[-20:], "lark-im-resources", "x")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, filename)
    shutil.copy(src, dest)
    return dest


def file_msg(mid, chat, name, when):
    return {
        "message_id": mid,
        "chat_id": chat,
        "create_time": when,
        "msg_type": "file",
        "content": json.dumps({"file_key": "file_" + mid, "file_name": name}, ensure_ascii=False),
        "sender": {"name": "甲"},
    }


def mget(message):
    return {"ok": True, "data": {"messages": [message]}}


def prepare_files(base, chat):
    """准备两份有文字的 PDF、一份 Word，以及一份不该被读到的文本。"""
    pdf_a = os.path.join(base, "alpha.pdf")
    pdf_b = os.path.join(base, "beta.pdf")
    docx = os.path.join(base, "note.docx")
    secret = os.path.join(base, "secret.txt")
    # 非中文页至少要有 12 个字母数字，才会当成有文字的 PDF。
    text_pdf(pdf_a, "PDFALPHA REPORT ONE")
    text_pdf(pdf_b, "PDFBETA REPORT TWO")
    mini_docx(docx, "WORDGAMMA")
    open(secret, "w", encoding="utf-8").write("SECRETOTHER")
    when = str(int(time.time() * 1000))
    ids = {
        "pdf_a": "om_simpdfa",
        "pdf_b": "om_simpdfb",
        "word": "om_simword",
        "bad": "om_simbadc",
    }
    names = {"pdf_a": "alpha.pdf", "pdf_b": "beta.pdf", "word": "note.docx", "bad": "secret.txt"}
    srcs = {"pdf_a": pdf_a, "pdf_b": pdf_b, "word": docx, "bad": secret}
    staged = {}
    messages = {}
    for key, mid in ids.items():
        staged[key] = stage(base, mid, names[key], srcs[key])
        use_chat = chat if key != "bad" else "chat_sim_other"
        messages[key] = file_msg(mid, use_chat, names[key], when)
    return ids, messages, staged


def run_bot(base, cfg, question, script, fake, by_id, mid):
    cfg_path = os.path.join(base, "sim.json")
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
    bot = cfg["bot_open_id"]
    name = cfg["bot_names"][0]
    event = {
        "message_id": mid,
        "chat_id": cfg["chat_ids"][0],
        "chat_type": "group",
        "message_type": "text",
        "sender_id": "user_sender",
        "sender_type": "user",
        "content": "@%s %s" % (name, question),
        "mentions": [{"id": bot, "key": "@_user_1", "name": name}],
    }
    ev_path = os.path.join(base, "events.jsonl")
    open(ev_path, "w", encoding="utf-8").write(json.dumps({"event": event}, ensure_ascii=False) + "\n")
    script_path = os.path.join(base, "script.json")
    json.dump({"rounds": script}, open(script_path, "w", encoding="utf-8"), ensure_ascii=False)
    fake_path = os.path.join(base, "fake.json")
    json.dump(fake, open(fake_path, "w", encoding="utf-8"), ensure_ascii=False)
    by_path = os.path.join(base, "byid.json")
    json.dump(by_id, open(by_path, "w", encoding="utf-8"), ensure_ascii=False)
    env = dict(os.environ)
    for key in ("AGENTBOT_AGENT_CMD", "AGENTBOT_FAKE_LARK_DELAY", "AGENTBOT_CATCHUP"):
        env.pop(key, None)
    env.update({
        "AGENTBOT_FAKE_AGENT": "0",
        "AGENTBOT_FAKE_DUMP_PROMPT": "1",
        "AGENTBOT_FAKE_LARK": fake_path,
        "AGENTBOT_FAKE_LARK_BY_ID": by_path,
        "AGENTBOT_FAKE_SCRIPT": script_path,
        "PYTHONUTF8": "0",
    })
    env.pop("PYTHONIOENCODING", None)
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "agentbot.py"), cfg_path, "--dry-run", ev_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=90, cwd=base,
    )
    return (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def calls(text, name):
    return len(re.findall(r"tool call %s\b" % re.escape(name), text))


def reply_has(text, mid, needle):
    return needle in text.split("[dry-run] reply %s: " % mid, 1)[-1]


def main():
    ensure_pypdf()
    root = tempfile.mkdtemp(prefix="agentbot_sim_")
    try:
        batch_default(root)
        batch_message_ids(root)
        one_round(root)
        over_budget(root)
        write_stops(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d failed" % len(FAILS))
    return 1 if FAILS else 0


def batch_default(root):
    base = os.path.join(root, "batch")
    os.makedirs(base)
    cfg = load_cfg(base)
    chat = cfg["chat_ids"][0]
    ids, messages, _staged = prepare_files(base, chat)
    history = [messages["pdf_a"], messages["pdf_b"], messages["word"]]
    fake = {"im +chat-messages-list": {"ok": True, "data": {"messages": history}}}
    by_id = {messages[k]["message_id"]: mget(messages[k]) for k in ("pdf_a", "pdf_b", "word")}
    script = [
        {"tool": "history", "args": {}},
        [
            {"tool": "message_attachment", "args": {"message_id": ids["pdf_a"]}},
            {"tool": "message_attachment", "args": {"message_id": ids["pdf_b"]}},
            {"tool": "message_attachment", "args": {"message_id": ids["word"]}},
        ],
        "三份材料都已读完",
    ]
    out, code = run_bot(base, cfg, "请读群历史以及两份 PDF 和一份 Word", script, fake, by_id, "simbatch")
    ps = prompts_of(out)
    later = "\n".join(ps[2:]) if len(ps) >= 3 else ""
    check("默认轮数内一轮多读：两份 PDF、Word 和历史都读到",
          code == 0 and calls(out, "history") == 1 and calls(out, "message_attachment") == 3
          and len(ps) == 3
          and "还可以再用 4 轮" in ps[0] and "还可以再用 4 轮" not in ps[1]
          and "还可以再用 3 轮" in ps[1] and "还可以再用 2 轮" in ps[2]
          and "PDFALPHA" in later and "PDFBETA" in later and "WORDGAMMA" in later
          and "PDFALPHA" not in ps[0] and "PDFALPHA" not in ps[1]
          and reply_has(out, "simbatch", "三份材料都已读完")
          and "Traceback" not in out and "这几步已经查完" not in out and "已压缩" not in out,
          out[-2000:])


def batch_message_ids(root):
    base = os.path.join(root, "ids")
    os.makedirs(base)
    cfg = load_cfg(base)
    chat = cfg["chat_ids"][0]
    ids, messages, _staged = prepare_files(base, chat)
    history = [messages["pdf_a"], messages["pdf_b"], messages["word"]]
    fake = {"im +chat-messages-list": {"ok": True, "data": {"messages": history}}}
    by_id = {messages[k]["message_id"]: mget(messages[k]) for k in ("pdf_a", "pdf_b", "word")}
    script = [
        {"tool": "history", "args": {}},
        {"tool": "message_attachment", "args": {"message_ids": [ids["pdf_a"], ids["pdf_b"], ids["word"]]}},
        "三份材料都已读完",
    ]
    out, code = run_bot(base, cfg, "请按消息列表读两份 PDF 和一份 Word", script, fake, by_id, "simids")
    ps = prompts_of(out)
    body = ps[2] if len(ps) >= 3 else ""
    check("message_ids 在默认轮数内分别标注并读完全部附件",
          code == 0 and calls(out, "message_attachment") == 1 and len(ps) == 3
          and "还可以再用 4 轮" in (ps[0] if ps else "")
          and "【第 1 条 %s】" % ids["pdf_a"] in body and "PDFALPHA" in body
          and "【第 2 条 %s】" % ids["pdf_b"] in body and "PDFBETA" in body
          and "【第 3 条 %s】" % ids["word"] in body and "WORDGAMMA" in body
          and "Traceback" not in out and "这几步已经查完" not in out,
          body[-1500:] if body else out[-1500:])


def one_round(root):
    base = os.path.join(root, "one")
    os.makedirs(base)
    cfg = load_cfg(base, max_tool_rounds=1)
    chat = cfg["chat_ids"][0]
    ids, messages, staged = prepare_files(base, chat)
    fake = {"im +chat-messages-list": {"ok": True, "data": {"messages": []}}}
    by_id = {messages[k]["message_id"]: mget(messages[k]) for k in ("pdf_a", "pdf_b", "word", "bad")}
    script = [
        [
            {"tool": "message_attachment", "args": {"message_id": ids["pdf_a"]}},
            {"tool": "message_attachment", "args": {"message_id": ids["pdf_b"]}},
            {"tool": "message_attachment", "args": {"message_id": ids["bad"]}},
            {"tool": "message_attachment", "args": {"message_id": ids["word"]}},
        ],
        "四条都处理完了",
    ]
    out, code = run_bot(base, cfg, "同一轮读这些附件", script, fake, by_id, "simone")
    ps = prompts_of(out)
    second = ps[1] if len(ps) >= 2 else ""
    check("同一轮按顺序读多条，对不上的会话不下载，其余仍读到",
          code == 0 and calls(out, "message_attachment") == 4 and len(ps) == 2
          and "PDFALPHA" in second and "PDFBETA" in second and "WORDGAMMA" in second
          and "当前会话" in second and "剩余 0 轮" in second
          and "SECRETOTHER" not in out and os.path.isfile(staged["bad"])
          and reply_has(out, "simone", "四条都处理完了") and "Traceback" not in out,
          second[-1500:] if second else out[-1500:])


def over_budget(root):
    base = os.path.join(root, "budget")
    os.makedirs(base)
    cfg = load_cfg(base, tool_context_budget_chars=350)
    long_text = "长" * 800
    fake = {"docs +fetch": {"ok": True, "data": {"document": {"content": long_text}}}}
    script = [
        {"tool": "docs_read", "args": {"doc": "doxSIMBIG1"}},
        {"tool": "docs_read", "args": {"doc": "doxSIMBIG2"}},
        "预算内已经回答",
    ]
    out, code = run_bot(base, cfg, "请读两份很长的文档", script, fake, {}, "simbudget")
    ps = prompts_of(out)
    third = ps[2] if len(ps) >= 3 else ""
    check("工具结果超过预算时压缩并截断，不报错",
          code == 0 and calls(out, "docs_read") == 2 and len(ps) == 3
          and "已压缩" in third and "已截断" in third
          and reply_has(out, "simbudget", "预算内已经回答")
          and "Traceback" not in out and "这几步已经查完" not in out,
          third[-800:] if third else out[-1500:])


def write_stops(root):
    base = os.path.join(root, "write")
    os.makedirs(base)
    cfg = load_cfg(base)
    chat = cfg["chat_ids"][0]
    ids, messages, _staged = prepare_files(base, chat)
    fake = {"im +chat-messages-list": {"ok": True, "data": {"messages": [messages["pdf_a"]]}}}
    by_id = {ids["pdf_a"]: mget(messages["pdf_a"])}
    script = [[
        {"tool": "history", "args": {}},
        {"tool": "docs_create", "args": {"title": "纪要", "markdown": "正文"}},
        {"tool": "message_attachment", "args": {"message_id": ids["pdf_a"]}},
    ]]
    out, code = run_bot(base, cfg, "先看历史再新建文档再读附件", script, fake, by_id, "simwrite")
    check("同一轮里写入被拒绝后不再读后面的附件",
          code == 0 and calls(out, "history") == 1 and calls(out, "docs_create") == 1
          and calls(out, "message_attachment") == 0
          and "不在写入白名单" in out and "docs +create" not in out and "PDFALPHA" not in out
          and "Traceback" not in out,
          out[-1500:])


if __name__ == "__main__":
    sys.exit(main())
