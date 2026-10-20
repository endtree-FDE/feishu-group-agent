# 一键安装向导：问答、生成配置、密钥不落盘、重复运行、单进程、PowerShell 5.1。不连飞书。
import json, os, shutil, subprocess, sys, tempfile

FW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FW)
import utf8io  # noqa: F401
import setup_wizard
import proc_identity
from checks.check_config import check_data

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(str(detail)[-1500:])


SECRET = "supersecretvalue123"
SECRET_KEY = "app_" + "secret"
APP = "cli_exampleapp"
CHAT = "oc_groupwizard0001"
OWNER = "ou_ownerwizard"
BOT = "ou_botwizard01"
ENV_OK = [
    {"name": "Python", "ok": True, "detail": "3.12.0"},
    {"name": "git", "ok": True, "detail": ["git"]},
    {"name": "lark-cli", "ok": True, "detail": ["1"]},
    {"name": "Cursor", "ok": True, "logged_in": True, "detail": ["1"]},
]


def base_answers(**extra):
    data = {
        "name": "assistant",
        "bot_name": "小助手",
        "app_id": APP,
        "profile": "assistant",
        "chat_ids": [CHAT],
        "bot_open_id": BOT,
        "owner_open_id": OWNER,
        "owner_name": "小李",
        "persona": "general",
        "provider": "cursor",
        "create_tasks": False,
        "start": False,
    }
    data.update(extra)
    return data


