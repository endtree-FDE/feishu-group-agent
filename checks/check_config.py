#!/usr/bin/env python3
# 检查 agents/*.json 的类型，并拦住写进文件的密钥。不连网，不改文件。
# 缺了的键不算错。不认识的键先留着。编辑器可读 agents/config.schema.json。
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "agents", "config.schema.json")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")

_TYPE_WORD = {
    "string": "文字",
    "integer": "整数",
    "number": "数字",
    "boolean": "true 或 false",
    "object": "对象",
    "array": "列表",
}


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _is_type(value, typ):
    if typ == "string":
        return isinstance(value, str)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "object":
        return isinstance(value, dict)
    if typ == "array":
        return isinstance(value, list)
    return True


def _seen(value):
    if isinstance(value, str):
        return "文字「%s」" % value if len(value) <= 40 else "一段文字"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return "数字 %s" % value
    if isinstance(value, dict):
        return "对象"
    if isinstance(value, list):
        return "列表"
    return "别的类型"


def _join(path, key):
    return key if not path else "%s.%s" % (path, key)


def _secretish(text):
    raw = str(text or "")
    low = raw.lower()
    return "sk-" in low or "api_key=" in low or " " in raw


def _field_message(path, schema, value):
    if path.endswith("api_key_env"):
        return "要写环境变量的名字，例如 DEEPSEEK_API_KEY。不要把密钥写在这里。"
    if path.endswith("base_url") and isinstance(value, str) and _secretish(value):
        return "地址里不要带密钥，也不要在问号后面写 api_key。"
    if "enum" in schema:
        opts = "、".join(str(x) if x != "" else "空" for x in schema["enum"])
        shown = _seen(value) if not (isinstance(value, str) and _secretish(value)) else "一段不能展示的内容"
        return "只能是 %s，现在是%s。" % (opts, shown)
    typ = schema.get("type")
    if typ and not _is_type(value, typ):
        return "这一项要是%s，现在是%s。" % (_TYPE_WORD.get(typ, typ), _seen(value))
    if isinstance(value, str) and schema.get("pattern") and not re.search(schema["pattern"], value):
        if path.endswith("base_url"):
            return "要写成 http:// 或 https:// 开头的地址，不要夹密钥。"
        return "格式不对。"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return "不能小于 %s。" % schema["minimum"]
        if "maximum" in schema and value > schema["maximum"]:
            return "不能大于 %s。" % schema["maximum"]
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        return "太短。"
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        return "太长。"
    return ""


def validate(value, schema, path=""):
    if not isinstance(schema, dict):
        return []
    msg = _field_message(path or "（根）", schema, value)
    if msg and (schema.get("type") and not _is_type(value, schema["type"])):
        return [(path or "（根）", msg)]
    if msg and "enum" in schema and value not in schema["enum"]:
        return [(path or "（根）", msg)]
    errors = []
    if isinstance(value, str):
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            errors.append((path, msg or "格式不对。"))
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append((path, msg or "太短。"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append((path, msg or "太长。"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append((path, "不能小于 %s。" % schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append((path, "不能大于 %s。" % schema["maximum"]))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value:
                errors.append((_join(path, key), "缺少这一项。"))
        for key, item in value.items():
            sub = props.get(key)
            if isinstance(sub, dict):
                errors.extend(validate(item, sub, _join(path, key)))
            elif schema.get("additionalProperties") not in (None, True) and isinstance(schema.get("additionalProperties"), dict):
                errors.extend(validate(item, schema["additionalProperties"], _join(path, key)))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(validate(item, schema["items"], "%s[%d]" % (path, i)))
    return errors


def secret_errors(data):
    if not isinstance(data, dict):
        return []
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    errors = []
    env = agent.get("api_key_env")
    if isinstance(env, str) and env.strip() and (not ENV_NAME.match(env) or _secretish(env)):
        errors.append(("agent.api_key_env", "要写环境变量的名字，例如 DEEPSEEK_API_KEY。不要把密钥写在这里。"))
    url = agent.get("base_url")
    if isinstance(url, str) and url.strip() and _secretish(url):
        errors.append(("agent.base_url", "地址里不要带密钥，也不要在问号后面写 api_key。"))
    return errors


def placeholder_hints(data, filename):
    if os.path.basename(filename) == "example.json":
        return []
    found = []

    def walk(value, path):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, _join(path, k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, "%s[%d]" % (path, i))
        elif isinstance(value, str) and "REPLACE_" in value:
            found.append(path or "（根）")

    walk(data, "")
    if not found:
        return []
    return ["还有 %d 处 REPLACE_ 占位（例如 %s）。换成你自己的值之前不要拿去上线。这一项不让检查失败。" % (len(found), found[0])]


def check_data(data, filename="config.json", schema=None):
    schema = schema if schema is not None else load_schema()
    if not isinstance(data, dict):
        return [("（根）", "配置最外层要是一个对象。")], []
    errors = validate(data, schema)
    seen = {e[0] for e in errors}
    for path, msg in secret_errors(data):
        if path not in seen:
            errors.append((path, msg))
    return errors, placeholder_hints(data, filename)


def check_file(path, schema=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        return [("（文件）", "打不开：%s" % e.strerror)], []
    except json.JSONDecodeError as e:
        return [("（文件）", "不是合法的 JSON（第 %d 行）。" % e.lineno)], []
    return check_data(data, path, schema)


def main(argv):
    if len(argv) < 2:
        print("用法: python checks/check_config.py agents/<名字>.json")
        return 2
    schema = load_schema()
    failed = False
    for path in argv[1:]:
        errors, hints = check_file(path, schema)
        print(path)
        for item in hints:
            print("  提示  %s" % item)
        if not errors and not hints:
            print("  通过")
        for path_item, msg in errors:
            failed = True
            where = path_item or "（根）"
            print("  错误  %s：%s" % (where, msg))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
