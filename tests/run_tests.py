# 离线测试（不连飞书、不调 cursor-agent）：python3 tests/run_tests.py
import json, os, re, subprocess, sys, tempfile, sqlite3, shutil
FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="agentbot_t_"); os.makedirs(os.path.join(T, "kb"))
# 配置模板：AGENTBOT_TEST_CFG 指定；否则用 agents/example.json（占位符配置，测试会改写到临时目录）
_cp = os.environ.get("AGENTBOT_TEST_CFG") or os.path.join(FW, "agents", "example.json")
cfg = json.load(open(_cp, encoding="utf-8-sig")); print("config template:", os.path.basename(_cp))
cfg["base_dir"] = T; cfg["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 100}
cfg["p2p"] = {"allow_open_ids": ["user_anyone"], "open_to_all": True}
cfg.setdefault("memory", {})["enabled"] = False  # 长期记忆用例单独打开（线上配置可能已开）
cfg["progress"] = {"enabled": False}  # 老用例不测进度；进度/流式用例单独打开
cfg["agent"].update({"max_runtime_sec": 1800, "stall_sec": 300}); cfg["agent"].pop("model", None)
cfg["write"]["allowed_users"] = {"user_admin": "管理员"}
cfg["write"]["resources"] = {"folder_token": "fldTEST", "docs_append_tokens": ["doxTEST"], "calendar_id": "cal_TEST", "base_token": "bscTEST", "table_id": "tblTEST"}
cfg["tools"]["base_read"].update({"base_token": "bscTEST", "table_id": "tblTEST"})
CP = os.path.join(T, "c.json"); json.dump(cfg, open(CP, "w", encoding="utf-8"), ensure_ascii=False)
open(os.path.join(T, "redact.txt"), "w", encoding="utf-8").write("杭州某某科技有限公司\n")
CH = cfg["chat_ids"][0]; BOT = cfg["bot_open_id"]; BN = cfg["bot_names"][0]; M = [{"id": BOT, "key": "@_user_1", "name": BN}]
DENY = {"ok": False, "error": {"type": "authorization", "subtype": "app_scope_not_applied", "code": 99991672, "message": "access denied"}}
FAKE = {"im +chat-messages-list": {"ok": True, "data": {"messages": [{"create_time": "2026-10-16 19:00", "content": "周五交报名材料", "sender": {"name": "小李"}}]}},
        "drive +search": DENY, "docs +create": {"ok": True, "data": {"url": "https://x.feishu.cn/docx/NEW"}}, "calendar +agenda": DENY,
        "im +messages-mget": {"ok": True, "data": {"messages": [{"msg_type": "file"}]}}}
FP = os.path.join(T, "fake.json"); json.dump(FAKE, open(FP, "w", encoding="utf-8"), ensure_ascii=False)
results = []
def run(events, extra_args=(), name="", cfg_path=None, stream=False, extra_env=None):
    p = os.path.join(T, name + ".jsonl")
    open(p, "w", encoding="utf-8").write("\n".join(json.dumps({"event": e}, ensure_ascii=False) for e in events) + "\n")
    env = dict(os.environ, AGENTBOT_FAKE_AGENT="0.3", AGENTBOT_FAKE_LARK=FP)
    if stream:  # 用假的流式 cursor-agent 走真实的 stream-json 读取路径
        env.pop("AGENTBOT_FAKE_AGENT"); env["AGENTBOT_AGENT_CMD"] = json.dumps([sys.executable, os.path.join(FW, "tests", "fake_stream_agent.py")])
    for k, v in (extra_env or {}).items():
        if v is None: env.pop(k, None)
        else: env[k] = v
    r = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path or CP, "--dry-run", p, *extra_args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=240)
    return r.stdout + r.stderr
def ev(mid, text, **kw):
    e = {"message_id": mid, "chat_id": CH, "chat_type": "group", "message_type": "text", "sender_id": "user_sender", "sender_type": "user", "content": "@" + BN + " " + text, "mentions": M}
    e.update(kw); return e
def check(name, cond, out=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name)
    if not cond: print(out[-1500:])

o = run([ev("m1", "你好"), ev("m1", "你好")], name="dedup"); check("去重：同一 message_id 只回一次", o.count("[dry-run] reply m1") == 1, o)
o = run([ev("m2", "不 at", mentions=[])], name="noat"); check("群里未 @ 不回复", "reply m2" not in o, o)
o = run([ev("m3", "x", chat_id="chat_other")], name="otherchat"); check("非白名单群不回复", "reply m3" not in o, o)
o = run([ev("m4", "x", sender_type="app")], name="bot"); check("忽略机器人消息", "reply m4" not in o, o)
o = run([ev("m5", "帮我看看今年的星座运势")], name="nopolicy"); check("未配置红线：任何话题都照常交给 agent 回答", "agent start m5" in o and "[dry-run] reply m5" in o, o)
def pcfg(name, **extra):
    c = json.loads(json.dumps(cfg)); c.pop("taboo_keywords", None); c.update(extra)
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
SAL = {"redlines": [{"name": "薪资", "keywords": ["薪资"], "action": "refuse", "reply": "这个话题请直接找负责人沟通。"}]}
o = run([ev("m5b", "大家的薪资是多少")], name="policy", cfg_path=pcfg("pol", policy=SAL))
check("配置了红线（薪资）：命中后按配置文案拒答，不调 agent", "这个话题请直接找负责人沟通" in o and "agent start m5b" not in o, o)
o = run([ev("m5c", "今天天气如何")], name="policy_miss", cfg_path=pcfg("pol2", policy=SAL))
check("配置了红线但没命中：照常回答", "agent start m5c" in o and "[dry-run] reply m5c" in o, o)
o = run([ev("m5d", "聊聊旧词")], name="legacy", cfg_path=pcfg("legacy", taboo_keywords=["旧词"], policy={"redlines": []}))
check("兼容旧键 taboo_keywords：命中拒答，不调 agent", "agent start m5d" not in o and "[dry-run] reply m5d" in o, o)
o = run([ev("m6", "第一问"), ev("m7", "追问", root_id="m6")], name="mem")
db = sqlite3.connect(os.path.join(T, "state", "state.db")); n = db.execute("select count(*) from turns where sess='m6'").fetchone()[0]
check("话题记忆：同 root_id 归入同一会话", n == 4 and "sess=m6" in o.split("Q m7")[1], o)
o = run([ev("m8", "/new", root_id="m6")], name="new"); n = db.execute("select count(*) from turns where sess='m6'").fetchone()[0]
check("/new 清空话题记忆", n == 0 and "已清空" in o, o)
p2p = {"message_id": "p1", "chat_id": "chat_p2p1", "chat_type": "p2p", "message_type": "text", "sender_id": "user_anyone", "sender_type": "user", "content": "私聊不用 at"}
o = run([p2p], name="p2p"); check("私聊无需 @ 也回复（白名单内）", "reply p1" in o and "sess=p2p:chat_p2p1" in o, o)
o = run([dict(p2p, message_id="p2", content='<<FAKE_TOOL>>{"tool":"history","args":{}}<<END>> 群里聊了啥')], name="p2phist")
check("私聊里不提供群历史", "私聊里不提供群历史" in o, o)
o = run([ev("m9", '<<FAKE_TOOL>>{"tool":"history","args":{}}<<END>> 最近聊了什么')], name="hist")
check("群历史工具：调用 chat-messages-list 并二次回答", "[fake-lark] im +chat-messages-list" in o and "reply m9" in o, o)
o = run([ev("m10", '<<FAKE_TOOL>>{"tool":"docs_search","args":{"query":"进度"}}<<END>> 找文档')], name="deny")
check("缺权限优雅降级：提示「这项权限未开」且不崩溃", "这项权限未开" in o and "Traceback" not in o and "reply m10" in o, o)
o = run([ev("m11", '<<FAKE_TOOL>>{"tool":"docs_create","args":{"title":"测试","markdown":"内容"}}<<END>> 建文档')], name="wdeny")
check("写入：非白名单用户被拒", "不在写入白名单" in o, o)
import re as _re
evs = [ev("m12", '<<FAKE_TOOL>>{"tool":"docs_create","args":{"title":"周报","markdown":"内容"}}<<END>> 建文档', sender_id="user_admin")]
o = run(evs, name="wprop"); code = (_re.search(r"确认 (\d{6})", o) or [None, None])[1]
check("写入：白名单用户得到确认码，未执行", code and "docs +create" not in o.split("reply m12")[0].split("[fake-lark]")[-1], o)
# 确认码只在同一进程内有效：同一批里测试 发起→他人确认（拒）→本人确认（执行）
evs = [ev("m13", '<<FAKE_TOOL>>{"tool":"docs_create","args":{"title":"周报","markdown":"内容"}}<<END>> 建文档', sender_id="user_admin")]
p = os.path.join(T, "w2.jsonl"); open(p, "w", encoding="utf-8").write(json.dumps({"event": evs[0]}, ensure_ascii=False) + "\n")
env = dict(os.environ, AGENTBOT_FAKE_AGENT="0.3", AGENTBOT_FAKE_LARK=FP)
pr = subprocess.Popen([sys.executable, "-c", f"""
import sys, json, re, runpy, threading, io
BN={BN!r}
sys.argv=['agentbot.py', {CP!r}, '--dry-run', {p!r}]
g = runpy.run_path({os.path.join(FW, 'agentbot.py')!r}, run_name='lib')
g['main']()
logs = open({os.path.join(T, 'logs', 'bot.log')!r}, encoding='utf-8').read()
code = re.findall(r'确认 (\\d{{6}})', logs)[-1]
base = json.loads({json.dumps(json.dumps(evs[0], ensure_ascii=False))})
g['handle'](dict(base, message_id='m14', content='@'+BN+' 确认 '+code, sender_id='user_other'))
g['handle'](dict(base, message_id='m15', content='@'+BN+' 确认 '+code))
g['handle'](dict(base, message_id='m16', content='@'+BN+' 确认 '+code))
"""], cwd=FW, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env)
o = pr.communicate()[0]
check("写入：他人确认被拒", "只有发起人本人" in o, o)
check("写入：本人确认后执行 docs +create 并返回链接", "[fake-lark] docs +create" in o and "已完成" in o and "docx/NEW" in o, o)
check("写入：确认码一次性", o.count("已完成") == 1 and "无效或已过期" in o, o)
aud = open(os.path.join(T, "audit", sorted(os.listdir(os.path.join(T, "audit")))[0]), encoding="utf-8").read()
check("审计日志记录 proposed/denied/done", all(k in aud for k in ("write_proposed", "write_denied", "write_done")), aud)
o = run([ev("m17", '<<FAKE_TOOL>>{"tool":"docs_append","args":{"doc":"doxOTHER","markdown":"x"}}<<END>> 追加', sender_id="user_admin")], name="wres")
check("写入：非白名单文档被拒", "不在可写白名单" in o, o)
o = run([ev("m18", "回答里提到 杭州某某科技有限公司 吗")], name="redact"); check("回复中客户名被替换为某客户", "杭州某某科技有限公司" not in o.split("[dry-run] reply m18")[1] and "某客户" in o, o)
o = run([dict(p2p, message_id="p3", message_type="file", content="[文件] a.txt")], name="att")
check("私聊文件消息：调用 messages-mget 下载并回答", "[fake-lark] im +messages-mget" in o and "reply p3" in o, o)
evs = [ev("c%d" % i, "并发%d" % i, sender_id="u%d" % i) for i in range(8)]
o = run(evs, ("--parallel",), name="conc"); check("并发上限 2 + 排队上限 4", "peak_active=2" in o and o.count("queue full") == 2, o)
# ---------- 流式读取 / 卡死保护 / 部分结果 / 进度 ----------
def mkcfg(name, mode="text", **agent):
    c = json.loads(json.dumps(cfg)); c["agent"].update(agent)
    c["progress"] = {"enabled": True, "mode": mode, "card_interval_sec": 1, "reaction": "OnIt", "done_reaction": "DONE", "status_message": True,
                     "fast_interval_sec": 1, "fast_phase_sec": 120, "slow_interval_sec": 60, "min_gap_sec": 0, "max_edits": 17}
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
def block(o, mid):  # 发给用户的最终文本（状态消息替换成的答案，或直接回复），含多行
    i = o.rfind("[dry-run] edit dry_status_%s: " % mid)
    if i < 0 or o[i:].split(": ", 1)[1].startswith(("⏳", "✅")): i = o.rfind("[dry-run] reply %s: " % mid)
    return o[i:].split(": ", 1)[1] if i >= 0 else ""
