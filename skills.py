# 技能层：每个技能一个文件夹，里面一份 SKILL.md（agentskills.io：YAML 头含 name、description，正文是步骤）。
# 配置（默认关闭）：
#   "skills": {"enabled": false, "dir": "skills", "index_budget_chars": 800}
# 关闭时 prompt_block() 返回空字符串，调用方不往提示词里加任何东西。
# 打开后：启动时扫描目录，提示词里只放「名字：描述」；正文由 tools.skill_read 按需读取。
import os, re
import guard

# 这段连同每一行目录一起算进 index_budget_chars，保证注入的整块不超过预算。
CATALOG_HEADER = '技能目录（要用某个技能的步骤时，只输出一行：<<TOOL>>{"tool":"skill_read","name":"名字"}）：'
TRUNC_NOTE = "（技能较多，已按字数预算截断）"
# 小写字母、数字、短横线；不能以短横线开头或结尾，也不能连续短横线。最长 64。
_NAME_RX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BODY_LIMIT = 8000


class SkillError(Exception):
    """读技能失败。str(e) 可以直接给用户看。"""


class SkillProblem:
    def __init__(self, folder, reason):
        self.folder, self.reason = folder, reason


class Skill:
    def __init__(self, name, description, path):
        self.name, self.description, self.path = name, description, path

    @property
    def line(self):
        desc = re.sub(r"\s+", " ", self.description).strip()
        return "%s：%s" % (self.name, desc)


def _contained(root, path):
    """path 解析后必须落在 root 里面（挡住符号链接和 ../）。"""
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if os.name == "nt":
        root, path = os.path.normcase(root), os.path.normcase(path)
    if path == root:
        return False
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _name_ok(name):
    return isinstance(name, str) and 1 <= len(name) <= 64 and _NAME_RX.match(name) is not None


def _parse_yaml_block(lines):
    """只解析技能头里用到的扁平 YAML：key: value，以及 > / | 折叠块。不引入 PyYAML。"""
    data = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            return None, "YAML 头无法解析"
        key, val = m.group(1), m.group(2).strip()
        if val in (">", ">-", "|", "|-"):
            fold = val.startswith(">")
            i += 1
            block = []
            while i < n and (not lines[i].strip() or lines[i][:1] in " \t"):
                if lines[i].strip():
                    block.append(lines[i].strip())
                i += 1
            data[key] = (" ".join(block) if fold else "\n".join(block).strip())
            continue
        if val == "":
            i += 1
            block = []
            while i < n and (not lines[i].strip() or lines[i][:1] in " \t"):
                if lines[i].strip():
                    block.append(lines[i].strip())
                i += 1
            data[key] = " ".join(block)
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        data[key] = val
        i += 1
    return data, None


def parse_skill(text):
    """返回 (data, body, error)。body 不含 YAML 头。"""
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None, "缺少 YAML 头"
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, None, "YAML 头没有结束"
    data, err = _parse_yaml_block(lines[1:end])
    if err:
        return None, None, err
    body = "\n".join(lines[end + 1:]).strip()
    return data, body, None


def _classify(folder, data, body):
    name = str(data.get("name") or "").strip()
    desc = str(data.get("description") or "").strip()
    if not name:
        return "缺少 name"
    if not _name_ok(name):
        return "name 只能用小写字母、数字和短横线，不能以短横线开头或结尾，也不能有连续短横线"
    if name != folder:
        return "name 必须和文件夹名一致"
    if not desc:
        return "缺少 description"
    if len(desc) > 1024:
        return "description 超过 1024 字"
    comp = str(data.get("compatibility") or "").strip()
    if comp and len(comp) > 500:
        return "compatibility 超过 500 字"
    if not body:
        return "缺少正文"
    return None


