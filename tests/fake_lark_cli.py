# 测试用假 lark-cli。im messages delete 是 high-risk-write：缺 --yes 时退出码 10。其他命令不要求 --yes。
import json, os, sys
argv = sys.argv[1:]
logp = os.environ.get("AGENTBOT_FAKE_LARK_LOG")
if logp:
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv, ensure_ascii=False) + "\n")
if argv[:3] == ["im", "messages", "delete"] and "--yes" not in argv:
    sys.stderr.write("high-risk-write: im messages delete requires --yes\n")
    sys.exit(10)
sys.stdout.write(json.dumps({"ok": True, "data": {}}) + "\n")