SCP = mkcfg("stream")
o = run([ev("s1", "<<STREAM_NORMAL>> 流式问题")], name="s_norm", cfg_path=SCP, stream=True)
b = block(o, "s1"); want = "哈哈答案第一段，同一段\n\n答案第二段同一段"
check("流式：最终答案与 text 模式一致（不含工具前的开场白、不重复冲刷）", b.startswith(want) and "我先查" not in b and want + "同一段" not in b, o)
check("进度：先加处理中表情，答完换完成表情", "[dry-run] react s1 OnIt" in o and "[dry-run] unreact s1" in o and "[dry-run] react s1 DONE" in o, o)
st = [l for l in o.splitlines() if "dry_status_s1" in l and "⏳" in l or "[dry-run] status(text) s1" in l]
check("进度：状态消息按阶段更新，显示文档标题和已用时间", any("正在读《" in l and "已用" in l for l in st) and any("正在整理答案" in l for l in st), o)
check("进度：状态里不显示本地路径、客户名已脱敏", st and not any(("C:" in l or "\\" in l or "杭州某某科技有限公司" in l) for l in st) and any("《某客户周报》" in l for l in st), o)
check("进度（text 模式）：状态消息被替换成答案（不另发）", "reply s1 ok=True via status edit" in o and "[dry-run] reply s1" not in o, o)
o = run([ev("s2", "<<STREAM_TOOL>> 最近聊了什么")], name="s_tool", cfg_path=SCP, stream=True)
check("流式：<<TOOL>> 工具行解析不破，二次作答", "tool call history" in o and "[fake-lark] im +chat-messages-list" in o and "根据工具结果：周五交报名" in block(o, "s2") and "<<TOOL>>" not in block(o, "s2"), o)
STALL = mkcfg("stall", stall_sec=2, max_runtime_sec=1800)
o = run([ev("s3", "<<STREAM_STALL>> 复杂题")], name="s_stall", cfg_path=STALL, stream=True); b = block(o, "s3")
check("卡死：发出已输出的部分结果并注明，且经过脱敏", "agent stopped: stall" in o and "已经写出的部分：先看第一步，" in b and "（时间到了，先给部分结果）" in b and "杭州某某科技有限公司" not in b and "某客户" in b, o)
o = run([ev("s4", "<<STREAM_STALL_EMPTY>> 复杂题")], name="s_empty", cfg_path=STALL, stream=True)
check("卡死且无部分文本：给拆小问题的提示", "这题比较复杂，没在时间内答完，可以拆成更小的问题再问我" in block(o, "s4") and "抱歉" not in block(o, "s4"), o)
o = run([ev("s5", "<<STREAM_STALL_TOOL>> 复杂题")], name="s_stalltool", cfg_path=STALL, stream=True)
check("卡死时半截工具行不外泄", "<<TOOL>>" not in block(o, "s5") and "这题比较复杂" in block(o, "s5"), o)
CAP = mkcfg("cap", stall_sec=2, max_runtime_sec=4)
o = run([ev("s6", "<<STREAM_CHATTY>> 一直输出")], name="s_cap", cfg_path=CAP, stream=True)
check("持续有输出不算卡死，只受防卡死总上限约束", "agent stopped: max_runtime" in o, o)
o = run([ev("s7", "<<STREAM_NORMAL>> 不用进度")], name="s_noprog", stream=True)
check("关闭进度时照常直接回复，不加表情", "react s7" not in o and block(o, "s7").startswith(want) and "[dry-run] reply s7" in o, o)
CARD = mkcfg("card", mode="card")
o = run([ev("s8", "<<STREAM_NORMAL>> 卡片进度")], name="s_card", cfg_path=CARD, stream=True)
cards = [l for l in o.splitlines() if "[dry-run] card " in l]
check("卡片：发进度卡片（默认收起的思考面板），并按阶段更新", "[dry-run] status(card) s8" in o and cards and '"collapsible_panel"' in cards[0] and '"expanded": false' in cards[0] and any("正在读《某客户周报》" in l for l in cards), o)
think = [l for l in cards if "整理答案结构" in l]
check("卡片：思考面板脱敏、去路径（只留标题），未配红线时不删任何话题", think and any("薪资" in l for l in think) and not any(("C:" in l or "kb/" in l or "\\\\" in l or "杭州某某科技有限公司" in l) for l in cards) and "《某客户周报》" in think[-1] and "《赛程》" in think[-1], o)
check("卡片：答案照常文本回复（格式不变），卡片改成已完成", block(o, "s8").startswith(want) and "[dry-run] reply s8" in o and any("✅ 已完成，用时" in l for l in o.splitlines() if "[dry-run] patch dry_status_s8" in l), o)
_pc = json.load(open(CARD, encoding="utf-8")); _pc["policy"] = {"redlines": [{"name": "薪资", "keywords": ["薪资"], "action": "drop", "apply_to": ["progress"]}]}
CARDP = os.path.join(T, "cardp.json"); json.dump(_pc, open(CARDP, "w", encoding="utf-8"), ensure_ascii=False)
o2 = run([ev("s8b", "<<STREAM_NORMAL>> 卡片进度 + 红线")], name="s_cardp", cfg_path=CARDP, stream=True)
cards2 = [l for l in o2.splitlines() if "[dry-run] card " in l]
check("卡片 + 红线只作用于进度：思考面板里命中的行被删，答案照常", cards2 and not any("薪资" in l for l in cards2) and any("整理答案结构" in l for l in cards2) and "[dry-run] reply s8b" in o2, o2)
o = run([ev("s9", "<<STREAM_STALL>> 卡片卡死")], name="s_cardstall", cfg_path=mkcfg("cardstall", mode="card", stall_sec=2), stream=True)
check("卡片 + 卡死：发部分结果，卡片注明", "（时间到了，先给部分结果）" in block(o, "s9") and "已先给部分结果" in o, o)

# ---------- 长期记忆 P1：只读群档案 ----------
CH2 = "chat_second_group"
def put_profile(chat, text):
    d = os.path.join(T, "memory", "groups", chat); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "GROUP.md"), "w", encoding="utf-8").write(text)
