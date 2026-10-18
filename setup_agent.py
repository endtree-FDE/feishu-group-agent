#!/usr/bin/env python3
# 生成 agents/<名字>.json，并打印一条 Windows 计划任务命令。默认只干跑，不写文件，也不注册任务。
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from providers.presets import PRESETS

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")
PROVIDERS = ("cursor", "openai_compat", "deepseek", "dashscope", "zhipu", "pangu")


def _secretish(text):
    raw = str(text or "")
    low = raw.lower()
    return "sk-" in low or "api_key=" in low or " " in raw


def task_lines(name):
    rel = "agents\\%s.json" % name
    return [
        "计划任务不会在这里注册。在 Windows 上可以执行：",
        "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\register-tasks.ps1 -Config %s -Name FeishuBot-%s" % (rel, name),
        "它会登记登录时运行 scripts\\start.ps1 -Config %s。这里不改 scripts 目录里的文件。" % rel,
    ]


def build_config(example, name, provider, bot_name, base_url, model, api_key_env):
    if not NAME_RE.match(name or ""):
        return None, "名字只能用字母、数字、下划线和连字符，最长 40 个字符。"
    if name == "example":
        return None, "不要覆盖 example.json。请换一个名字。"
    if provider not in PROVIDERS:
        return None, "不认识的模型。可用 %s。" % "、".join(PROVIDERS)
    cfg = json.loads(json.dumps(example))
    cfg["name"] = name
    if bot_name:
        cfg["bot_names"] = [bot_name]
    cfg["model_provider"] = provider
    agent = cfg.setdefault("agent", {})
    if not isinstance(agent, dict):
        return None, "示例里的 agent 不是对象。"
    agent.pop("preset", None)
    if provider in PRESETS:
        spec = PRESETS[provider]
        agent["base_url"] = spec["base_url"]
        agent["model"] = spec["model"]
        agent["api_key_env"] = spec["api_key_env"]
    elif provider == "cursor":
        agent.pop("base_url", None)
        agent.pop("api_key_env", None)
    else:
        if not (base_url and model and api_key_env):
            return None, "openai_compat 需要 --base-url、--model 和 --api-key-env。--api-key-env 只写变量名。"
        if not ENV_RE.match(api_key_env) or _secretish(api_key_env):
            return None, "--api-key-env 要写环境变量的名字，不要写密钥。"
        if _secretish(base_url) or not (base_url.startswith("https://") or base_url.startswith("http://")):
            return None, "--base-url 要是 http:// 或 https:// 开头的地址，不要夹密钥。"
        agent["base_url"] = base_url
        agent["model"] = model
        agent["api_key_env"] = api_key_env
    return cfg, ""


def _load_example(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("示例配置最外层要是对象")
    return data


def _prompt(label, default=""):
    suffix = " [%s]" % default if default else ""
    line = input(label + suffix + "：").strip()
    return line or default


def _from_prompts():
    name = _prompt("配置名字（字母、数字、下划线、连字符）")
    provider = _prompt("模型（%s）" % "、".join(PROVIDERS), "cursor")
    bot_name = _prompt("群里显示的名字", "小助手")
    base_url = model = api_key_env = ""
    if provider == "openai_compat":
        base_url = _prompt("base_url")
        model = _prompt("model")
        api_key_env = _prompt("环境变量名（不要输入密钥）")
    write = _prompt("写入文件？输入 y 才写，直接回车只干跑", "n").lower() == "y"
    return name, provider, bot_name, base_url, model, api_key_env, write


def _emit(cfg, name, wrote_path):
    provider = cfg.get("model_provider")
    env_name = ""
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    if provider in PRESETS or provider == "openai_compat":
        env_name = agent.get("api_key_env") or ""
    if wrote_path:
        print("已写入 %s" % wrote_path)
    else:
        print("干跑，没有写入文件。")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("")
    for line in task_lines(name):
        print(line)
    if env_name:
        print("密钥不要写进 JSON。启动前只在环境变量 %s 里设置。" % env_name)
    elif provider == "cursor":
        print("这一份走本机 Cursor。先在自己的电脑上 cursor-agent login，不要把密钥写进 JSON。")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a == "--api-key" or a.startswith("--api-key=") for a in argv):
        print("不要把密钥交给这个脚本。只写环境变量的名字，用 --api-key-env。")
        return 2
    parser = argparse.ArgumentParser(description="生成一份机器人配置，并打印计划任务命令。默认不写文件。")
    parser.add_argument("--name", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--bot-name", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--outdir", default="")
    parser.add_argument("--example", default="")
    args = parser.parse_args(argv)
    if args.dry_run and args.write:
        print("--dry-run 和 --write 不能一起用。没有写入文件。")
        return 2
    interactive = sys.stdin.isatty() and not args.name and not args.write and not args.dry_run
    write = bool(args.write)
    if interactive:
        try:
            args.name, args.provider, args.bot_name, args.base_url, args.model, args.api_key_env, write = _from_prompts()
        except EOFError:
            print("没有读到输入。非交互时请加上 --name，需要写文件再加 --write。")
            return 2
    if not args.name:
        print("用法: python setup_agent.py --name demo --provider deepseek --dry-run")
        print("不加 --write 就只打印，不写文件，也不注册计划任务。")
        return 2
    if not sys.stdin.isatty() and not args.write:
        write = False
    provider = args.provider or "cursor"
    example_path = args.example or os.path.join(ROOT, "agents", "example.json")
    try:
        example = _load_example(example_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print("读不到示例配置。")
        return 2
    cfg, err = build_config(example, args.name, provider, args.bot_name, args.base_url, args.model, args.api_key_env)
    if err:
        print(err)
        return 2
    outdir = args.outdir or os.path.join(ROOT, "agents")
    root_out = os.path.realpath(outdir)
    dest = os.path.realpath(os.path.join(root_out, args.name + ".json"))
    if os.path.dirname(dest) != root_out:
        print("输出路径不在指定目录里。没有写入。")
        return 2
    if not write:
        _emit(cfg, args.name, "")
        return 0
    if not os.path.isdir(root_out):
        print("目录不存在，没有创建：%s" % outdir)
        return 1
    if os.path.exists(dest):
        print("已经有这个文件，没有覆盖：%s" % dest)
        return 1
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    _emit(cfg, args.name, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