def _fit(header, lines, budget):
    """整块（标题 + 各行 + 截断说明）不超过 budget。放得下就原样返回。"""
    full = header + "\n" + "\n".join(lines)
    if len(full) <= budget:
        return full
    note = TRUNC_NOTE
    if len(header) + 1 + len(note) > budget:
        room = budget - len(note)
        if room <= 0:
            return note[:budget]
        return header[:room].rstrip() + note
    body_room = budget - len(header) - len(note) - 2
    kept = []
    for ln in lines:
        trial = ln if not kept else "\n".join(kept + [ln])
        if len(trial) <= body_room:
            kept.append(ln)
            continue
        if not kept and body_room > 0:
            cut = ln[:body_room].rstrip()
            if cut:
                return header + "\n" + cut + "\n" + note
        break
    if not kept:
        return header + "\n" + note
    return header + "\n" + "\n".join(kept) + "\n" + note


class SkillSet:
    def __init__(self, cfg, base):
        s = cfg.get("skills") or {}
        self.enabled = bool(s.get("enabled", False))
        self.dir_name = s.get("dir") or "skills"
        try:
            self.budget = int(s.get("index_budget_chars", 800))
        except (TypeError, ValueError):
            self.budget = 800
        if self.budget <= 0:
            self.budget = 800
        self.root = os.path.join(base, self.dir_name)
        self.names = guard.load_names(os.path.join(base, cfg.get("redact_file", "redact.txt")))
        self.valid = []
        self.invalid = []
        self._by_name = {}
        self._scan()

    def _scan(self):
        if not os.path.isdir(self.root):
            return
        for folder in sorted(os.listdir(self.root)):
            if not folder or folder.startswith("."):
                continue
            path = os.path.join(self.root, folder)
            if not os.path.isdir(path):
                continue
            if not _contained(self.root, path):
                self.invalid.append(SkillProblem(folder, "技能文件不在技能目录内"))
                continue
            md = os.path.join(path, "SKILL.md")
            if not os.path.isfile(md):
                self.invalid.append(SkillProblem(folder, "缺少 SKILL.md"))
                continue
            if not _contained(self.root, md):
                self.invalid.append(SkillProblem(folder, "技能文件不在技能目录内"))
                continue
            try:
                raw = open(md, encoding="utf-8-sig").read()
            except OSError:
                self.invalid.append(SkillProblem(folder, "无法读取 SKILL.md"))
                continue
            data, body, err = parse_skill(raw)
            if err:
                self.invalid.append(SkillProblem(folder, err))
                continue
            reason = _classify(folder, data, body)
            if reason:
                self.invalid.append(SkillProblem(folder, reason))
                continue
            sk = Skill(str(data.get("name")).strip(), str(data.get("description")).strip(), md)
            self.valid.append(sk)
            self._by_name[sk.name] = sk

    def status(self):
        extra = "" if not self.invalid else " 不合法=%d" % len(self.invalid)
        return "enabled=%s 数量=%d%s" % (self.enabled, len(self.valid), extra)

    def prompt_block(self):
        """给提示词的技能目录。关闭、没有合法技能时返回空字符串（调用方什么都不加）。"""
        if not self.enabled or not self.valid:
            return ""
        return _fit(CATALOG_HEADER, [s.line for s in self.valid], self.budget)

    def read(self, name):
        """只返回 SKILL.md 正文（去掉 YAML 头），并脱敏。名字必须在本次启动扫到的目录里。"""
        if name is None or str(name).strip() == "":
            raise SkillError("没有给出技能名字")
        name = str(name).strip()
        if not _name_ok(name):
            raise SkillError("技能名字不合法")
        sk = self._by_name.get(name)
        if not sk:
            raise SkillError("没有这个技能")
        path = sk.path
        if not _contained(self.root, path) or not os.path.isfile(path):
            raise SkillError("技能名字不合法")
        try:
            raw = open(path, encoding="utf-8-sig").read()
        except OSError:
            raise SkillError("这份技能暂时读不出来")
        data, body, err = parse_skill(raw)
        if err or not body or str(data.get("name") or "").strip() != name:
            raise SkillError("这份技能格式不对")
        text = guard.redact(body.strip(), self.names)
        if len(text) > _BODY_LIMIT:
            text = text[:_BODY_LIMIT].rstrip() + "\n（技能说明较长，已截断）"
        return text