put_profile(CH, "# 群档案：测试群\n【成员与分工】小李：负责测试（档案标记AAA）\n【项目与进展】在给杭州某某科技有限公司做方案\n")
put_profile(CH2, "# 群档案：第二个群\n【成员与分工】小王：只属于二群（档案标记BBB）\n")
def mcfg(name, **mem):
    c = json.loads(json.dumps(cfg)); c["chat_ids"] = [CH, CH2]; c.setdefault("memory", {}).update(mem)
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
def prompts(o): return [json.loads(l.split("fake prompt: ", 1)[1]) for l in o.splitlines() if "fake prompt: " in l]
os.environ["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
o = run([ev("g1", "小李负责什么？"), ev("g2", "/记忆")], name="mem_off", cfg_path=mcfg("mem_off", enabled=False))
ps = prompts(o)
check("记忆关闭时：不注入群档案，/记忆 提示未开启", ps and "AAA" not in ps[0] and "还没开启长期记忆" in o and "AAA" not in o, o)
ON = mcfg("mem_on", enabled=True)
o = run([ev("g3", "小李负责什么？"), ev("g4", "再问一次小李负责什么？")], name="mem_on", cfg_path=ON)
ps = prompts(o)
check("开启后：本群档案注入 prompt，且客户名已脱敏", len(ps) == 2 and "AAA" in ps[0] and "某客户" in ps[0] and "杭州某某科技有限公司" not in ps[0], o)
check("档案位置：在规则和工具说明之后、提问之前（稳定前缀）", ps and ps[0].index(cfg["system_prompt"][:20]) < ps[0].index("AAA") < ps[0].index("提问：") and ("<<TOOL>>" not in ps[0] or ps[0].index("<<TOOL>>") < ps[0].index("AAA")), o)
pre = lambda x: x[: x.index("提问：")].split("以下是本话题之前的对话")[0]
check("同一群两次提问，档案前缀逐字相同", len(ps) == 2 and pre(ps[0]) == pre(ps[1]), o)
o = run([ev("g5", "/记忆")], name="mem_show", cfg_path=ON)
check("/记忆：只读展示本群档案（脱敏，不调 agent）", "AAA" in o and "某客户" in o and "杭州某某科技有限公司" not in o and "agent start g5" not in o, o)
o = run([ev("g6", "小王是谁？", chat_id=CH2), ev("g7", "/记忆", chat_id=CH2)], name="mem_iso", cfg_path=ON)
ps = prompts(o)
check("按 chat_id 隔离：二群只看到二群档案", ps and "BBB" in ps[0] and "AAA" not in ps[0] and "AAA" not in o and "BBB" in o, o)
o = run([ev("g8", "小李负责什么？", chat_type="p2p", chat_id="chat_p2p_x", sender_id="user_anyone", mentions=[]), ev("g9", "/记忆", chat_type="p2p", chat_id="chat_p2p_x", sender_id="user_anyone", mentions=[])], name="mem_p2p", cfg_path=ON)
ps = prompts(o)
check("私聊：不注入任何群档案，/记忆 说明私聊没有群档案", ps and "AAA" not in ps[0] and "BBB" not in ps[0] and "私聊里没有群档案" in o, o)
o = run([ev("g10", "这个群有档案吗？", chat_id=CH2)], name="mem_none", cfg_path=mcfg("mem_none", enabled=True, dir="memory_empty"))
ps = prompts(o)
check("没有档案文件时照常回答，不报错", ps and "长期档案" not in ps[0] and "[dry-run] reply g10" in o, o)
put_profile(CH, "# 群档案：长档案\n" + "\n".join("【第%d条】" % i + "很长的内容" * 12 for i in range(200)) + "\n结尾标记ZZZ\n")
o = run([ev("g11", "问一下")], name="mem_budget", cfg_path=mcfg("mem_budget", enabled=True, profile_budget_chars=1500))
ps = prompts(o)
blk = ps[0].split("不要向用户复述这段说明：\n", 1)[1].split("\n\n", 1)[0] if ps and "不要向用户复述这段说明" in ps[0] else ""
check("字数预算：档案超长时截断到 1500 字以内并注明", blk and len(blk) <= 1500 and "ZZZ" not in blk and "已按字数预算截断" in blk, o)
os.environ.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
sys.path.insert(0, FW); import memory as _mem
_gm = _mem.GroupMemory({"memory": {"enabled": True}}, T)
check("chat_id 校验：拒绝路径穿越和非法字符", _gm.path("../../etc") is None and _gm.path("a/../b") is None and _gm.path("bad id") is None and _gm.path(CH2).endswith("GROUP.md"))

# ---------- 长期记忆 P2：记一下 / 忘记 / 取代 ----------
C3, C4 = "chat_group_three", "chat_group_four"
d3 = os.path.join(T, "memory", "groups", C3); os.makedirs(d3, exist_ok=True)
seed = [{"id": "g-0007", "type": "rule", "subject": "提交次数", "content": "一共只能提交 3 次", "short": "一共只能提交 3 次", "status": "active", "uncertain": False},
        {"id": "g-0008", "type": "deadline", "subject": "讨论会", "content": "10/18 16:00 讨论会", "short": "10/18 16:00 讨论会（旧时间OLDT）", "status": "active", "uncertain": False}]
open(os.path.join(d3, "facts.jsonl"), "w", encoding="utf-8").write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in seed))
open(os.path.join(d3, "GROUP.md"), "w", encoding="utf-8").write("# 群档案：三群\n【截止与时间点】10/18 16:00 讨论会（旧时间OLDT）\n")
def p2cfg(name, **mem):
    c = json.loads(json.dumps(cfg)); c["chat_ids"] = [CH, CH2, C3, C4]; c.setdefault("memory", {}).update(dict(enabled=True, **mem))
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
P2 = p2cfg("p2")
def facts3(ch=C3):
    f = os.path.join(T, "memory", "groups", ch, "facts.jsonl")
    return [json.loads(l) for l in open(f, encoding="utf-8")] if os.path.exists(f) else []
