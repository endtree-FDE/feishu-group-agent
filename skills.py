# 技能层：每个技能一个文件夹，里面一份 SKILL.md（agentskills.io：YAML 头含 name、description，正文是步骤）。
# 配置（默认关闭）：
#   "skills": {"enabled": false, "dir": "skills", "only": [], "index_budget_chars": 800,
#              "preload": true, "preload_budget_chars": 3000}
# only 不写或空列表：合法技能全部选用。非空才收窄目录、预放和 skill_read。
# 关闭时 prompt_block() 和 preload_block() 都返回空字符串，调用方不往提示词里加任何东西。
# 打开后：启动时扫描目录，提示词里只放「名字：描述」。
# YAML 头可以选填 triggers（一组关键词）。本题命中时，preload_block() 把得分最高的那一份正文
# （脱敏后，不超过 preload_budget_chars）交给调用方，放进会变的部分。没命中仍由 tools.skill_read 按需读取。
# preload 默认 true，只有 enabled 为 true 时才生效。
import os, re
import guard

# 这段连同每一行目录一起算进 index_budget_chars，保证注入的整块不超过预算。
CATALOG_HEADER = '技能目录（要用某个技能的步骤时，只输出一行：<<TOOL>>{"tool":"skill_read","name":"名字"}）：'
TRUNC_NOTE = "（技能较多，已按字数预算截断）"
# 预放块从这一句开头。acp_client.PRELOAD_MARK 必须相同，档案快照才不会把这段冻进去。
PRELOAD_MARK = "本次预放技能"
PRELOAD_NOTE = "（技能说明较长，已按字数预算截断）"
# 小写字母、数字、短横线；不能以短横线开头或结尾，也不能连续短横线。最长 64。
_NAME_RX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BODY_LIMIT = 8000


class SkillError(Exception):
    """读技能失败。str(e) 可以直接给用户看。"""


class SkillProblem:
    def __init__(self, folder, reason):
        self.folder, self.reason = folder, reason


class Skill:
    def __init__(self, name, description, path, triggers=None):
        self.name, self.description, self.path = name, description, path
        self.triggers = list(triggers or [])

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


def _unquote(val):
    val = (val or "").strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val


def _inline_list(val):
    """[a, b] 这种一行列表。不是列表就返回 None。"""
    if not (isinstance(val, str) and val.startswith("[") and val.endswith("]")):
        return None
    inner = val[1:-1].strip()
    if not inner:
        return []
    return [_unquote(p) for p in inner.split(",") if p.strip()]


def _parse_yaml_block(lines):
    """只解析技能头里用到的扁平 YAML：key: value、一行或缩进列表，以及 > / | 折叠块。不引入 PyYAML。"""
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
            if block and all(x.startswith("- ") for x in block):
                data[key] = [_unquote(x[2:]) for x in block if x[2:].strip()]
            else:
                data[key] = " ".join(block)
            continue
        inline = _inline_list(val)
        if inline is not None:
            data[key] = inline
            i += 1
            continue
        data[key] = _unquote(val)
        i += 1
    return data, None


def _only_list(raw):
    """skills.only：缺省、空列表、false 都表示「目录里合法的全部选用」。
    非空列表（或一个名字字符串）才收窄。非法形状当成没写，避免旧配置被误伤。"""
    if raw is None or raw is False or raw == "":
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out or None


def _trigger_list(raw):
    """triggers 选填。列表、或单个字符串，都收成去重后的关键词。别的形状当没有。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def _trigger_score(question, triggers):
    """命中的关键词长度相加。更长的词分更高；一个都没对上就是 0。"""
    q = question or ""
    score = 0
    for t in triggers or ():
        if t and t in q:
            score += len(t)
    return score


def _clip_preload(text, budget):
    """正文（含截断说明）不超过预算。放得下就原样返回。"""
    if len(text) <= budget:
        return text
    note = "\n" + PRELOAD_NOTE
    if len(note) >= budget:
        return note[-budget:]
    return text[:budget - len(note)].rstrip() + note


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
        # 预放默认开，但只有 enabled 为 true 时 preload_block 才会返回正文
        self.preload = bool(s.get("preload", True))
        try:
            self.preload_budget = int(s.get("preload_budget_chars", 3000))
        except (TypeError, ValueError):
            self.preload_budget = 3000
        if self.preload_budget <= 0:
            self.preload_budget = 3000
        self.root = os.path.join(base, self.dir_name)
        self.names = guard.load_names(os.path.join(base, cfg.get("redact_file", "redact.txt")))
        self.only = _only_list(s.get("only"))
        self.valid = []
        self.invalid = []
        self.skipped = []
        self.missing_only = []
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
            sk = Skill(str(data.get("name")).strip(), str(data.get("description")).strip(), md, _trigger_list(data.get("triggers")))
            self.valid.append(sk)
            self._by_name[sk.name] = sk
        self._apply_only()

    def _apply_only(self):
        """非空 only 才收窄。没被点名的合法技能不进目录，也不能 skill_read。"""
        if not self.only:
            return
        want = set(self.only)
        kept, skipped = [], []
        for sk in self.valid:
            if sk.name in want:
                kept.append(sk)
            else:
                skipped.append(sk)
        self.valid = kept
        self.skipped = skipped
        self._by_name = {sk.name: sk for sk in kept}
        invalid_names = {p.folder for p in self.invalid}
        found = {sk.name for sk in kept}
        self.missing_only = [n for n in self.only if n not in found and n not in invalid_names]

    def status(self):
        extra = ""
        if self.invalid:
            extra += " 不合法=%d" % len(self.invalid)
        if self.skipped:
            extra += " 未选用=%d" % len(self.skipped)
        if self.missing_only:
            extra += " 未找到=%d" % len(self.missing_only)
        return "enabled=%s 数量=%d%s" % (self.enabled, len(self.valid), extra)

    def prompt_block(self):
        """给提示词的技能目录。关闭、没有合法技能时返回空字符串（调用方什么都不加）。"""
        if not self.enabled or not self.valid:
            return ""
        return _fit(CATALOG_HEADER, [s.line for s in self.valid], self.budget)

    def _load_body(self, name):
        """正文（去掉 YAML 头）并脱敏。名字必须在本次启动扫到的目录里。"""
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
        return guard.redact(body.strip(), self.names)

    def read(self, name):
        """只返回 SKILL.md 正文（去掉 YAML 头），并脱敏。名字必须在本次启动扫到的目录里。"""
        text = self._load_body(name)
        if len(text) > _BODY_LIMIT:
            text = text[:_BODY_LIMIT].rstrip() + "\n（技能说明较长，已截断）"
        return text

    def pick_preload(self, question):
        """命中触发词时返回得分最高的技能。没有、或预放没开，返回 None。得分相同取目录里靠前的。"""
        if not self.enabled or not self.preload or not self.valid:
            return None
        best, best_score = None, 0
        for sk in self.valid:
            score = _trigger_score(question, sk.triggers)
            if score > best_score:
                best, best_score = sk, score
        return best

    def preload_block(self, question):
        """给本题可变部分的预放正文。关闭、未开启预放、没命中时返回空字符串。最多一份。"""
        sk = self.pick_preload(question)
        if not sk:
            return ""
        try:
            text = self._load_body(sk.name)
        except SkillError:
            return ""
        if not text.strip():
            return ""
        text = _clip_preload(text, self.preload_budget)
        return "%s（%s，按这些步骤回答，不必再读取这份技能）：\n%s" % (PRELOAD_MARK, sk.name, text)