def write_answers(folder, data):
    path = os.path.join(folder, "answers.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def run_w(folder, answers, extra_args=(), deps=None):
    setup_wizard._SEEN.clear()
    deps = dict(deps or {})
    deps.setdefault("env_items", ENV_OK)
    logs = []
    deps["output_fn"] = logs.append
    path = write_answers(folder, answers)
    code = setup_wizard.run_wizard(
        ["--answers", path, "--outdir", os.path.join(folder, "agents"),
         "--draft", os.path.join(folder, "draft.json"),
         "--backup-dir", os.path.join(folder, "backups"),
         "--lock-dir", os.path.join(folder, "locks"),
         "--root", folder, "--example", os.path.join(FW, "agents", "example.json")] + list(extra_args),
        deps,
    )
    text = "\n".join(logs)
    return code, text, logs


def tree_text(folder, skip_name=""):
    blobs = []
    for root, dirs, files in os.walk(folder):
        for name in files:
            if name == skip_name:
                continue
            path = os.path.join(root, name)
            try:
                blobs.append(open(path, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
    return "\n".join(blobs)


def test_dry_answers():
    folder = tempfile.mkdtemp(prefix="wiz_dry_")
    try:
        data = base_answers()
        data[SECRET_KEY] = SECRET
        calls = []

        def run(argv, stdin=None, timeout=30):
            calls.append((list(argv), stdin))
            return 0, "", ""

        code, text, _logs = run_w(folder, data, ["--dry-run"], {"run": run, "lark_argv": ["lark-cli"]})
        cfg_path = os.path.join(folder, "agents", "assistant.json")
        draft = open(os.path.join(folder, "draft.json"), encoding="utf-8").read()
        check("干跑不写配置", code == 0 and not os.path.exists(cfg_path), text[-800:])
        check("干跑不调用保存密钥", not any("init" in " ".join(argv) for argv, _stdin in calls), calls)
        check("干跑说明没有写入", "没有写入" in text and "凭证与基础信息" in text and "设置" in text, text[-800:])
        check("密钥不在屏幕和草稿里", SECRET not in text and SECRET not in draft and SECRET_KEY not in draft, draft[:400])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_write_and_repeat():
    folder = tempfile.mkdtemp(prefix="wiz_wr_")
    try:
        marker = os.path.join(folder, "agents")
        os.makedirs(marker)
        open(os.path.join(marker, "keep.txt"), "w", encoding="utf-8").write("keep-me")
        code, text, _logs = run_w(folder, base_answers(), deps={"doctor_fn": lambda cfg, path: "OK   model cursor\n"})
        dest = os.path.join(folder, "agents", "assistant.json")
        body = open(dest, encoding="utf-8").read() if os.path.isfile(dest) else ""
        cfg = json.loads(body) if body else {}
        errors, hints = check_data(cfg, "assistant.json") if cfg else ([("missing", "no")], [])
        check("写入后配置检查通过", code == 0 and errors == [] and hints == [] and "已写入" in text, text[-600:] + str(errors))
        check("新功能默认关闭",
              cfg.get("alerts", {}).get("enabled") is False and cfg.get("report", {}).get("enabled") is False
              and cfg.get("digest", {}).get("enabled") is False and cfg.get("knowledge", {}).get("enabled") is False
              and cfg.get("memory", {}).get("enabled") is False
              and cfg.get("memory", {}).get("growth", {}).get("enabled") is False
              and cfg.get("skills", {}).get("enabled") is False and cfg.get("progress", {}).get("enabled") is False)
        check("私聊不放开，写入只有主人",
              cfg.get("p2p", {}).get("allow_group_members") is False and cfg.get("p2p", {}).get("open_to_all") is False
              and cfg.get("p2p", {}).get("allow_open_ids") == []
              and list((cfg.get("write") or {}).get("allowed_users") or {}) == [OWNER])
        check("主人记在关闭的开关里，模型是 Cursor",
              cfg.get("alerts", {}).get("owner_open_ids") == [OWNER]
              and cfg.get("model", {}).get("provider") == "cursor"
              and "通用" in cfg.get("system_prompt", "") or "团队" in cfg.get("system_prompt", ""))
        check("配置里没有密钥样式", SECRET not in body and "sk-" not in body and "REPLACE_" not in body)
        check("旁边的文件还在", open(os.path.join(marker, "keep.txt"), encoding="utf-8").read() == "keep-me")
        code2, text2, _logs2 = run_w(folder, base_answers(bot_name="小帮手"), deps={"doctor_fn": lambda cfg, path: "OK   model cursor\n"})
        baks = os.listdir(os.path.join(folder, "backups"))
        bak_body = open(os.path.join(folder, "backups", baks[0]), encoding="utf-8").read() if baks else ""
        new_body = open(dest, encoding="utf-8").read()
        check("再次运行先备份再改，不删除备份",
              code2 == 0 and len(baks) == 1 and "小助手" in bak_body and "小帮手" in new_body and os.path.isfile(dest),
              text2[-400:])
        code3, _text3, _logs3 = run_w(folder, base_answers(bot_name="小组手"), deps={"doctor_fn": lambda cfg, path: "OK   model cursor\n"})
        check("第二次备份还留着", code3 == 0 and len(os.listdir(os.path.join(folder, "backups"))) == 2)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_secret_handed_not_stored():
    folder = tempfile.mkdtemp(prefix="wiz_sec_")
    try:
        seen = []

        def run(argv, stdin=None, timeout=30):
            seen.append((list(argv), stdin))
            return 0, "saved", ""

        data = base_answers()
        data[SECRET_KEY] = SECRET
        code, text, _logs = run_w(folder, data, deps={
            "run": run, "lark_argv": ["lark-cli"], "doctor_fn": lambda cfg, path: "OK   model cursor\n",
        })
        init = [item for item in seen if "init" in item[0]]
        dest = os.path.join(folder, "agents", "assistant.json")
        body = open(dest, encoding="utf-8").read()
        draft = open(os.path.join(folder, "draft.json"), encoding="utf-8").read()
        joined = " ".join(init[0][0]) if init else ""
        check("密钥只从标准输入交给 init",
              code == 0 and len(init) == 1 and "--app-secret-stdin" in init[0][0]
              and "--name" in init[0][0] and "assistant" in init[0][0]
              and SECRET not in joined and init[0][1] and init[0][1].startswith(SECRET),
              str(init)[:500])
        check("密钥不在配置、草稿和输出里", SECRET not in body and SECRET not in draft and SECRET not in text and SECRET_KEY not in body)
        others = tree_text(folder, "answers.json")
        check("除了答案文件，目录里没有密钥", SECRET not in others, others[:300])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_bad_input_and_templates():
    folder = tempfile.mkdtemp(prefix="wiz_bad_")
    try:
        code, text, _logs = run_w(folder, base_answers(app_id="not-an-app"))
        check("非法 App ID 不写文件", code == 2 and not os.path.exists(os.path.join(folder, "agents", "assistant.json")) and "凭证与基础信息" in text, text[-500:])
        planted = "sk-" + "plantedsecretvalue"
        code2, text2, _logs2 = run_w(folder, base_answers(provider="openai_compatible", api_key_env=planted))
        check("密钥样式的变量名被拒绝且不回显", code2 == 2 and planted not in text2 and not os.path.exists(os.path.join(folder, "agents", "assistant.json")))
        example = json.load(open(os.path.join(FW, "agents", "example.json"), encoding="utf-8"))
        cfg, err = setup_wizard.build_config(example, base_answers(persona="hackathon", provider="openai_compatible",
                                                                    base_url="https://api.openai.com/v1", model_name="gpt-4o-mini",
                                                                    api_key_env="UNIT_WIZARD_KEY"))
        check("黑客松人设和变量名写进配置",
              err == "" and "黑客松" in cfg["system_prompt"] and cfg["model"]["api_key_env"] == "UNIT_WIZARD_KEY"
              and cfg["model"]["provider"] == "openai_compatible" and "sk-" not in json.dumps(cfg))
        code3, text3, _logs3 = run_w(folder, base_answers(
            provider="openai_compatible", base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini", api_key_env="UNIT_WIZARD_KEY"),
            deps={"doctor_fn": lambda cfg, path: "OK   model x\n"})
        body = open(os.path.join(folder, "agents", "assistant.json"), encoding="utf-8").read()
        check("缺环境变量时说明怎么设，配置仍保留",
              code3 == 1 and "UNIT_WIZARD_KEY" in text3 and "未设置" in text3 and "UNIT_WIZARD_KEY" in body
              and SECRET not in body, text3[-700:])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_resume_and_tamper():
    folder = tempfile.mkdtemp(prefix="wiz_re_")
    try:
        draft_path = os.path.join(folder, "draft.json")
        with open(draft_path, "w", encoding="utf-8") as handle:
            json.dump({"name": "assistant", "persona": "studio", SECRET_KEY: SECRET}, handle)
        data = base_answers()
        data.pop("name")
        data.pop("persona")
        code, text, _logs = run_w(folder, data, ["--dry-run"])
        draft = open(draft_path, encoding="utf-8").read()
        check("篡改草稿里的密钥会被清掉", code == 0 and SECRET not in draft and SECRET_KEY not in draft, draft)
        check("接着用上次的人设", "工作室" in text or "studio" in draft, text[-400:] + draft[:300])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _lock(folder, bot_id, created, cmdline):
    lock_dir = os.path.join(folder, "locks")
    os.makedirs(lock_dir, exist_ok=True)
    path = proc_identity.app_lock_file(bot_id, lock_dir)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(proc_identity.format_lock(4242, created, folder))

    def snapshot(pid):
        if int(pid) != 4242:
            return None
        return {"pid": 4242, "created": created, "cmdline": cmdline, "cwd": ""}

    return snapshot


def test_single_process():
    folder = tempfile.mkdtemp(prefix="wiz_pid_")
    try:
        snap = _lock(folder, BOT, "t1", "python agentbot.py agents/assistant.json")
        started = []
        code, text, _logs = run_w(folder, base_answers(start=True), deps={
            "snapshot": snap,
            "doctor_fn": lambda cfg, path: "OK   model cursor\n",
            "start_fn": lambda path: started.append(path) or (0, "started"),
        })
        check("已有接收进程就拒绝再启动", code == 3 and started == [] and "同一个" in text and os.path.isfile(os.path.join(folder, "agents", "assistant.json")), text[-500:])
        snap2 = _lock(folder, BOT, "t2", "notepad.exe")
        # 创建时间对不上，命令行也不是机器人，应该允许启动。
        code2, text2, _logs2 = run_w(folder, base_answers(start=True), deps={
            "snapshot": snap2,
            "doctor_fn": lambda cfg, path: "OK   model cursor\n",
            "start_fn": lambda path: started.append(path) or (0, "started"),
        })
        check("过期锁不会挡住新的启动", code2 == 0 and len(started) == 1 and "自我介绍" in text2, text2[-500:])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_explain_and_interactive():
    samples = [
        "FAIL config 含有密钥字段 app_secret，请从配置里删掉",
        "FAIL profile 未填写",
        "FAIL lark-cli 无法运行 --version",
        "FAIL lark config config show 失败",
        "WARN auth 参数不被这一版接受，没有因此判失败",
        "FAIL auth 应用身份未就绪或 status 失败",
        "WARN scopes 列不出应用权限，请在开放平台核对",
        "WARN scopes 拿到的是用户 token，不是应用权限，请在开放平台核对",
        "WARN scopes 没有解析到应用权限列表，请在开放平台核对",
        "FAIL scopes 缺少 im:message:send_as_bot",
        "FAIL network 无法解析 open.feishu.cn",
        "WARN network open.feishu.cn 解析到 198.18/15，NO_PROXY 未包含飞书域名",
        "FAIL tcp443 连不上 443",
        "WARN package pypdf 未安装，对应附件会降级",
        "WARN package openpyxl 未安装，对应附件会降级",
        "WARN processes 列不出进程",
        "FAIL processes duplicate agentbot count=2",
        "FAIL processes duplicate event consume im.message.receive_v1 count=2",
        "FAIL model 环境变量 UNIT_WIZARD_KEY 未设置。请在运行这台程序的同一个窗口里设置环境变量",
        "错误  model.api_key_env：要写环境变量的名字，不要把密钥写在这里。",
        "提示  还有 1 处 REPLACE_ 占位。",
    ]
    explained = setup_wizard.explain_report("\n".join(samples))
    blob = "\n".join(line for _level, line in explained)
    check("每条失败和提醒都有白话", len(explained) == len(samples) and all(len(line) > 12 for _level, line in explained), blob[-800:])
    check("说明里告诉人去哪改", "凭证与基础信息" in blob and "设置" in blob or "权限管理" in blob)
    check("说明不回显密钥样式", "sk-" not in blob)
    folder = tempfile.mkdtemp(prefix="wiz_ask_")
    try:
        answers_queue = iter([
            "assistant", "小助手", "bad", APP, "", SECRET, "", "", "", "", "", "", "", "",
        ])

        def ask(_prompt=""):
            return next(answers_queue)

        code, _text, logs = None, "", []
        setup_wizard._SEEN.clear()
        deps = {
            "env_items": ENV_OK,
            "input_fn": ask,
            "output_fn": logs.append,
            "chats": [{"chat_id": CHAT, "name": "示例群"}],
            "people": {"users": [{"open_id": OWNER, "name": "小李"}], "bots": [{"open_id": BOT, "name": "小助手"}]},
            "lark_argv": ["lark-cli"],
            "run": lambda argv, stdin=None, timeout=30: (_ for _ in ()).throw(AssertionError("dry-run should not run")),
        }
        try:
            code = setup_wizard.run_wizard(
                ["--dry-run", "--outdir", os.path.join(folder, "agents"),
                 "--draft", os.path.join(folder, "draft.json"),
                 "--backup-dir", os.path.join(folder, "backups"),
                 "--root", folder, "--example", os.path.join(FW, "agents", "example.json")],
                deps,
            )
        except AssertionError:
            code = 99
        text = "\n".join(logs)
        draft = open(os.path.join(folder, "draft.json"), encoding="utf-8").read() if os.path.isfile(os.path.join(folder, "draft.json")) else ""
        check("一问一答会解释并在答错后重试",
              code == 0 and "为什么：" in text and "去哪找：" in text and "直接回车就用：" in text
              and text.count("App ID") >= 1 and "没有写入" in text, text[-700:])
        check("交互干跑也不把密钥落下", SECRET not in text and SECRET not in draft and not os.path.exists(os.path.join(folder, "agents", "assistant.json")))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_groups_and_people():
    listed = [{"chat_id": CHAT, "name": "示例群"}, {"chat_id": "oc_groupwizard0002", "name": "第二个"}]
    ids, err = setup_wizard.resolve_chats("2", listed)
    check("可以按序号选群", ids == ["oc_groupwizard0002"] and err == "")
    ids, err = setup_wizard.resolve_chats("", listed)
    check("群的默认是第一个", ids == [CHAT] and err == "")
    ids, err = setup_wizard.resolve_chats("oc_handfillwizard01", [])
    check("查不到群时可以手填", ids == ["oc_handfillwizard01"] and err == "")
    ids, err = setup_wizard.resolve_chats("不是编号", [])
    check("手填的群编号要合法", ids == [] and err)
    owner, err = setup_wizard.resolve_owner("2", [{"open_id": OWNER, "name": "小李"}, {"open_id": "ou_otherwizard", "name": "小林"}])
    check("可以按序号选主人", owner and owner["open_id"] == "ou_otherwizard" and err == "")
    bot, err = setup_wizard.resolve_bot("", [{"open_id": BOT, "name": "小助手"}])
    check("只有一个机器人时默认就是它", bot == BOT and err == "")
    payload = {"ok": True, "data": {"chats": [{"chat_id": CHAT, "name": "示例群"}]}}
    check("能从飞书命令的 JSON 里认出群", setup_wizard.extract_chats(payload)[0]["name"] == "示例群")
    people = {"data": {"users": [{"member_id": OWNER, "name": "小李"}], "bots": [{"member_id": BOT, "name": "小助手"}]}}
    users, bots = setup_wizard.extract_people(people)
    check("能分开人和机器人", users[0]["open_id"] == OWNER and bots[0]["open_id"] == BOT)


def test_powershell51():
    path = os.path.join(FW, "scripts", "setup.ps1")
    raw = open(path, "rb").read()
    text = raw.decode("utf-8-sig")
    bom = raw[:3] == b"\xef\xbb\xbf"
    banned = []
    for token in ("&&", "||", "??", "?.", "foreach -parallel"):
        if token in text:
            banned.append(token)
    if "-AsHashtable" in text:
        banned.append("-AsHashtable")
    check("setup.ps1 带 UTF-8 BOM 且是 5.1 能解析的写法",
          bom and not banned and "setup_wizard.py" in text and "Find-Python" in text and "ExecutionPolicy" not in text.split("param", 1)[0],
          str(banned))
    # 没有 Windows PowerShell 时，至少确认文件能被当成 UTF-8 读完，并且 param 在注释之后。
    head = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")][0]
    check("param 是第一条真正的语句", head.startswith("param("), head)


def test_conflict_helper():
    folder = tempfile.mkdtemp(prefix="wiz_help_")
    try:
        snap = _lock(folder, BOT, "t9", "python agentbot.py")
        notes = setup_wizard.receiver_conflict(BOT, folder, snap, os.path.join(folder, "locks"))
        check("单进程检查能认出还活着的接收进程", bool(notes) and "4242" in notes[0])
        notes2 = setup_wizard.receiver_conflict(BOT, folder, lambda pid: {"pid": 1, "cmdline": "notepad.exe", "created": "other", "cwd": ""}, os.path.join(folder, "locks"))
        check("编号被别的程序占用时不当成机器人", notes2 == [])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    test_dry_answers()
    test_write_and_repeat()
    test_secret_handed_not_stored()
    test_bad_input_and_templates()
    test_resume_and_tamper()
    test_single_process()
    test_explain_and_interactive()
    test_groups_and_people()
    test_powershell51()
    test_conflict_helper()
    failed = [name for name, ok in results if not ok]
    print("%d/%d passed" % (len(results) - len(failed), len(results)))
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