os.environ["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
o = run([ev("r1", "记一下：讨论会：改到 10/19 15:00", chat_id=C3)], name="p2_deny", cfg_path=P2)
check("记一下：非白名单成员被拒，不写入", "只有写入白名单里的成员能修改群记忆" in o and len(facts3()) == 2, o)
o = run([ev("r2", "记一下：讨论会：改到 10/19 15:00（新时间NEWT）", chat_id=C3, sender_id="user_admin"), ev("r3", "讨论会几点？", chat_id=C3)], name="p2_sup", cfg_path=P2)
fs = facts3(); new = [x for x in fs if x["id"] == "g-0009"]; old = [x for x in fs if x["id"] == "g-0008"][0]
check("记一下：白名单成员写入，回复编号，同主题取代旧条目", "已记 #9" in o and "取代了 #8（原来是：10/18 16:00 讨论会（旧时间OLDT））" in o and new and new[0]["supersedes"] == ["g-0008"] and old["status"] == "superseded" and new[0]["how"] == "explicit" and new[0]["source"]["by"] == "user_admin", o)
ps = prompts(o)
check("取代后：下一个问题的 prompt 里只有新时间，没有旧时间", ps and "NEWT" in ps[-1] and "OLDT" not in ps[-1], o)
g3 = open(os.path.join(d3, "GROUP.md"), encoding="utf-8").read()
check("取代后：GROUP.md 重新生成（保留标题、去掉旧条目）", g3.startswith("# 群档案：三群") and "NEWT" in g3 and "OLDT" not in g3 and "一共只能提交 3 次" in g3, g3)
o = run([ev("r4", "/记忆", chat_id=C3)], name="p2_show", cfg_path=P2)
check("/记忆：带编号列出有效条目，注明有几条被取代，不调 agent", "#7 一共只能提交 3 次" in o and "#9 讨论会：改到 10/19 15:00（新时间NEWT）" in o and "另有 1 条已被新条目取代" in o and "OLDT" not in o.split("reply r4")[1] and "agent start r4" not in o, o)
o = run([ev("r5", "记住：客户：杭州某某科技有限公司要求周五交付，联系电话 13812345678", chat_id=C3, sender_id="user_admin")], name="p2_redact", cfg_path=P2)
raw = open(os.path.join(d3, "facts.jsonl"), encoding="utf-8").read()
check("记住：落盘前脱敏（客户名→某客户、手机号打码），回复提示已隐藏", "已记 #10" in o and "杭州某某科技有限公司" not in raw and "13812345678" not in raw and "某客户" in raw and "已自动隐藏" in o, o)
o = run([ev("r6", "/忘记 10", chat_id=C3, sender_id="user_admin"), ev("r7", "/忘记 99", chat_id=C3, sender_id="user_admin"), ev("r8", "/忘记 7", chat_id=C3)], name="p2_forget", cfg_path=P2)
raw = open(os.path.join(d3, "facts.jsonl"), encoding="utf-8").read()
aud = open(os.path.join(T, "memory", "_audit", "memory-audit.jsonl"), encoding="utf-8").read()
check("/忘记 N：真删（账本里没有原文），不存在的编号如实说，非白名单不能删", "已删除 #10" in o and "没有找到 #99" in o and "某客户要求周五交付" not in raw and '"g-0007"' in raw and o.count("只有写入白名单里的成员") == 1, o)
check("审计：记录记了/删了哪个编号，不记删掉的内容", '"action": "forget", "ids": ["g-0010"]' in aud and '"action": "remember", "ids": ["g-0009"]' in aud and "周五交付" not in aud, aud)
o = run([ev("r9", "记一下：周会改到周四", chat_id=C3, sender_id="user_admin")], name="p2_noreuse", cfg_path=P2)
check("编号不复用：删掉 #10 之后新记的是 #11", "已记 #11" in o, o)
o = run([ev("r10", "记一下：四群专属ISO4", chat_id=C4, sender_id="user_admin"), ev("r11", "/记忆", chat_id=C4), ev("r12", "/记忆", chat_id=C3)], name="p2_iso", cfg_path=P2)
check("按群隔离：四群记的只在四群，三群看不到", "ISO4" not in open(os.path.join(d3, "facts.jsonl"), encoding="utf-8").read() and "ISO4" in o.split("reply r11")[1].split("reply r12")[0] and "ISO4" not in o.split("reply r12")[1], o)
pv = dict(chat_type="p2p", chat_id="chat_p2p_y", sender_id="user_anyone", mentions=[])
o = run([ev("r13", "记一下：私聊里的事PRIV", **pv)], name="p2_p2p", cfg_path=P2)
check("私聊：记一下不进群记忆，给出说明", "私聊里说的事不会记进群记忆" in o and not os.path.exists(os.path.join(T, "memory", "groups", "chat_p2p_y")) and "PRIV" not in open(os.path.join(d3, "facts.jsonl"), encoding="utf-8").read(), o)
o = run([ev("r14", "记一下：关了也想记", sender_id="user_admin")], name="p2_off", cfg_path=mcfg("p2_off", enabled=False))
check("记忆关闭时：记一下提示未开启，不写入", "还没开启长期记忆" in o and not facts3(CH), o)
o = run([ev("r15", "记住了吗", chat_id=C3, sender_id="user_admin")], name="p2_notcmd", cfg_path=P2)
check("“记住了吗”不是记忆指令，照常交给 agent", "agent start r15" in o and "已记" not in o, o)
d4b = os.path.join(T, "memory", "groups", CH2)  # 二群只有人工 GROUP.md（P1 格式）、没有账本
o = run([ev("r16", "记一下：二群新事NEW2", chat_id=CH2, sender_id="user_admin")], name="p2_base", cfg_path=P2)
g2 = open(os.path.join(d4b, "GROUP.md"), encoding="utf-8").read()
check("只有人工档案的群：第一次记一下时保留原档案正文（存成 GROUP.base.md）再追加", "已记 #1" in o and "BBB" in g2 and "NEW2" in g2 and g2.startswith("# 群档案：第二个群") and os.path.exists(os.path.join(d4b, "GROUP.base.md")), g2 + o)
o = run([ev("r17", "记一下：大家的薪资是多少", chat_id=C3, sender_id="user_admin")], name="p2_policy", cfg_path=pcfg("p2pol", chat_ids=[C3], memory={"enabled": True}, policy={"redlines": [{"name": "薪资", "keywords": ["薪资"], "action": "refuse", "apply_to": ["memory"]}]}))
check("配置了只作用于记忆的红线：命中拒记", "按本机器人的规则不记录" in o and "薪资" not in open(os.path.join(d3, "facts.jsonl"), encoding="utf-8").read(), o)
os.environ.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
# /忘记全部：确认码只在同一进程内有效，这里直接调用记忆模块
_g = _mem.GroupMemory({"memory": {"enabled": True}, "write": {"allowed_users": {"user_admin": "管理员"}}}, T)
cx = {"chat_id": C4, "chat_type": "group", "user": "user_admin", "message_id": "x"}
r1 = _g.handle("/忘记全部", cx); code = (re.search(r"确认清空 (\d{6})", r1) or [None, None])[1]
r2 = _g.handle("确认清空 000000" if code != "000000" else "确认清空 111111", cx)
r3 = _g.handle("确认清空 %s" % code, dict(cx, user="user_other"))
r4 = _g.handle("确认清空 %s" % code, cx); r5 = _g.handle("确认清空 %s" % code, cx)
check("/忘记全部：要确认码；错码、别人、重复使用都不清空；本人正确确认后清空", code and "没有清空" in r2 and "只有写入白名单" in r3 and r4.startswith("已清空本群记忆（1 条）") and "没有清空" in r5 and not os.path.exists(os.path.join(T, "memory", "groups", C4, "GROUP.md")), "\n".join([r1, r2, r3, r4, r5]))

# ---------- 技能层：目录注入 + skill_read（默认关闭）----------
import skills as _sk
os.environ["AGENTBOT_FAKE_DUMP_PROMPT"] = "1"
def skcfg(name, budget=800, memory_on=False, enabled=True, directory="skills", preload=None, preload_budget=None):
    c = json.loads(json.dumps(cfg))
    c["skills"] = {"enabled": enabled, "dir": directory, "index_budget_chars": budget}
    if preload is not None: c["skills"]["preload"] = preload
    if preload_budget is not None: c["skills"]["preload_budget_chars"] = preload_budget
    if memory_on: c.setdefault("memory", {})["enabled"] = True
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
def write_skill(folder, desc, body, directory="skills", name=None, triggers=None):
    d = os.path.join(T, directory, folder); os.makedirs(d, exist_ok=True)
    trig = ("triggers: [%s]\n" % ", ".join(triggers)) if triggers else ""
    open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8").write("---\nname: %s\ndescription: %s\n%s---\n\n%s\n" % (name or folder, desc, trig, body))
qsame = "今天安排是什么"
o = run([ev("sk0", qsame)], name="sk_before"); p_before = prompts(o)[0]
write_skill("meeting-minutes", "整理会议纪要。用户要纪要、结论或待办时使用。只组织文字，不写入。", "# 整理会议纪要\n\n没有的信息写「未提及」。正文标记BODY_MEETING。\n")
write_skill("progress-table", "查团队进度表。用户问进度、负责人、截止或状态时使用。只读，不改表。", "# 查进度表\n\n表里没有的项写「表里没有」。正文标记BODY_PROGRESS。\n")
o = run([ev("sk1", qsame)], name="sk_off"); p_off = prompts(o)[0]
check("技能关闭：有技能文件也不注入，提示词与没有技能时逐字相同", p_before == p_off and "技能目录" not in p_off and "BODY_PROGRESS" not in p_off and "progress-table" not in p_off, p_off)
o = run([ev("sk2", '<<FAKE_TOOL>>{"tool":"skill_read","name":"progress-table"}<<END>> 读技能')], name="sk_off_read")
check("技能关闭：skill_read 当未知工具，不返回正文", "未知工具" in o and "BODY_PROGRESS" not in o and "Traceback" not in o, o)
put_profile(CH, "# 群档案：测试群\n【成员与分工】小李：负责测试（档案标记AAA）\n")
ON = skcfg("sk_on", memory_on=True)
o = run([ev("sk3", "进度如何"), ev("sk4", "再问一次进度", root_id="sk3")], name="sk_on", cfg_path=ON)
ps = prompts(o)
def sk_prefix(p): return p.split("以下是本话题之前的对话")[0].split("提问：")[0]
check("目录注入：工具说明之后、群档案之前，一行一个技能，首轮不含正文",
      len(ps) == 2 and ps[0].index("可用工具") < ps[0].index("技能目录") < ps[0].index("档案标记AAA") < ps[0].index("提问：")
      and "meeting-minutes：整理会议纪要" in ps[0] and "progress-table：查团队进度表" in ps[0]
      and ps[0].index("meeting-minutes：") < ps[0].index("progress-table：")
      and "BODY_PROGRESS" not in ps[0] and "BODY_MEETING" not in ps[0], ps[0] if ps else o)
check("技能目录是稳定前缀：话题里两次提问，目录及之前逐字相同", len(ps) == 2 and sk_prefix(ps[0]) == sk_prefix(ps[1]) and "技能目录" in sk_prefix(ps[0]), o)
write_skill("long-alpha", "甲" * 80 + "MARKER_ALPHA_END", "# 长技能甲\n\n步骤。\n", directory="skills_long")
write_skill("long-beta", "乙" * 80 + "MARKER_BETA_END", "# 长技能乙\n\n步骤。\n", directory="skills_long")
_budget = len(_sk.CATALOG_HEADER) + 1 + 30 + 1 + len(_sk.TRUNC_NOTE)
o = run([ev("sk5", "看目录")], name="sk_budget", cfg_path=skcfg("sk_budget", budget=_budget, directory="skills_long"))
ps = prompts(o); skblock = _sk.SkillSet({"skills": {"enabled": True, "dir": "skills_long", "index_budget_chars": _budget}, "redact_file": "redact.txt"}, T).prompt_block()
check("预算截断：目录不超过预算，排不下的技能不出现",
      ps and skblock and len(skblock) <= _budget and skblock in ps[0] and _sk.TRUNC_NOTE in skblock
      and "long-alpha" in skblock and "MARKER_ALPHA_END" not in skblock and "MARKER_BETA_END" not in skblock and "long-beta" not in ps[0], skblock + "\n" + (ps[0] if ps else o))
write_skill("redact-demo", "测试脱敏。用户提到这份演练技能时使用。", "# 脱敏演练\n\n这里提到杭州某某科技有限公司，电话 13812345678。\n正文标记SKILLBODY。\n")
o = run([ev("sk6", '<<FAKE_TOOL>>{"tool":"skill_read","name":"redact-demo"}<<END>> 按技能说')], name="sk_read", cfg_path=ON)
ps = prompts(o)
chunk = ""
if len(ps) >= 2 and "工具结果（skill_read）" in ps[1]:
    chunk = ps[1].split("工具结果（skill_read）", 1)[1].split("提问：", 1)[0]
check("skill_read：返回正文且不含 YAML 头，客户名和手机号已脱敏",
      "SKILLBODY" in chunk and "name: redact-demo" not in chunk and "SKILLBODY" not in ps[0]
      and "杭州某某科技有限公司" not in ps[1] and "13812345678" not in ps[1]
      and "某客户" in chunk and "手机号已隐藏" in chunk, o)
o = run([ev("sk7", '<<FAKE_TOOL>>{"tool":"skill_read","name":"no-such-skill"}<<END>> 读一个没有的')], name="sk_miss", cfg_path=ON)
check("skill_read：没有这个技能", "没有这个技能" in o and "Traceback" not in o and "SKILLBODY" not in o, o)
open(os.path.join(T, "OUTSIDE_SECRET.txt"), "w", encoding="utf-8").write("OUTSIDE_SECRET_MARKER\n")
_ss = _sk.SkillSet({"skills": {"enabled": True, "dir": "skills"}, "redact_file": "redact.txt"}, T)
_bad_names = ["../OUTSIDE_SECRET.txt", "..", "progress-table/../../OUTSIDE_SECRET.txt", "progress-table\\..\\secret",
              "/etc/passwd", "C:/Windows/system.ini", "progress-table/SKILL.md", "", "Not-A-Name"]
_unit_ok, _unit_msg = True, ""
for _n in _bad_names:
    try: _got = _ss.read(_n)
    except _sk.SkillError as _e: _got = str(_e)
    if "OUTSIDE_SECRET_MARKER" in (_got or "") or (_n and "技能名字不合法" not in _got and "没有给出技能名字" not in _got):
        _unit_ok, _unit_msg = False, "%r -> %s" % (_n, _got); break
o = run([ev("sk8", '<<FAKE_TOOL>>{"tool":"skill_read","name":"../../OUTSIDE_SECRET.txt"}<<END>> 读外面')], name="sk_trav", cfg_path=ON)
check("路径穿越被拒：不读目录外的文件", _unit_ok and "技能名字不合法" in o and "OUTSIDE_SECRET_MARKER" not in o and "Traceback" not in o, _unit_msg + "\n" + o)
write_skill("ok-skill", "合法的示例技能，用来检查数量。", "# 好\n\n步骤。\n", directory="skills_check")
write_skill("BadName", "这个名字有大写，不合法。", "# 坏例子\n\n步骤。\n", directory="skills_check", name="BadName")
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), skcfg("sk_check", directory="skills_check"), "--check"], capture_output=True, text=True, encoding="utf-8", timeout=60)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check：列出技能数量，并报告格式不合法的技能", "skills enabled=True 数量=1 不合法=1" in _co and "FAIL skill BadName" in _co and "小写" in _co, _co)
_repo = _sk.SkillSet({"skills": {"enabled": True, "dir": "skills"}}, FW)
_by = {s.name: s for s in _repo.valid}
_rb = _repo.read("progress-table") if "progress-table" in _by else ""
_mb = _repo.read("meeting-minutes") if "meeting-minutes" in _by else ""
check("仓库示例技能格式合法：查进度表、整理会议纪要",
      [s.name for s in _repo.valid] == ["meeting-minutes", "progress-table"] and not _repo.invalid
      and "base_read" in _rb and "表里没有" in _rb and "name: progress-table" not in _rb
      and "未提及" in _mb and "不写入" in _mb and "name: meeting-minutes" not in _mb
      and "进度表" in _by["progress-table"].triggers and "谁负责" in _by["progress-table"].triggers
      and "会议纪要" in _by["meeting-minutes"].triggers and "待办" in _by["meeting-minutes"].triggers,
      _repo.status() + str(_by["progress-table"].triggers) + str(_by["meeting-minutes"].triggers))
