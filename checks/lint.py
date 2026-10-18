#!/usr/bin/env python3
# 开源 CI 用的轻量检查：确认 Python 能编译、源文件是 UTF-8。
# 不调用格式化工具，也不按行宽重排。现有代码是紧凑写法，新代码沿用所在文件的 4 空格缩进。
import pathlib, py_compile, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", "memory", "logs", "state", "audit", "kb_inbox", "kb_staging", "kb_prev", "kb_new"}

def main():
    bad = []
    n = 0
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP for part in path.parts):
            continue
        n += 1
        try:
            path.read_text(encoding="utf-8")
        except UnicodeError as e:
            bad.append("%s 不是 UTF-8：%s" % (path.relative_to(ROOT), e))
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append("%s 无法编译：%s" % (path.relative_to(ROOT), e.msg))
    if bad:
        print("\n".join(bad))
        return 1
    print("lint ok：%d 个 Python 文件可以编译" % n)
    return 0

if __name__ == "__main__":
    sys.exit(main())
