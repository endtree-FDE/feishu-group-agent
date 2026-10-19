# 文本管道用 UTF-8。不依赖 PYTHONUTF8 或 PYTHONIOENCODING。
# 接到控制台时保持系统代码页，中文 Windows 上直接看屏幕仍按原来的代码页显示。
import sys

_UTF8 = {"utf8", "utf8sig", "cp65001"}


def _is_utf8(encoding):
    key = (encoding or "").lower().replace("-", "").replace("_", "")
    return key in _UTF8


def configure():
    """管道和重定向不是 UTF-8 时改成 UTF-8。已经是 UTF-8，或正连着控制台，则不动。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        if _is_utf8(getattr(stream, "encoding", None)):
            continue
        try:
            tty = stream.isatty()
        except Exception:
            tty = False
        if tty:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


configure()