_inline, _ibody, _ierr = _sk.parse_skill('---\nname: demo-skill\ndescription: 演示触发词。\ntriggers: ["进度表", 纪要]\n---\n\n步骤。\n')
_block, _bbody, _berr = _sk.parse_skill("---\nname: demo-skill\ndescription: 演示触发词。\ntriggers:\n  - 进度表\n  - 纪要\n---\n\n步骤。\n")
check("triggers：一行列表和缩进列表都能解析",
      _ierr is None and (_inline or {}).get("triggers") == ["进度表", "纪要"] and "步骤" in (_ibody or "")
      and _berr is None and (_block or {}).get("triggers") == ["进度表", "纪要"], str(_inline) + str(_block) + str(_ierr) + str(_berr))
def _head(p):
    cut = len(p)
    for m in ("\n\n以下是本话题之前的对话", "\n\n" + _sk.PRELOAD_MARK, "\n\n提问："):
        i = p.find(m)
        if i != -1: cut = min(cut, i)
    return p[:cut]
def _pre_body(p):
    mark = "\n\n" + _sk.PRELOAD_MARK
    i = p.find(mark)
    if i < 0: return ""
    rest = p[i + 2:]
    j = rest.find("\n\n提问：")
    return rest if j < 0 else rest[:j]
put_profile(CH, "# 群档案：测试群\n【成员与分工】小李：负责测试（档案标记PREAAA）\n")
write_skill("progress-table", "查团队进度表。", "# 查进度表\n\n这里提到杭州某某科技有限公司，电话 13812345678。\n正文标记PROG_BODY。\n", directory="skills_pre", triggers=["进度表", "进度"])
write_skill("meeting-minutes", "整理会议纪要。", "# 整理会议纪要\n\n正文标记MEET_BODY。\n", directory="skills_pre", triggers=["会议纪要", "纪要"])
PRE = skcfg("sk_pre", memory_on=True, directory="skills_pre")
o = run([ev("pr0", "今天天气怎么样"), ev("pr1", "看一下进度表", root_id="pr0"), ev("pr2", "进度和会议纪要都要")], name="sk_pre", cfg_path=PRE)
ps = prompts(o)
_b1 = _pre_body(ps[1]) if len(ps) > 1 else ""
_b2 = _pre_body(ps[2]) if len(ps) > 2 else ""
check("触发词命中：正文放在档案和历史之后、提问之前，已脱敏，不含 YAML 头",
      len(ps) >= 2 and "档案标记PREAAA" in ps[1] and ps[1].index("档案标记PREAAA") < ps[1].index(_sk.PRELOAD_MARK) < ps[1].index("提问：")
      and "以下是本话题之前的对话" in ps[1] and ps[1].index("以下是本话题之前的对话") < ps[1].index(_sk.PRELOAD_MARK)
      and "PROG_BODY" in _b1 and "MEET_BODY" not in _b1 and "name: progress-table" not in ps[1]
      and "杭州某某科技有限公司" not in ps[1] and "13812345678" not in ps[1]
      and "某客户" in _b1 and "手机号已隐藏" in _b1 and "progress-table" in _b1, ps[1] if len(ps) > 1 else o)
check("触发词未命中：不预放正文，目录仍在",
      ps and "PROG_BODY" not in ps[0] and "MEET_BODY" not in ps[0] and _sk.PRELOAD_MARK not in ps[0]
      and "技能目录" in ps[0] and "progress-table：查团队进度表" in ps[0], ps[0] if ps else o)
check("多个触发词命中：只预放得分最高的一个",
      len(ps) >= 3 and "MEET_BODY" in _b2 and "PROG_BODY" not in _b2 and _b2.count(_sk.PRELOAD_MARK) == 1 and "meeting-minutes" in _b2, _b2)
check("预放不改变稳定前缀：目录和档案在预放之前，同一话题两次提问逐字相同",
      len(ps) >= 2 and _head(ps[0]) == _head(ps[1]) and "技能目录" in _head(ps[0]) and "档案标记PREAAA" in _head(ps[0])
      and "PROG_BODY" not in _head(ps[0]) and _sk.PRELOAD_MARK not in _head(ps[0]), _head(ps[0])[-300:] + "\n---\n" + _head(ps[1])[-300:])
_long = "甲" * 80 + "ENDMARKER_LONG"
write_skill("progress-table", "查团队进度表。", "# 长正文\n\n" + _long + "\n", directory="skills_pre_budget", triggers=["进度表"])
_pbudget = 40
o = run([ev("pr3", "看进度表")], name="sk_pre_budget", cfg_path=skcfg("sk_pre_budget", directory="skills_pre_budget", preload_budget=_pbudget))
ps = prompts(o); _inner = _pre_body(ps[0]) if ps else ""; _nl = _inner.find("\n"); _inner = _inner[_nl + 1:] if _nl >= 0 else _inner
check("预放预算：正文超过 preload_budget_chars 时截断",
      ps and _sk.PRELOAD_NOTE in _inner and len(_inner) <= _pbudget and "ENDMARKER_LONG" not in ps[0] and _sk.PRELOAD_MARK in ps[0], _inner + " len=%d" % len(_inner))
o = run([ev("pr4", "看一下进度表")], name="sk_pre_off", cfg_path=skcfg("sk_pre_off", enabled=False, directory="skills_pre"))
ps = prompts(o)
check("skills.enabled=false：有触发词也不预放，提示词里没有技能目录和正文",
      ps and "PROG_BODY" not in ps[0] and "MEET_BODY" not in ps[0] and _sk.PRELOAD_MARK not in ps[0] and "技能目录" not in ps[0] and "progress-table" not in ps[0], ps[0] if ps else o)
o = run([ev("pr5", "看一下进度表")], name="sk_pre_nopreload", cfg_path=skcfg("sk_pre_nopreload", directory="skills_pre", preload=False))
ps = prompts(o)
check("skills.preload=false：仍放目录，不预放正文",
      ps and "技能目录" in ps[0] and "progress-table：查团队进度表" in ps[0] and "PROG_BODY" not in ps[0] and _sk.PRELOAD_MARK not in ps[0], ps[0] if ps else o)
_fc = json.load(open(PRE, encoding="utf-8")); _fc["model_provider"] = "fake"
json.dump(_fc, open(os.path.join(T, "sk_pre_fake.json"), "w", encoding="utf-8"), ensure_ascii=False)
o = run([ev("pr6", "看一下进度表")], name="sk_pre_fake", cfg_path=os.path.join(T, "sk_pre_fake.json"), extra_env={"AGENTBOT_FAKE_AGENT": None})
ps = prompts(o)
check("model_provider=fake：命中触发词也预放正文",
      "model provider fake" in o and ps and "PROG_BODY" in ps[0] and _sk.PRELOAD_MARK in ps[0] and "某客户" in ps[0] and "杭州某某科技有限公司" not in ps[0], ps[0] if ps else o)
_ctrace = os.path.join(T, "sk_pre_cursor.trace")
if os.path.exists(_ctrace): os.remove(_ctrace)
_cc = json.load(open(PRE, encoding="utf-8")); _cc["model_provider"] = "cursor"
json.dump(_cc, open(os.path.join(T, "sk_pre_cursor.json"), "w", encoding="utf-8"), ensure_ascii=False)
o = run([ev("pr7", "看一下进度表")], name="sk_pre_cursor", cfg_path=os.path.join(T, "sk_pre_cursor.json"), stream=True, extra_env={"AGENTBOT_STREAM_TRACE": _ctrace})
_ct = open(_ctrace, encoding="utf-8").read() if os.path.exists(_ctrace) else ""
check("model_provider=cursor：命中触发词也预放正文",
      "model provider cursor" in o and "PROG_BODY" in _ct and _sk.PRELOAD_MARK in _ct
      and _ct.index("技能目录") < _ct.index(_sk.PRELOAD_MARK) < _ct.index("提问：")
      and "杭州某某科技有限公司" not in _ct and "某客户" in _ct, (o[-400:] + "\n" + _ct[-500:]))
os.environ.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)

# ---------- 常驻 ACP（假服务端；默认 oneshot 不走这条路径）----------
import acp_client as _acp
_snap = "规则\n\n以下是本群的长期档案（说明）：\nSNAP_OLD\n\n提问：后来"
_kept = _acp.apply_snapshot(_snap.replace("SNAP_OLD", "SNAP_NEW"), "以下是本群的长期档案（说明）：\nSNAP_OLD")
_wiped = _acp.apply_snapshot("规则\n\n以下是本群的长期档案：\nNEW\n\n提问：x", "")
check("档案快照：换回会话开始时的正文，开始时没有档案就不再插入",
      "SNAP_OLD" in _kept and "SNAP_NEW" not in _kept and "提问：后来" in _kept and "长期档案" not in _wiped and _wiped.endswith("提问：x"), _kept + "\n" + _wiped)
_pre_prompt = "规则\n\n以下是本群的长期档案（说明）：\nSNAP_NEW\n\n本次预放技能（progress-table）：\nNEW_BODY\n\n提问：进度"
_pre_kept = _acp.apply_snapshot(_pre_prompt, "以下是本群的长期档案（说明）：\nSNAP_OLD")
check("档案快照停在预放标记之前，不吞掉会变的技能正文",
      "SNAP_OLD" in _pre_kept and "SNAP_NEW" not in _pre_kept and "NEW_BODY" in _pre_kept and "提问：进度" in _pre_kept
      and _sk.PRELOAD_MARK == _acp.PRELOAD_MARK and ("\n\n" + _acp.PRELOAD_MARK) in _acp._PROFILE_NEXT, _pre_kept)
