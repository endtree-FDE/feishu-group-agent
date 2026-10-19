# 私聊按群成员放行。成员接口打桩，不连飞书。
import io, json, os, re, shutil, subprocess, sys, tempfile, threading, time
from contextlib import redirect_stdout

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401

T = tempfile.mkdtemp(prefix="agentbot_p2p_")
results = []
TOKEN = "p2p-should-not-log-this-token"
TOOL = '<<FAKE_TOOL>>{"tool":"docs_search","args":{"query":"进度"}}<<END>> 查文档'


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(detail[-1500:])


def main():
    os.makedirs(os.path.join(T, "kb"), exist_ok=True)
    src = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8-sig"))
    src["base_dir"] = T
    src["profile"] = "placeholder"
    src["bot_open_id"] = "ou_bot_placeholder"
    src["chat_ids"] = ["oc_g1", "oc_g2"]
    src["chats"] = {"auto_join": False, "admit": "confirm", "inviter_open_ids": []}
    src["rate_limit"] = {"min_gap_per_user": 0, "max_per_hour": 10000}
    src["features"]["p2p"] = True
    src["memory"]["enabled"] = False
    src["progress"] = {"enabled": False}
    src["p2p"] = {
        "allow_open_ids": ["user_listed"],
        "open_to_all": False,
        "allow_group_members": True,
        "member_cache_ttl_sec": 600,
    }
    src["write"]["allowed_users"] = {"user_admin": "管理员"}
    cfg_path = os.path.join(T, "bot.json")
    json.dump(src, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)
    empty = os.path.join(T, "empty.jsonl")
    open(empty, "w", encoding="utf-8").write("")
    fake_path = os.path.join(T, "fake.json")
    json.dump({"drive +search": {"ok": False, "error": {"type": "authorization", "subtype": "app_scope_not_applied",
                                                       "code": 99991672, "message": "access denied"}}},
              open(fake_path, "w", encoding="utf-8"), ensure_ascii=False)
    os.environ["AGENTBOT_FAKE_AGENT"] = "0"
    os.environ["AGENTBOT_FAKE_LARK"] = fake_path
    sys.argv = ["agentbot.py", cfg_path, "--dry-run", empty]
    import agentbot

    clock = {"t": 1000000.0}
    agentbot.time.time = lambda: clock["t"]
    calls = []
    call_lock = threading.Lock()
    MODE = {"fail": None, "timeout": False, "kind": ""}
    MEM = {"oc_g1": ["user_m1"], "oc_g2": ["user_m2"]}
    HOLD = {"on": False}
    RELEASE = threading.Event()
    ENTERED = {"n": 0}
    seq = {"n": 0}

    def fake_run(cmd, cwd=None, timeout=60):
        argv = list(cmd)
        if "+chat-members-list" not in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": {}}), "")
        with call_lock:
            calls.append(argv)
        if HOLD["on"]:
            with call_lock:
                ENTERED["n"] += 1
            RELEASE.wait(5)
        if MODE["timeout"]:
            raise subprocess.TimeoutExpired(cmd, timeout)
        cid = argv[argv.index("--chat-id") + 1] if "--chat-id" in argv else ""
        if MODE["fail"]:
            return subprocess.CompletedProcess(argv, 1, "", MODE["fail"])
        users = [{"member_id": u} for u in MEM.get(cid, [])]
        data = {"chat_id": cid, "users": users, "bots": [], "has_more": False, "truncations": []}
        if MODE["kind"] == "trunc":
            data["truncations"] = [{"limit": 100, "member_type": "user"}]
        elif MODE["kind"] == "more":
            data["has_more"] = True
            data["page_token"] = "next-page"
        elif MODE["kind"] == "total":
            data["user_total"] = len(users) + 4
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": data}), "")

    agentbot.run_capture = fake_run

    def reset_cache():
        agentbot._MEMBER_CACHE.clear()
        agentbot._MEMBER_ATTEMPT.clear()
        agentbot._MEMBER_INFLIGHT.clear()

    def base_state():
        MODE["fail"] = None
        MODE["timeout"] = False
        MODE["kind"] = ""
        HOLD["on"] = False
        RELEASE.set()
        agentbot.CFG["chat_ids"] = ["oc_g1", "oc_g2"]
        agentbot.CFG["chats"] = {"auto_join": False, "admit": "confirm", "inviter_open_ids": []}
        agentbot.CFG["features"]["p2p"] = True
        agentbot.CFG["p2p"] = {
            "allow_open_ids": ["user_listed"],
            "open_to_all": False,
            "allow_group_members": True,
            "member_cache_ttl_sec": 600,
        }
        MEM.clear()
        MEM.update({"oc_g1": ["user_m1"], "oc_g2": ["user_m2"]})
        reset_cache()

    def logs():
        for h in agentbot.log.handlers:
            try: h.flush()
            except Exception: pass
        path = os.path.join(agentbot.LOGD, "bot.log")
        return open(path, encoding="utf-8").read() if os.path.isfile(path) else ""

    def replied(mid):
        return ("[dry-run] reply %s" % mid) in logs()

    def started(mid):
        return ("agent start %s" % mid) in logs()

    def section(mid):
        blob = logs()
        key = " INFO Q %s " % mid
        i = blob.find(key)
        if i < 0: return ""
        j = blob.find(" INFO Q ", i + len(key))
        return blob[i:] if j < 0 else blob[i:j]

    def cids_since(n):
        out = []
        for argv in calls[n:]:
            if "+chat-members-list" in argv and "--chat-id" in argv:
                out.append(argv[argv.index("--chat-id") + 1])
        return out

    def send(uid, text="你好"):
        # 成员缓存用的是被冻结的时钟。同一时刻再发，或把时钟拨回去，会撞上用户间隔，回复变成「请稍等」。
        agentbot.last_user.clear()
        agentbot.hour_hits.clear()
        seq["n"] += 1
        mid = "pm%d" % seq["n"]
        agentbot.handle({
            "message_id": mid, "chat_id": "p2p_" + mid, "chat_type": "p2p",
            "message_type": "text", "sender_id": uid, "sender_type": "user", "content": text,
        })
        return mid

    ids, complete, count = agentbot._parse_members({
        "ok": True, "data": {"items": [{"member_id": "user_item", "member_id_type": "open_id"}],
                             "has_more": False, "member_total": 1}})
    check("原始成员列表可以识别", complete and ids == {"user_item"} and count == 1)
    ids, complete, count = agentbot._parse_members({
        "ok": True, "data": {"users": [{"member_id": "user_m1"}], "bots": [], "has_more": True, "page_token": "next"}})
    check("没翻完的名单不算完整", not complete and count == 1)
    ids, complete, count = agentbot._parse_members({
        "ok": True, "data": {"users": [{"member_id": "a"}], "user_total": 3, "has_more": False, "truncations": []}})
    check("人数对不上算不完整", not complete and count == 1)
    agentbot.CFG["p2p"].pop("member_cache_ttl_sec", None)
    check("没写 TTL 时是 600 秒", agentbot.member_cache_ttl() == 600)
    agentbot.CFG["p2p"]["member_cache_ttl_sec"] = "nope"
    check("TTL 写错时仍是 600 秒", agentbot.member_cache_ttl() == 600)
    agentbot.CFG["p2p"]["member_cache_ttl_sec"] = -3
    check("TTL 为负数时仍是 600 秒", agentbot.member_cache_ttl() == 600)

    base_state()
    agentbot.CFG["p2p"]["allow_group_members"] = False
    n = len(calls)
    mid = send("user_m1")
    check("开关关闭时群成员不能私聊，也不查接口", not replied(mid) and not started(mid) and len(calls) == n)
    mid = send("user_listed")
    check("开关关闭时显式名单仍可私聊", replied(mid) and len(calls) == n)
    mid = send("user_admin")
    check("开关关闭时写入白名单仍可私聊，即使不在私聊名单里", replied(mid) and len(calls) == n)
    agentbot.CFG["p2p"].pop("allow_group_members", None)
    n = len(calls)
    mid = send("user_m1")
    check("没写 allow_group_members 时不查群成员", not replied(mid) and len(calls) == n)

    base_state()
    mid = send("user_m1")
    check("成员私聊得到回复", replied(mid) and started(mid))
    mid_out = send("user_out")
    check("群外的人被静默丢弃", not replied(mid_out) and not started(mid_out))
    mid2 = send("user_m2")
    check("第二个群的成员可以私聊", replied(mid2) and started(mid2))
    reset_cache()
    before = len(calls)
    send("user_m2", "再问一次")
    got = cids_since(before)
    check("一条私聊对每个群最多刷新一次", got.count("oc_g1") == 1 and got.count("oc_g2") == 1, str(got))
    mid_tool = send("user_m1", TOOL)
    sec = section(mid_tool)
    check("群成员可以使用飞书工具",
          replied(mid_tool) and "不能使用飞书工具" not in sec and ("drive +search" in sec or "这项权限未开" in sec), sec[-800:])

    base_state()
    agentbot.CFG["chat_ids"] = ["oc_g1"]
    clock["t"] = 10000
    before = len(calls)
    mid = send("user_m1")
    check("单群成员第一次会查接口", replied(mid) and cids_since(before) == ["oc_g1"], str(cids_since(before)))
    clock["t"] = 10599
    before = len(calls)
    mid = send("user_m1", "还在群里")
    check("TTL 内不再查询成员", replied(mid) and len(calls) == before)
    clock["t"] = 10600
    MEM["oc_g1"] = []
    before = len(calls)
    mid = send("user_m1", "刚退群")
    check("过了 TTL 恰好再查一次", cids_since(before) == ["oc_g1"], str(cids_since(before)))
    check("退群后刷新被拒绝", not replied(mid) and not started(mid))

    base_state()
    agentbot.CFG["chat_ids"] = ["oc_g1"]
    clock["t"] = 20000
    MODE["fail"] = "access denied 99991672 " + TOKEN
    mid = send("user_m1")
    blob = logs()
    check("接口失败且没有缓存时拒绝", not replied(mid) and not started(mid))
    check("失败日志写错误码且不包含令牌",
          "p2p member check failed oc_g1 99991672" in blob and TOKEN not in blob, blob[-800:])
    clock["t"] = 20600
    MODE["fail"] = None
    MEM["oc_g1"] = ["user_m1"]
    mid = send("user_m1")
    check("接口恢复后按新名单放行", replied(mid))
    clock["t"] = 21300
    MODE["fail"] = "99991672 " + TOKEN
    before = len(calls)
    mid = send("user_m1")
    check("缓存未超过两倍 TTL 时沿用", replied(mid) and cids_since(before) == ["oc_g1"])
    clock["t"] = 20600 + 1201
    before = len(calls)
    mid = send("user_m1")
    check("缓存超过两倍 TTL 时拒绝", not replied(mid) and not started(mid) and cids_since(before) == [])
    MODE["timeout"] = True
    MODE["fail"] = None
    clock["t"] = 40000
    reset_cache()
    mid = send("user_m1")
    check("超时且没有缓存时拒绝", not replied(mid) and "p2p member check failed oc_g1 timeout" in logs())
    MODE["timeout"] = False
    clock["t"] = 50000
    reset_cache()
    MODE["fail"] = "permission denied missing scope " + TOKEN
    mid = send("user_m1")
    check("缺少成员权限时拒绝", not replied(mid) and "p2p member check failed oc_g1 scope" in logs() and TOKEN not in logs())

    base_state()
    agentbot.CFG["chat_ids"] = ["oc_g1"]
    clock["t"] = 60000
    MODE["kind"] = "more"
    MEM["oc_g1"] = ["user_m1"]
    mid = send("user_m1")
    check("名单没翻完时拒绝", not replied(mid) and "p2p member check failed oc_g1 truncated" in logs())
    MODE["kind"] = "trunc"
    reset_cache()
    mid = send("user_m1")
    mid_again = send("user_m1")
    check("名单被截断时拒绝，并且不会当成完整名单", not replied(mid) and not replied(mid_again))
    MODE["kind"] = "total"
    reset_cache()
    mid = send("user_m1")
    check("返回人数多于列表时拒绝", not replied(mid) and "p2p member check failed oc_g1 truncated" in logs())
    MODE["kind"] = ""
    clock["t"] = 70000
    reset_cache()
    MEM["oc_g1"] = ["user_m1"]
    mid = send("user_m1")
    check("截断测试前先有一份成功名单", replied(mid))
    clock["t"] = 70700
    MODE["kind"] = "trunc"
    MEM["oc_g1"] = ["user_new"]
    mid_old = send("user_m1")
    mid_new = send("user_new")
    check("截断时沿用未超过两倍 TTL 的成功名单", replied(mid_old))
    check("截断名单里的新成员不能进来", not replied(mid_new) and not started(mid_new))

    base_state()
    MODE["fail"] = "99991672 " + TOKEN
    n = len(calls)
    mid_l = send("user_listed")
    mid_a = send("user_admin")
    check("接口失败时显式名单仍可私聊", replied(mid_l) and len(calls) == n)
    check("接口失败时写入白名单仍可私聊", replied(mid_a) and len(calls) == n)
    mid_o = send("user_out")
    check("接口失败时不在名单里的人被拒绝", not replied(mid_o) and not started(mid_o))

    base_state()
    agentbot.CFG["p2p"]["open_to_all"] = True
    MODE["fail"] = "99991672 " + TOKEN
    mid = send("user_out")
    check("open_to_all 在成员接口失败时仍然回复", replied(mid))
    MODE["fail"] = None
    reset_cache()
    mid_out = send("user_out", TOOL)
    sec = section(mid_out)
    check("open_to_all 且不是群成员时不能用工具",
          replied(mid_out) and "不能使用飞书工具" in sec and "drive +search" not in sec, sec[-800:])
    mid_m = send("user_m1", TOOL)
    sec = section(mid_m)
    check("open_to_all 下群成员仍可使用工具",
          replied(mid_m) and "不能使用飞书工具" not in sec and ("drive +search" in sec or "这项权限未开" in sec), sec[-800:])

    base_state()
    agentbot.CFG["p2p"]["open_to_all"] = True
    buf = io.StringIO()
    with redirect_stdout(buf):
        agentbot.self_check()
    text = buf.getvalue()
    check("--check 两个开关同时打开时提示 open_to_all 优先",
          "HINT p2p.allow_group_members 与 open_to_all 同时打开" in text
          and "谁能私聊仍由 open_to_all 决定" in text
          and "p2p members oc_g1 count=1 complete=yes" in text
          and "p2p members oc_g2 count=1 complete=yes" in text
          and "HINT p2p.open_to_all 已打开且私聊名单为空" not in text, text[-1500:])
    MODE["kind"] = "trunc"
    buf = io.StringIO()
    with redirect_stdout(buf):
        agentbot.report_p2p_members()
    text = buf.getvalue()
    check("--check 截断时标明名单不完整",
          "DEGR p2p members oc_g1 count=1 complete=no truncated" in text
          and "DEGR p2p members oc_g2 count=1 complete=no truncated" in text, text)
    MODE["kind"] = ""
    agentbot.CFG["chat_ids"] = []
    agentbot.CFG["chats"] = {"auto_join": False, "admit": "confirm", "inviter_open_ids": []}
    agentbot.CFG["p2p"]["open_to_all"] = False
    buf = io.StringIO()
    with redirect_stdout(buf):
        agentbot.report_p2p_members()
    text = buf.getvalue()
    check("没有所服务的群时 --check 说明 groups=0", "OK   p2p members groups=0" in text and "HINT p2p.allow_group_members" not in text, text)

    base_state()
    MEM["oc_g1"] = ["user_c1", "user_c2"]
    reset_cache()
    HOLD["on"] = True
    RELEASE.clear()
    ENTERED["n"] = 0
    barrier = threading.Barrier(2)
    err = []

    def go(uid, mid):
        try:
            barrier.wait(5)
            agentbot.handle({
                "message_id": mid, "chat_id": "p2p_" + mid, "chat_type": "p2p",
                "message_type": "text", "sender_id": uid, "sender_type": "user", "content": "同时问",
            })
        except Exception as e:
            err.append(repr(e))

    try:
        t1 = threading.Thread(target=go, args=("user_c1", "pmc1"))
        t2 = threading.Thread(target=go, args=("user_c2", "pmc2"))
        t1.start(); t2.start()
        deadline = time.time() + 5
        while time.time() < deadline and ENTERED["n"] < 1:
            time.sleep(0.01)
        time.sleep(0.4)
        single = ENTERED["n"] == 1
        RELEASE.set()
        t1.join(5); t2.join(5)
    finally:
        HOLD["on"] = False
        RELEASE.set()
    check("并发私聊对同一个群只查一次",
          single and not t1.is_alive() and not t2.is_alive() and not err
          and replied("pmc1") and replied("pmc2"), "entered=%s err=%s" % (ENTERED["n"], err))

    base_state()
    agentbot.CFG["chat_ids"] = []
    agentbot.CFG["chats"] = {"auto_join": True, "admit": "confirm", "inviter_open_ids": []}
    agentbot.save_chat("oc_admitted", "admitted", "command")
    agentbot.save_chat("oc_pending", "pending", "added")
    MEM["oc_admitted"] = ["user_ad"]
    MEM["oc_pending"] = ["user_pe"]
    before = len(calls)
    mid = send("user_ad")
    mid_p = send("user_pe")
    got = cids_since(before)
    check("已认群的成员可以私聊", replied(mid) and started(mid))
    check("待认群的成员不能私聊", not replied(mid_p) and not started(mid_p))
    check("不会去查待认群", "oc_admitted" in got and "oc_pending" not in got, str(got))

    base_state()
    before = len(calls)
    th = agentbot.preload_p2p_members()
    check("开关打开时会在后台预加载", th is not None)
    if th: th.join(5)
    got = cids_since(before)
    check("预加载每个所服务的群各查一次", sorted(got) == ["oc_g1", "oc_g2"], str(got))
    before = len(calls)
    mid = send("user_m1")
    check("预加载之后 TTL 内不再查询", replied(mid) and len(calls) == before)

    def flags_ok(argv):
        try:
            def val(flag):
                return argv[argv.index(flag) + 1]
            return ("+chat-members-list" in argv and val("--member-id-type") == "open_id"
                    and "--page-all" in argv and val("--page-limit") == "0"
                    and val("--as") == "bot" and val("--format") == "json"
                    and "--chat-id" in argv and "--profile" in argv)
        except (ValueError, IndexError):
            return False

    member_argv = [a for a in calls if "+chat-members-list" in a]
    check("成员命令翻完所有页，并用 bot 身份和 open_id", bool(member_argv) and all(flags_ok(a) for a in member_argv),
          str(member_argv[0] if member_argv else []))
    check("整段日志都不包含令牌", TOKEN not in logs())

    base_state()
    write = '<<FAKE_TOOL>>{"tool":"docs_create","args":{"title":"周报","markdown":"内容"}}<<END>> 建文档'
    mid = send("user_m1", write)
    sec = section(mid)
    check("群成员不在写入白名单时不能建文档",
          replied(mid) and "不在写入白名单" in sec and "确认 " not in sec, sec[-500:])

    def talk(mid, uid, text, chat, **extra):
        agentbot.last_user.clear()
        agentbot.hour_hits.clear()
        ev = {
            "message_id": mid, "chat_id": chat, "chat_type": "p2p",
            "message_type": "text", "sender_id": uid, "sender_type": "user", "content": text,
        }
        ev.update(extra)
        agentbot.handle(ev)

    agentbot.CFG["write"]["allowed_users"]["user_writer"] = "可写成员"
    MEM["oc_g1"] = ["user_m1", "user_writer"]
    reset_cache()
    talk("pw1", "user_writer", write, "p2p_bind")
    codes = re.findall(r"确认 (\d{6})", logs())
    code = codes[-1] if codes else ""
    check("可写的群成员在私聊里得到确认码", bool(code), section("pw1")[-500:])
    talk("pw2", "user_writer", "确认 " + code, "p2p_other")
    check("别的私聊不能用这个确认码", "只有发起人本人在原会话里才能确认" in section("pw2"), section("pw2")[-400:])
    talk("pw3", "user_writer", "确认 " + code, "p2p_bind")
    sec = section("pw3")
    check("原私聊会话可以确认并执行", "已完成" in sec and "[fake-lark] docs +create" in sec, sec[-500:])
    talk("pw4", "user_writer", write, "p2p_topic", root_id="topic_root", thread_id="topic_root")
    codes = re.findall(r"确认 (\d{6})", logs())
    code2 = codes[-1] if len(codes) >= 2 else ""
    talk("pw5", "user_writer", "确认 " + code2, "p2p_topic")
    check("话题里的确认码不能在这条私聊的主会话里用", "只有发起人本人在原会话里才能确认" in section("pw5"), section("pw5")[-400:])
    talk("pw6", "user_writer", "确认 " + code2, "p2p_topic", root_id="topic_root", thread_id="topic_root")
    check("同一话题里可以确认", "已完成" in section("pw6"), section("pw6")[-400:])

    before = logs()
    talk("ps0", "user_out", "/状态", "p2p_out")
    check("群外的人发命令也不回复", "ps0" not in logs()[len(before):])
    agentbot.CFG.setdefault("rate_limit", {})["max_commands_per_min"] = 2
    agentbot.cmd_hits.clear()
    talk("ps1", "user_m1", "/状态", "p2p_cmd")
    talk("ps2", "user_m1", "/状态", "p2p_cmd")
    talk("ps3", "user_m1", "/状态", "p2p_cmd")
    check("群成员的命令受每分钟上限",
          replied("ps1") and replied("ps2") and "请稍等一下" in section("ps3")
          and "agent start ps1" not in logs() and "agent start ps3" not in logs(), section("ps3")[-300:])
    talk("ps4", "user_m1", "你好", "p2p_cmd2")
    check("命令限速之后普通私聊仍走提问间隔", started("ps4") and replied("ps4"))
    print("\n%d/%d passed" % (sum(1 for _, ok in results if ok), len(results)))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(T, ignore_errors=True)
    sys.exit(code)