check("模型配置项：一致、没有可选值、不在列表里都跳过；不一致时用返回的 configId 和可选值",
      _acp._model_set_params({"configOptions": [{"id": "model", "category": "model", "type": "select", "currentValue": "grok-4.7-high-fast", "options": [{"value": "grok-4.7-high-fast"}]}]}, "grok-4.7-high-fast") is None
      and _acp._model_set_params({"configOptions": []}, "grok-4.7-high-fast") is None
      and _acp._model_set_params({"configOptions": [{"id": "model", "category": "model", "currentValue": "auto", "options": []}]}, "grok-4.7-high-fast") is None
      and _acp._model_set_params({"configOptions": [{"id": "model", "category": "model", "currentValue": "other", "options": [{"value": "other"}]}]}, "grok-4.7-high-fast") is None
      and _acp._model_set_params({"configOptions": [{"configId": "cursor_model", "category": "model", "currentValue": "auto", "options": [{"group": "g", "options": [{"value": "model-value-id", "name": "grok-4.7-high-fast"}]}]}]}, "grok-4.7-high-fast") == ("cursor_model", "model-value-id")
      and _acp._model_set_params({"configOptions": [{"id": "model", "category": "model", "currentValue": "auto", "options": [{"value": "grok-4.7-high-fast"}]}]}, "grok-4.7-high-fast") == ("model", "grok-4.7-high-fast"))
FAKE_ACP = os.path.join(FW, "tests", "fake_acp_agent.py")
def acfg(name, progress=False, memory_on=False, **agent):
    c = json.loads(json.dumps(cfg))
    c["agent"].update({"mode": "acp", "stall_sec": 30, "max_runtime_sec": 60, "network_retries": 0, "acp_max_sessions": 8, "acp_idle_sec": 1800})
    c["agent"].update(agent)
    if memory_on:
        c.setdefault("memory", {})["enabled"] = True
    if progress:
        c["progress"] = {"enabled": True, "mode": "card", "card_interval_sec": 1, "reaction": "OnIt", "done_reaction": "DONE", "status_message": True,
                         "fast_interval_sec": 1, "fast_phase_sec": 120, "slow_interval_sec": 60, "min_gap_sec": 0, "max_edits": 17, "thinking_panel": True, "thinking_chars": 1200}
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
def load_trace(path):
    if not os.path.exists(path): return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
def run_acp(events, name, cfg_path, extra_env=None, args=()):
    trace = os.path.join(T, name + ".trace")
    if os.path.exists(trace): os.remove(trace)
    p = os.path.join(T, name + ".jsonl")
    open(p, "w", encoding="utf-8").write("\n".join(json.dumps({"event": e}, ensure_ascii=False) for e in events) + "\n")
    env = dict(os.environ, AGENTBOT_FAKE_LARK=FP, AGENTBOT_ACP_TRACE=trace, AGENTBOT_AGENT_CMD=json.dumps([sys.executable, FAKE_ACP]))
    env.pop("AGENTBOT_FAKE_AGENT", None); env.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
    if extra_env: env.update(extra_env)
    r = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path, "--dry-run", p, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=90)
    return (r.stdout or "") + (r.stderr or ""), load_trace(trace), r.returncode
def prompts_of(tr):
    return [e for e in tr if e.get("event") == "prompt"]
def new_ids(tr):
    ids = []
    for e in tr:
        if e.get("event") == "rpc" and e.get("method") == "session/new":
            pass
        if e.get("event") == "prompt":
            ids.append(e.get("sessionId"))
    # sessionId 在 prompt 上；新建次数看 rpc
    return ids, [e for e in tr if e.get("method") == "session/new"]
ACP_CFG = acfg("acp_base")
o, tr, rc = run_acp([ev("ac1", "<<ACP_PROGRESS>> 看进度")], "acp_ok", acfg("acp_prog", progress=True))
check("常驻：正常问答走 ACP，思考和读文件进进度卡片",
      rc == 0 and "常驻答案" in block(o, "ac1") and "acp fallback" not in o and "acp ready" in o
      and any("正在读《周报》" in l for l in o.splitlines()) and any("整理答案结构" in l for l in o.splitlines()), o)
argv0 = next((e.get("argv") or [] for e in tr if e.get("event") == "argv"), [])
check("常驻启动参数：--mode ask、--model、子命令 acp",
      argv0[argv0.index("--mode") + 1] == "ask" and "acp" in argv0 and "--model" in argv0 and "grok-4.7-high-fast" in argv0, str(argv0))
check("常驻握手：initialize、authenticate、session/set_mode=ask、set_config_option",
      any(e.get("method") == "initialize" for e in tr) and any(e.get("method") == "authenticate" for e in tr)
      and any(e.get("method") == "session/set_mode" and (e.get("params") or {}).get("modeId") == "ask" for e in tr)
      and any(e.get("method") == "session/set_config_option" and (e.get("params") or {}).get("configId") == "model" and (e.get("params") or {}).get("value") == "grok-4.7-high-fast" for e in tr), str(tr)[:1500])
def model_calls(tr):
    return [e for e in tr if e.get("method") == "session/set_config_option"]
o, tr, rc = run_acp([ev("acp_same", "普通一句")], "acp_same", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "same"})
check("模型配置项当前值已与配置一致：不调用 set_config_option",
      rc == 0 and not model_calls(tr) and "常驻答案" in block(o, "acp_same") and "acp set_model" not in o and "acp fallback" not in o, o)
o, tr, rc = run_acp([ev("acp_none", "普通一句")], "acp_none", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "none"})
o2, tr2, rc2 = run_acp([ev("acp_empty", "普通一句")], "acp_empty", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "empty"})
check("没有可用的模型配置项：不调用 set_config_option",
      not model_calls(tr) and not model_calls(tr2) and "常驻答案" in block(o, "acp_none") and "常驻答案" in block(o2, "acp_empty")
      and "acp set_model" not in o and "acp set_model" not in o2, o + o2)
o, tr, rc = run_acp([ev("acp_unlisted", "普通一句")], "acp_unlisted", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "unlisted"})
check("配置的模型不在可选值里：不调用 set_config_option",
      not model_calls(tr) and "常驻答案" in block(o, "acp_unlisted") and "acp fallback" not in o and "Invalid params" not in o, o)
o, tr, rc = run_acp([ev("acp_grp", "分组模型")], "acp_grouped", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "grouped"})
_calls = model_calls(tr)
check("当前值不一致：用返回的 configId 和可选值调用 set_config_option",
      len(_calls) == 1 and (_calls[0].get("params") or {}).get("configId") == "cursor_model"
      and (_calls[0].get("params") or {}).get("value") == "model-value-id"
      and "常驻答案" in block(o, "acp_grp") and "acp fallback" not in o, o + str(_calls))
o, tr, rc = run_acp([ev("acp_bad", "设模型失败")], "acp_invalid", ACP_CFG, extra_env={"AGENTBOT_ACP_CONFIG_CASE": "invalid"})
_calls = model_calls(tr)
check("set_config_option 失败只记 debug：这一题仍成功，日志没有这条 WARNING",
      len(_calls) == 1 and "常驻答案" in block(o, "acp_bad") and "acp fallback" not in o
      and "WARNING acp set_model" not in o and "Invalid params" not in o, o + str(_calls))
o, tr, rc = run_acp([ev("ac2", "第一题"), ev("ac3", "第二题", root_id="ac2"), ev("ac4", "另一题")], "acp_reuse", ACP_CFG)
ids, news = new_ids(tr)
check("同话题复用会话，不同话题分开",
      rc == 0 and len(news) == 2 and len(ids) == 3 and ids[0] == ids[1] and ids[0] != ids[2]
      and o.count("acp session reuse") >= 1 and o.count("acp session new") >= 2, o + str(ids))
o, tr, rc = run_acp([ev("ac5", "<<ACP_PERM>> 写一下"), ev("ac6", "<<ACP_PERM_BARE>> 再写")], "acp_perm", ACP_CFG)
perms = [e.get("outcome") or {} for e in tr if e.get("event") == "permission_result"]
check("权限请求一律拒绝：有拒绝项就选 reject-once，没有就 cancelled",
      "已拒绝权限请求" in block(o, "ac5") and "已拒绝权限请求" in block(o, "ac6") and "权限被允许" not in o
      and any(p.get("optionId") == "reject-once" for p in perms) and any(p.get("outcome") == "cancelled" for p in perms), o + str(perms))
o, tr, rc = run_acp([ev("ac7", "<<ACP_STALL>> 卡住")], "acp_stall", acfg("acp_stall", stall_sec=2, max_runtime_sec=30))
check("卡住后这一题回退到 -p，不把半截常驻输出当答案",
      "acp fallback: stall" in o and "一次性回退答案" in block(o, "ac7") and "卡住前的片段" not in block(o, "ac7")
      and any(e.get("event") == "argv" and "-p" in (e.get("argv") or []) for e in tr), o)
o, tr, rc = run_acp([ev("ac8", "<<ACP_SLOW>> 一直有输出")], "acp_cap", acfg("acp_cap", stall_sec=30, max_runtime_sec=3))
check("常驻总时长超过 max_runtime_sec 也回退到 -p", "acp fallback: max_runtime" in o and "一次性回退答案" in block(o, "ac8"), o)
o, tr, rc = run_acp([ev("ac9", "<<ACP_CRASH>> 崩了"), ev("ac10", "崩完再问")], "acp_crash", ACP_CFG)
n_init = sum(1 for e in tr if e.get("method") == "initialize")
check("进程崩溃：这一题回退到 -p，后台重启后下一题仍走常驻",
      "acp fallback:" in o and "process exited" in o and "一次性回退答案" in block(o, "ac9")
      and "常驻答案" in block(o, "ac10") and "acp fallback" not in o.split("Q ac10", 1)[-1]
      and n_init >= 2 and "acp restart" in o, o + " inits=%d" % n_init)
o, tr, rc = run_acp([ev("ac11", "<<ACP_PROTO>> 协议错")], "acp_proto", ACP_CFG)
check("协议出错：这一题回退到 -p", "acp fallback:" in o and "simulated protocol error" in o and "一次性回退答案" in block(o, "ac11"), o)
o, tr, rc = run_acp([ev("ac12", "<<ACP_TOOL>> 最近聊了什么")], "acp_tool", ACP_CFG)
check("常驻模式下工具回合仍可用",
      "acp fallback" not in o and "tool call history" in o and "[fake-lark] im +chat-messages-list" in o
      and "根据工具结果：周五交报名" in block(o, "ac12") and "<<TOOL>>" not in block(o, "ac12")
      and len(new_ids(tr)[1]) == 1 and o.count("acp session reuse") >= 1, o)
write_skill("progress-table", "查团队进度表。", "# 查进度表\n\n正文标记BODY_PROGRESS。\n", directory="skills_acp")
_skc = json.load(open(acfg("acp_skill"), encoding="utf-8"))
_skc["skills"] = {"enabled": True, "dir": "skills_acp", "index_budget_chars": 800}
json.dump(_skc, open(os.path.join(T, "acp_skill.json"), "w", encoding="utf-8"), ensure_ascii=False)
o, tr, rc = run_acp([ev("ac13", "<<ACP_SKILL>> 按技能说")], "acp_skill", os.path.join(T, "acp_skill.json"))
check("常驻模式下 skill_read 仍可用",
      "acp fallback" not in o and "tool call skill_read" in o and "按技能回答：BODY_PROGRESS" in block(o, "ac13")
      and "name: progress-table" not in block(o, "ac13") and len(new_ids(tr)[1]) == 1, o)
put_profile(CH, "# 群档案\n标记SNAP_OLD\n")
snap_src = r'''
import os, sys, time, runpy, json
os.environ["AGENTBOT_FAKE_LARK"] = %r
os.environ["AGENTBOT_ACP_TRACE"] = %r
os.environ["AGENTBOT_AGENT_CMD"] = json.dumps([%r, %r])
os.environ.pop("AGENTBOT_FAKE_AGENT", None)
sys.path.insert(0, %r)
sys.argv = ["agentbot.py", %r, "--dry-run"]
g = runpy.run_path(%r, run_name="lib")
base = {"chat_id": %r, "chat_type": "group", "message_type": "text", "sender_id": "user_sender", "sender_type": "user", "mentions": %s}
g["handle"](dict(base, message_id="ac14", content="@%s 先问档案"))
open(%r, "w", encoding="utf-8").write("# 群档案\n标记SNAP_NEW\n")
g["handle"](dict(base, message_id="ac15", root_id="ac14", content="@%s 再问档案"))
''' % (FP, os.path.join(T, "acp_snap.trace"), sys.executable, FAKE_ACP, FW, acfg("acp_snap", memory_on=True), os.path.join(FW, "agentbot.py"), CH, json.dumps(M), BN, os.path.join(T, "memory", "groups", CH, "GROUP.md"), BN)
if os.path.exists(os.path.join(T, "acp_snap.trace")): os.remove(os.path.join(T, "acp_snap.trace"))
env = dict(os.environ); env.pop("AGENTBOT_FAKE_AGENT", None); env.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
pr = subprocess.run([sys.executable, "-c", snap_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
o = (pr.stdout or "") + (pr.stderr or ""); tr = load_trace(os.path.join(T, "acp_snap.trace")); ps = prompts_of(tr)
check("会话开始时冻结群档案，中途改档案不进同一会话",
      len(ps) == 2 and "SNAP_OLD" in ps[0]["text"] and "SNAP_NEW" not in ps[0]["text"]
      and "SNAP_OLD" in ps[1]["text"] and "SNAP_NEW" not in ps[1]["text"] and "acp snapshot kept" in o, o + str([p["text"][-200:] for p in ps]))
write_skill("meeting-minutes", "整理会议纪要。", "# 纪要\n\n正文标记MEET_PRE。\n", directory="skills_pre_acp", triggers=["会议纪要"])
write_skill("progress-table", "查团队进度表。", "# 进度\n\n正文标记PROG_PRE。\n", directory="skills_pre_acp", triggers=["进度表"])
put_profile(CH, "# 群档案\n标记PRE_OLD\n")
_precfg = json.load(open(acfg("acp_pre", memory_on=True), encoding="utf-8"))
_precfg["skills"] = {"enabled": True, "dir": "skills_pre_acp", "index_budget_chars": 800, "preload": True}
json.dump(_precfg, open(os.path.join(T, "acp_pre.json"), "w", encoding="utf-8"), ensure_ascii=False)
_pre_trace = os.path.join(T, "acp_pre.trace")
pre_src = r'''
import os, sys, runpy, json
os.environ["AGENTBOT_FAKE_LARK"] = %r
os.environ["AGENTBOT_ACP_TRACE"] = %r
os.environ["AGENTBOT_AGENT_CMD"] = json.dumps([%r, %r])
os.environ.pop("AGENTBOT_FAKE_AGENT", None)
sys.path.insert(0, %r)
sys.argv = ["agentbot.py", %r, "--dry-run"]
g = runpy.run_path(%r, run_name="lib")
base = {"chat_id": %r, "chat_type": "group", "message_type": "text", "sender_id": "user_sender", "sender_type": "user", "mentions": %s}
g["handle"](dict(base, message_id="acpre0", content="@%s 请整理会议纪要"))
open(%r, "w", encoding="utf-8").write("# 群档案\n标记PRE_NEW\n")
g["handle"](dict(base, message_id="acpre1", root_id="acpre0", content="@%s 看一下进度表"))
''' % (FP, _pre_trace, sys.executable, FAKE_ACP, FW, os.path.join(T, "acp_pre.json"), os.path.join(FW, "agentbot.py"), CH, json.dumps(M), BN, os.path.join(T, "memory", "groups", CH, "GROUP.md"), BN)
if os.path.exists(_pre_trace): os.remove(_pre_trace)
env = dict(os.environ); env.pop("AGENTBOT_FAKE_AGENT", None); env.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
pr = subprocess.run([sys.executable, "-c", pre_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
o = (pr.stdout or "") + (pr.stderr or ""); tr = load_trace(_pre_trace); ps = prompts_of(tr)
_t0 = ps[0]["text"] if len(ps) > 0 else ""
_t1 = ps[1]["text"] if len(ps) > 1 else ""
check("常驻会话里预放技能留在快照之外：换题换正文，档案仍是开始时那份",
      len(ps) == 2 and "MEET_PRE" in _t0 and "PROG_PRE" not in _t0 and "PRE_OLD" in _t0 and "PRE_NEW" not in _t0
      and "PROG_PRE" in _t1 and "MEET_PRE" not in _t1 and "PRE_OLD" in _t1 and "PRE_NEW" not in _t1
      and _t1.index("PRE_OLD") < _t1.index(_sk.PRELOAD_MARK) < _t1.index("提问：")
      and "acp snapshot kept" in o and "acp fallback" not in o, o + _t0[-180:] + "\n---\n" + _t1[-180:])
idle_trace = os.path.join(T, "acp_idle.trace")
idle_src = '''
import os, sys, time, runpy, json
os.environ["AGENTBOT_FAKE_LARK"] = %r
os.environ["AGENTBOT_ACP_TRACE"] = %r
os.environ["AGENTBOT_AGENT_CMD"] = json.dumps([%r, %r])
os.environ.pop("AGENTBOT_FAKE_AGENT", None)
sys.argv = ["agentbot.py", %r, "--dry-run"]
g = runpy.run_path(%r, run_name="lib")
base = {"chat_id": %r, "chat_type": "group", "message_type": "text", "sender_id": "user_sender", "sender_type": "user", "mentions": %s}
g["handle"](dict(base, message_id="idle0", content="@%s 第一句"))
time.sleep(1.6)
g["handle"](dict(base, message_id="idle1", root_id="idle0", content="@%s 第二句"))
''' % (FP, idle_trace, sys.executable, FAKE_ACP, acfg("acp_idle", acp_idle_sec=1), os.path.join(FW, "agentbot.py"), CH, json.dumps(M), BN, BN)
if os.path.exists(idle_trace): os.remove(idle_trace)
pr = subprocess.run([sys.executable, "-c", idle_src], cwd=FW, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
o = (pr.stdout or "") + (pr.stderr or ""); ids, news = new_ids(load_trace(idle_trace))
check("空闲超过 acp_idle_sec：同一话题重新开会话",
      len(news) == 2 and len(ids) == 2 and ids[0] != ids[1] and "acp session drop idle" in o, o + str(ids))
o, tr, rc = run_acp([ev("cap0", "甲"), ev("cap1", "乙"), ev("cap2", "甲再问", root_id="cap0")], "acp_capn", acfg("acp_capn", acp_max_sessions=1))
ids, news = new_ids(tr)
check("会话数超过 acp_max_sessions：挤掉最久没用的，旧话题下次是新会话",
      len(news) == 3 and len(ids) == 3 and len(set(ids)) == 3 and "acp session evict" in o, o + str(ids))
o, tr, rc = run_acp([ev("nw0", "先聊"), ev("nw1", "/new", root_id="nw0"), ev("nw2", "清空后再问", root_id="nw0")], "acp_new", ACP_CFG)
ids, news = new_ids(tr)
check("新话题丢掉常驻会话，下一问重新建",
      "已清空" in o and "acp session drop" in o and len(news) == 2 and len(ids) == 2 and ids[0] != ids[1], o + str(ids))
o, tr, rc = run_acp([ev("os1", "不应走常驻")], "oneshot", acfg("oneshot_mode", mode="oneshot"))
check("mode=oneshot：命令行是 -p --mode ask，没有 acp 子命令",
      "acp ready" not in o and "acp session" not in o and "一次性回退答案" in block(o, "os1")
      and any(e.get("event") == "argv" and "-p" in e.get("argv", []) and "acp" not in e.get("argv", []) and e["argv"][e["argv"].index("--mode") + 1] == "ask" for e in tr)
      and not any(e.get("method") == "initialize" for e in tr), o + str([e for e in tr if e.get("event") == "argv"]))
o, tr, rc = run_acp([ev("ask1", "旧配置")], "legacy_ask", acfg("legacy_ask", mode="ask"))
check("旧的 mode=ask 行为不变：仍是一次性 --mode ask",
      "一次性回退答案" in block(o, "ask1") and "acp ready" not in o
      and any(e.get("event") == "argv" and e["argv"][e["argv"].index("--mode") + 1] == "ask" and "-p" in e["argv"] and "acp" not in e["argv"] for e in tr), o)
o, tr, rc = run_acp([ev("plan1", "计划")], "legacy_plan", acfg("legacy_plan", mode="plan"))
check("旧的 mode=plan 仍把 --mode plan 传给一次性进程",
      any(e.get("event") == "argv" and e["argv"][e["argv"].index("--mode") + 1] == "plan" and "-p" in e["argv"] for e in tr) and "acp ready" not in o, str(tr)[:800])
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), ACP_CFG, "--check"], capture_output=True, text=True, encoding="utf-8",
                     env=dict(os.environ, AGENTBOT_AGENT_CMD=json.dumps([sys.executable, FAKE_ACP]), AGENTBOT_ACP_TRACE=os.path.join(T, "acp_check.trace")), timeout=60)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check 在 mode=acp 时做 initialize 握手", "OK   acp" in _co and "protocolVersion=1" in _co and "FAIL acp" not in _co, _co)
_bad = dict(os.environ, AGENTBOT_AGENT_CMD=json.dumps([sys.executable, FAKE_ACP]), AGENTBOT_ACP_FAIL_INIT="1")
_bad.pop("AGENTBOT_FAKE_AGENT", None)
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), ACP_CFG, "--check"], capture_output=True, text=True, encoding="utf-8", env=_bad, timeout=60)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check 握手失败时报告原因并且退出码非 0", _cr.returncode != 0 and "FAIL acp" in _co and "process exited" in _co, _co)
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), acfg("check_oneshot", mode="oneshot"), "--check"], capture_output=True, text=True, encoding="utf-8", timeout=60)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("mode=oneshot 的 --check 不启动 ACP", "FAIL acp" not in _co and "OK   acp" not in _co and "acp spawn" not in _co, _co)
o, tr, rc = run_acp([ev("par%d" % i, "并行%d" % i) for i in range(2)], "acp_par", ACP_CFG, args=("--parallel",))
ids, news = new_ids(tr)
check("常驻进程上两题并行仍各有会话，并发上限沿用原来的设置",
      rc == 0 and "常驻答案" in block(o, "par0") and "常驻答案" in block(o, "par1") and len(news) == 2 and "Traceback" not in o, o + str(ids))

# ---------- OpenAI 兼容接口（本地假服务；不连接真的模型）----------
import fake_openai_server as _oas
_oa = _oas.serve()
_OA_URL = "http://127.0.0.1:%d/v1" % _oa.server_address[1]
_OA_SECRET = "unit-secret-value"
def ocfg(name, model="oa-model", **agent):
    c = json.loads(json.dumps(cfg))
    c["model_provider"] = "openai_compat"
    c["agent"].update({"model": model, "base_url": _OA_URL, "api_key_env": "UNIT_OPENAI_KEY",
                       "network_retries": 0, "stall_sec": 30, "max_runtime_sec": 60, "max_chars": 3500})
    c["agent"].update(agent)
    pth = os.path.join(T, name + ".json"); json.dump(c, open(pth, "w", encoding="utf-8"), ensure_ascii=False); return pth
def run_oa(events, name, cfg_path, retries_env=None, progress=False):
    if progress:
        c = json.load(open(cfg_path, encoding="utf-8"))
        c["progress"] = {"enabled": True, "mode": "card", "card_interval_sec": 1, "reaction": "OnIt", "done_reaction": "DONE",
                         "status_message": True, "fast_interval_sec": 1, "fast_phase_sec": 120, "slow_interval_sec": 60,
                         "min_gap_sec": 0, "max_edits": 17, "thinking_panel": True, "thinking_chars": 1200}
        json.dump(c, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
    p = os.path.join(T, name + ".jsonl")
    open(p, "w", encoding="utf-8").write("\n".join(json.dumps({"event": e}, ensure_ascii=False) for e in events) + "\n")
    env = dict(os.environ, AGENTBOT_FAKE_LARK=FP, UNIT_OPENAI_KEY=_OA_SECRET)
    env.pop("AGENTBOT_FAKE_AGENT", None); env.pop("AGENTBOT_FAKE_DUMP_PROMPT", None)
    r = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), cfg_path, "--dry-run", p], capture_output=True, text=True, encoding="utf-8", env=env, timeout=90)
    return (r.stdout or "") + (r.stderr or ""), r.returncode
def oa_hits(model):
    return [h for h in _oa.hits if h.get("model") == model]
o, rc = run_oa([ev("oa1", "普通问题")], "oa_stream", ocfg("oa_stream"))
check("openai_compat：流式文字拼成最终答案", "流式答案甲" in block(o, "oa1") and "model provider openai_compat" in o and _OA_SECRET not in o, o)
o, rc = run_oa([ev("oa2", "<<OA_REASON>> 想一想")], "oa_reason", ocfg("oa_reason"), progress=True)
check("openai_compat：reasoning_content 进思考进度，正文单独作答",
      "推理后的答案" in block(o, "oa2") and "整理答案结构" in o and "reasoning_content" not in block(o, "oa2"), o)
o, rc = run_oa([ev("oa3", "<<OA_TOOL>> 最近聊了什么")], "oa_tool", ocfg("oa_tool"))
check("openai_compat：工具回合仍走 <<TOOL>> 和白名单",
      "tool call history" in o and "[fake-lark] im +chat-messages-list" in o and "根据工具结果：周五交报名" in block(o, "oa3") and "<<TOOL>>" not in block(o, "oa3"), o)
write_skill("progress-table", "查团队进度表。", "# 查进度表\n\n正文标记BODY_PROGRESS。\n", directory="skills_oa")
_skc = json.load(open(ocfg("oa_skill"), encoding="utf-8"))
_skc["skills"] = {"enabled": True, "dir": "skills_oa", "index_budget_chars": 800}
json.dump(_skc, open(os.path.join(T, "oa_skill.json"), "w", encoding="utf-8"), ensure_ascii=False)
o, rc = run_oa([ev("oa4", "<<OA_SKILL>> 按技能说")], "oa_skill", os.path.join(T, "oa_skill.json"))
check("openai_compat：skill_read 仍可用，正文经过脱敏路径",
      "tool call skill_read" in o and "按技能回答：BODY_PROGRESS" in block(o, "oa4") and "name: progress-table" not in block(o, "oa4"), o)
_oc = json.load(open(ocfg("oa_pre"), encoding="utf-8"))
_oc["skills"] = {"enabled": True, "dir": "skills_pre", "index_budget_chars": 800, "preload": True}
json.dump(_oc, open(os.path.join(T, "oa_pre.json"), "w", encoding="utf-8"), ensure_ascii=False)
_before_pre = len(_oa.hits)
o, rc = run_oa([ev("oa8", "看一下进度表")], "oa_pre", os.path.join(T, "oa_pre.json"))
_hits = [h for h in _oa.hits[_before_pre:] if "看一下进度表" in (h.get("content") or "")]
_content = _hits[0]["content"] if _hits else ""
check("model_provider=openai_compat：命中触发词也预放正文",
      "model provider openai_compat" in o and "PROG_BODY" in _content and _sk.PRELOAD_MARK in _content
      and _content.index("技能目录") < _content.index(_sk.PRELOAD_MARK) < _content.index("提问：")
      and "杭州某某科技有限公司" not in _content and "某客户" in _content, (_content[-400:] + "\n" + o[-300:]))
o, rc = run_oa([ev("oa5", "<<OA_STALL>> 卡住")], "oa_stall", ocfg("oa_stall", stall_sec=2, max_runtime_sec=30))
b = block(o, "oa5")
check("openai_compat：stall_sec 超时返回已写出的部分并脱敏",
      "openai_compat stopped: stall" in o and "已经写出的部分：先看第一步" in b and "（时间到了，先给部分结果）" in b
      and "杭州某某科技有限公司" not in b and "某客户" in b, o)
_before = len(oa_hits("deny-auth"))
o, rc = run_oa([ev("oa6", "不该带上密钥")], "oa_401", ocfg("oa_401", model="deny-auth", network_retries=2))
check("openai_compat：401 不重试，日志和回复里没有密钥",
      "openai_compat http=401" in o and "抱歉，这次没能生成回答" in block(o, "oa6") and _OA_SECRET not in o
      and len(oa_hits("deny-auth")) == _before + 1 and "unit-secret-value" not in open(os.path.join(T, "oa_401.json"), encoding="utf-8").read(), o)
_before = len(oa_hits("flaky-net"))
o, rc = run_oa([ev("oa7", "请重试")], "oa_retry", ocfg("oa_retry", model="flaky-net", network_retries=1))
check("openai_compat：网络错误按 network_retries 重试后再答",
      "重试成功的答案" in block(o, "oa7") and "openai_compat network" in o and len(oa_hits("flaky-net")) == _before + 2 and _OA_SECRET not in o, o)
_miss = dict(os.environ); _miss.pop("UNIT_OPENAI_KEY", None); _miss.pop("AGENTBOT_FAKE_AGENT", None)
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), ocfg("oa_miss"), "--check"], capture_output=True, text=True, encoding="utf-8", env=_miss, timeout=30)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check：openai_compat 缺环境变量时报名字、不发请求",
      _cr.returncode != 0 and "FAIL openai_compat" in _co and "UNIT_OPENAI_KEY" in _co and "未设置" in _co and "OK   cursor-agent" not in _co, _co)
_ok = dict(os.environ, UNIT_OPENAI_KEY=_OA_SECRET); _ok.pop("AGENTBOT_FAKE_AGENT", None)
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), ocfg("oa_ok"), "--check"], capture_output=True, text=True, encoding="utf-8", env=_ok, timeout=30)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check：openai_compat 环境变量已设置并发短请求，输出不含密钥",
      "OK   openai_compat" in _co and "已设置" in _co and _OA_SECRET not in _co and "FAIL openai_compat" not in _co and "OK   cursor-agent" not in _co, _co)
_cr = subprocess.run([sys.executable, os.path.join(FW, "agentbot.py"), ocfg("oa_bad", model="deny-auth"), "--check"], capture_output=True, text=True, encoding="utf-8", env=_ok, timeout=30)
_co = (_cr.stdout or "") + (_cr.stderr or "")
check("--check：短请求 401 时失败且不含密钥", _cr.returncode != 0 and "FAIL openai_compat" in _co and "401" in _co and _OA_SECRET not in _co, _co)
_old = json.loads(json.dumps(cfg)); _old.pop("model_provider", None)
_oldp = os.path.join(T, "old_provider.json"); json.dump(_old, open(_oldp, "w", encoding="utf-8"), ensure_ascii=False)
o = run([ev("old1", "旧配置这一题")], name="old_provider", cfg_path=_oldp, stream=True)
check("没写 model_provider 的旧配置仍走 cursor 一次性进程", "普通回答" in block(o, "old1") and "model provider cursor" in o and "openai_compat" not in o, o)
try: _oa.shutdown()
except Exception: pass

print("\n%d/%d passed" % (sum(r for _, r in results), len(results))); shutil.rmtree(T, ignore_errors=True)
sys.exit(0 if all(r for _, r in results) else 1)
