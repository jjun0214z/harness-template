#!/usr/bin/env python3
"""규칙 잠금 훅 (PreToolUse: Edit · Write · MultiEdit · NotebookEdit · Bash · apply_patch).

하네스 핵심 파일이 조용히 흔들리지 않게 하는 장치다. 설정은 하네스 저장소 루트의 harness.json.
- 핵심 경로: CLAUDE.md · AGENTS.md · harness.json · .claude/ · .claude-plugin/ · .codex/ · .agents/ · plugins/ · harness/
- 묻지 않는 곳: docs/기록/ (근거는 쌓이기만 한다) · 상황판.md · scripts/
- 격리 워크트리 안의 핵심 경로 쓰기는 묻지 않는다. 확인은 하네스 기준 브랜치로 가는 push 때
  플러그인 push 가드가 바뀐 규칙 파일 목록을 들고 한 번 묻는다.
- 공유 체크아웃(주 작업 트리)의 핵심 경로 쓰기는 파일마다 묻는다.

격리 워크트리 판정(세션 root 가 아니라 쓰는 대상 파일 기준):
1. 대상 경로가 `<하네스>/.claude/worktrees/` 또는 `<프로젝트 폴더>/.work/` 아래다.
   안쪽 배치(harness.json layout)면 `<하네스>/.repos/` · `<하네스>/.works/` 아래도 같다: 작업 레포와 그 워크트리이지
   하네스 규칙 파일이 아니다(그 안의 `CLAUDE.md` · `.claude/` 는 그 저장소 것이다).
2. 대상이 속한 git 작업 트리가 링크된 워크트리다(`git rev-parse --git-dir` != `--git-common-dir`).
Bash 는 핵심 경로가 쓰기 대상일 때만 묻는다. 명령은 shellwords 로 읽는다(`cd X &&` · `sh -c` · `$(…)` · heredoc).
위치를 알 수 없는 쓰기(`cd $X && …`)나 따옴표가 깨진 명령은 핵심 경로를 언급하면 묻는다. 완벽한 판정은 아니다.
한 줄 코드(python -c · perl/ruby/node -e · `python3 - <<EOF`)는 쓰기 · 지우기 · 옮기기 · 다른 명령 실행을 할 수 있을 때만 묻는다.
읽기로 확실한 것(open 읽기 모드 · json.load · read_text 등)만 있으면 묻지 않는다. 모르는 호출 · 동적 값 · exec/eval 은 묻는다.
한계: 이 훅은 실수 방지용이며 의도적 우회를 다 막지는 못한다(경로를 글자 조합 · 변수로 만들거나, 별도 스크립트 파일을
실행하거나, 모르는 라이브러리의 읽기처럼 보이는 함수가 실제로 쓰는 경우 등).
Codex 는 permissionDecision=ask 를 지원하지 않아 systemMessage 경고로 대신한다.
"""
import ast
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellwords  # noqa: E402  같은 폴더

DEFAULT_PROTECTED = ("CLAUDE.md", "AGENTS.md", "harness.json", ".claude/", ".claude-plugin/", ".codex/", ".agents/", "plugins/", "harness/")


def load_settings():
    """하네스 루트 harness.json 의 보호 경로 · 호칭. 못 읽으면 기본값."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(here, "harness.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    rules = cfg.get("rules") or {}
    protected = tuple(rules.get("protected") or DEFAULT_PROTECTED)
    owner = (cfg.get("project") or {}).get("owner_title") or "결정권자"
    return protected, owner, inner_layout_dirs(cfg)


# harnesslib.LAYOUT_RESERVED · layout_dir_ok 와 같은 목록 · 같은 조건이다(훅은 하네스 스크립트를 import 하지 않는다). 고치면 둘 다 고친다.
LAYOUT_RESERVED = (".git", ".claude", ".claude-plugin", ".codex", ".agents", ".github", ".work",
                   ".gitignore", ".gitattributes", ".harness-manifest.json", ".harness-template", ".env", ".ds_store")


def inner_layout_dirs(cfg):
    """안쪽 배치의 작업 레포 · 워크트리 폴더 이름(예: .repos · .works). 그 안은 하네스 규칙 파일이 아니다.
    설정이 검증 조건에 하나라도 안 맞으면 빈 튜플: 보호를 풀지 않는다(`harness` · `.claude` · `plugins/p` 같은 값으로 규칙 파일이 열리지 않게)."""
    lay = cfg.get("layout") if isinstance(cfg, dict) else None
    if not isinstance(lay, dict) or set(lay) != {"repos_dir", "works_dir"}:
        return ()

    def ok(v):
        return (isinstance(v, str) and v.startswith(".") and v not in (".", "..") and len(v) <= 80 and v.strip() == v
                and not any(c in v for c in '/\\:*?"<>|') and v.lower() not in LAYOUT_RESERVED)

    a, b = lay["repos_dir"], lay["works_dir"]
    if not ((a == ".." or ok(a)) and (b == "../.work" or ok(b))):
        return ()
    if ok(a) and ok(b) and a.lower() == b.lower():
        return ()
    return tuple(v for v in (a, b) if ok(v))


PROTECTED, OWNER, INNER_DIRS = load_settings()
# 쓰기 명령: 인자 중 핵심 경로가 있으면 묻는다. 읽기 명령(ls · cat · grep · find · git log 등)은 묻지 않는다.
WRITE_CMDS = {"tee", "mv", "cp", "rm", "truncate", "ln", "touch", "mkdir", "rmdir", "install", "rsync", "patch"}
INLINE_CMDS = {"python", "python3", "perl", "node", "ruby"}  # 한 줄 코드가 핵심 경로를 쓸 수 있으면 묻는다
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
PATH_TOKEN = re.compile(r"[^\s\"'(),;=<>|&{}]+")
WHERE = f"규칙 변경은 격리 워크트리에서 하고, 기준 브랜치에 올릴 때 {OWNER} 확인을 거친다."


# ── 한 줄 코드 판정 ─────────────────────────────────────────────────────────
# 코드가 파일을 쓰기 · 지우기 · 옮기기 하거나 다른 명령을 돌릴 수 있으면(또는 판단이 애매하면) True.
# 읽기로 확실한 것만 False 다. 모르는 호출 · 동적 값 · exec/eval 은 쓸 수 있는 쪽(True)으로 본다.

# python: 이름으로 부르는 내장 함수 중 파일을 건드리지 않는 것(open 은 모드를 따로 본다)
PY_SAFE_FUNCS = {
    "print", "len", "str", "int", "float", "bool", "list", "dict", "set", "frozenset", "tuple", "bytes", "sorted",
    "reversed", "enumerate", "range", "zip", "min", "max", "sum", "any", "all", "isinstance", "repr", "type",
    "abs", "round", "hasattr", "ord", "chr", "hex", "format", "iter", "next", "open",
}
# python: 「모듈.함수」 단위로 읽기만 하는 것. import 별칭을 풀어 이 이름과 맞춘다
PY_SAFE_QUALIFIED = {
    "json.load", "json.loads", "json.dumps", "os.listdir", "os.getcwd", "os.walk", "os.scandir", "os.getenv",
    "os.environ.get", "os.path.join", "os.path.exists", "os.path.isfile", "os.path.isdir", "os.path.basename",
    "os.path.dirname", "os.path.abspath", "os.path.realpath", "os.path.relpath", "os.path.expanduser",
    "os.path.splitext", "os.path.normpath", "os.path.getsize", "os.path.isabs", "glob.glob", "re.match",
    "re.search", "re.findall", "re.finditer", "re.fullmatch", "re.sub", "re.split", "re.compile", "sys.exit",
    "fnmatch.fnmatch", "pathlib.Path", "pathlib.PurePath", "pathlib.Path.cwd", "pathlib.Path.home",
}
PY_OPEN_QUALIFIED = {"io.open", "codecs.open", "builtins.open"}  # open 처럼 두 번째 인자부터 모드
# python: 부르지 않고 값으로만 읽는 모듈 속성
PY_SAFE_ATTRS = {"sys.argv", "sys.stdin", "sys.stdout", "sys.stderr", "sys.platform", "sys.version", "os.environ",
                 "os.sep", "os.linesep", "os.path.sep", "os.curdir"}
# python: 받는 쪽이 모듈이 아닐 때(문자열 · dict · list · Path · 정규식 결과) 읽기만 하는 메서드
PY_SAFE_METHODS = {
    "read", "read_text", "read_bytes", "readline", "readlines", "get", "keys", "values", "items", "split", "rsplit",
    "strip", "lstrip", "rstrip", "join", "startswith", "endswith", "format", "lower", "upper", "title", "casefold",
    "splitlines", "count", "find", "rfind", "index", "rindex", "partition", "rpartition", "zfill", "ljust", "rjust",
    "center", "isdigit", "isalpha", "isspace", "append", "extend", "insert", "pop", "sort", "encode", "decode",
    "exists", "is_file", "is_dir", "glob", "rglob", "iterdir", "resolve", "relative_to", "with_suffix", "with_name",
    "match", "search", "findall", "finditer", "fullmatch", "sub", "group", "groups", "setdefault", "update",
    "stat", "as_posix", "absolute", "samefile",
}
PY_DYNAMIC = {"exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
              "__builtins__", "breakpoint", "input"}
PY_WRITE_MODE = set("wax+")
PY_OPEN_KWARGS = {"mode", "encoding", "errors", "newline"}


def _py_modes_write(args, keywords):
    """open 류의 모드 자리(위치 인자 · mode=)가 읽기 전용으로 확실하지 않으면 True. 정수 플래그 · 변수도 True."""
    if any(k.arg not in PY_OPEN_KWARGS for k in keywords):
        return True  # opener= · closefd= · **kw
    vals = list(args) + [k.value for k in keywords if k.arg == "mode"]
    for v in vals:
        if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
            return True  # 변수 · 정수 플래그(os.O_WRONLY) · *args
        if PY_WRITE_MODE & set(v.value):
            return True
    return False


def python_may_write(code):
    """읽기로 확실한 것만 False. 모르는 호출 · 별칭 · 값으로 넘긴 함수 · 동적 접근은 True."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return True
    alias = {}  # 이 코드 안의 import 이름 → 모듈.이름
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                if al.asname:
                    alias[al.asname] = al.name
                else:
                    alias[al.name.split(".")[0]] = al.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                return True
            for al in node.names:
                if al.name == "*":
                    return True
                alias[al.asname or al.name] = f"{node.module}.{al.name}"
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node

    def dotted(n):
        """import 별칭에서 시작하는 속성 사슬이면 풀린 이름. 아니면 None."""
        parts = []
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name) and n.id in alias:
            return ".".join([alias[n.id]] + parts[::-1])
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in PY_DYNAMIC:
            return True
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return True
        if isinstance(node, (ast.Starred, ast.Global, ast.Nonlocal)):
            return True
        # 이름 바꿔치기: 안전 목록 · import 별칭 이름에 다른 것을 넣거나, 모듈 속성에 대입하면 묻는다
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in PY_SAFE_FUNCS or name in PY_SAFE_METHODS or name in alias \
                    or (isinstance(node, ast.Attribute) and dotted(node.value)):
                return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and \
                (node.name in PY_SAFE_FUNCS or node.name in PY_SAFE_METHODS or node.name in alias):
            return True
        if isinstance(node, ast.arg) and (node.arg in PY_SAFE_FUNCS or node.arg in alias):
            return True
        # 모듈에서 꺼낸 이름(속성 사슬 끝 · from 으로 받은 이름)은 부르든 값으로 넘기든 안전 목록에 있어야 한다
        p = parent.get(node)
        outer = isinstance(node, (ast.Attribute, ast.Name)) and not (isinstance(p, ast.Attribute) and p.value is node)
        if outer and isinstance(getattr(node, "ctx", None), ast.Load):
            q = dotted(node)
            if q is not None:
                called = isinstance(p, ast.Call) and p.func is node
                ok = (q in PY_SAFE_QUALIFIED or q in PY_OPEN_QUALIFIED) if called else (q in PY_SAFE_ATTRS or q in PY_SAFE_QUALIFIED)
                if not ok:
                    return True
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        q = dotted(f)
        if q is not None:
            if q in PY_OPEN_QUALIFIED and _py_modes_write(node.args[1:], node.keywords):
                return True
            continue  # 이름 검사는 위에서 했다
        if isinstance(f, ast.Name):
            if f.id not in PY_SAFE_FUNCS:
                return True
            if f.id == "open" and _py_modes_write(node.args[1:], node.keywords):
                return True
        elif isinstance(f, ast.Attribute):
            if f.attr == "open":  # Path(...).open(mode) 같은 메서드: 모든 인자가 모드 자리
                if _py_modes_write(node.args, node.keywords):
                    return True
            elif f.attr == "replace":
                # str.replace 만 안전: 받는 쪽이 문자열 리터럴일 때만. os.replace · Path.replace 는 파일을 옮긴다
                if not (isinstance(f.value, ast.Constant) and isinstance(f.value.value, str)):
                    return True
            elif f.attr not in PY_SAFE_METHODS:
                return True
        else:
            return True  # d['f']() · (lambda: …)() 처럼 무엇을 부르는지 모른다
        if any(k.arg is None for k in node.keywords):
            return True
    # 모듈 함수를 값으로 넘기는 것(map(os.remove, …))은 위의 「모듈에서 꺼낸 이름」 검사가 막는다.
    return False


PERL_WRITE = re.compile(r"\$\^I|\b(?:BEGIN|END|INIT|CHECK|unlink|rename|rmdir|mkdir|truncate|chmod|chown|utime|link|symlink|system|exec|eval|"
                        r"qx|sysopen|do|require|syscall|fork|kill|File::\w+|copy|move)\b|`")
PERL_OPEN = re.compile(r"\bopen\s*\(?\s*(?:my\s+)?[$\w]+\s*,\s*(['\"])(.*?)\1(\s*,)?")
NODE_WRITE = re.compile(
    r"\b(?:write\w*|append\w*|unlink\w*|rm|rmSync|rmdir\w*|rename\w*|copy\w*|cp|cpSync|mkdir\w*|mkdtemp\w*|"
    r"truncate\w*|symlink\w*|link|linkSync|chmod\w*|chown\w*|utimes\w*|open|openSync|createWriteStream|"
    r"child_process|exec\w*|spawn\w*|fork|eval|Function|import|process\.binding|vm|globalThis|constructor)\b"
    r"|\brequire\s*\(\s*[^'\"\s]|`|[\w)\]]\s*\[|\\[xu0-7]")
RUBY_WRITE = re.compile(
    r"\b(?:FileUtils|IO|Open3|Kernel|system|exec|spawn|eval|instance_eval|class_eval|send|public_send|"
    r"syscall|fork|load|require|reopen|method|__send__|const_get|instance_variable_get)\b|%x|`|\bFile\.(?:write|binwrite|delete|unlink|rename|truncate|symlink|link|"
    r"chmod|chown|utime|new|mkfifo)\b|\bDir\.(?:mkdir|rmdir|delete|unlink)\b|\.(?:write|syswrite|truncate)\b")
RUBY_OPEN = re.compile(r"\b(?:File\.)?open\s*\(?\s*(['\"])(.*?)\1\s*(?:,\s*(['\"])(.*?)\3)?\s*([,)]?)")


def perl_may_write(code, flags):
    if any(f.startswith("-") and not f.startswith("--") and "i" in f[1:] for f in flags):
        return True  # perl -i · -pi : 파일을 제자리에서 고친다
    if PERL_WRITE.search(code):
        return True
    opens = len(re.findall(r"\bopen\b", code))
    reads = 0
    for m in PERL_OPEN.finditer(code):
        mode, three = m.group(2).strip(), bool(m.group(3))
        if three:
            if mode not in ("<", "<:encoding(UTF-8)", "<:utf8", "<:raw"):
                return True
        elif mode[:1] in (">", "|", "+") or mode.endswith("|"):
            return True
        reads += 1
    return opens != reads  # 모드를 못 읽은 open 이 있다


def node_may_write(code):
    return bool(NODE_WRITE.search(code))


def ruby_may_write(code):
    if RUBY_WRITE.search(code):
        return True
    opens = len(re.findall(r"\bopen\b", code))
    reads = 0
    for m in RUBY_OPEN.finditer(code):
        path, mode = m.group(2), m.group(4)
        if path.startswith("|") or (mode is not None and mode not in ("r", "rb", "rt", "r:UTF-8", "r:utf-8")):
            return True
        if mode is None and m.group(5) == ",":
            return True  # 모드가 변수다
        reads += 1
    return opens != reads


def inline_lang(head):
    if re.match(r"^python[0-9.]*$", head):
        return "python"
    return head if head in ("perl", "node", "ruby") else None


def inline_code(lang, rest):
    """명령줄에서 코드(-c · -e) 와 그 밖의 인자. 코드 인자가 없으면 (None, rest)."""
    flag = {"python": "c", "perl": "eE", "ruby": "e", "node": "ep"}[lang]
    longs = ("--eval", "--print") if lang == "node" else ()
    for i, t in enumerate(rest):
        if t in longs or (t.startswith("-") and not t.startswith("--") and len(t) > 1 and t[-1] in flag):
            if i + 1 < len(rest):
                return rest[i + 1], rest[:i + 1] + rest[i + 2:]
            return None, rest
        if lang == "python" and not t.startswith("-"):
            break  # 스크립트 파일 이름 뒤는 그 스크립트의 인자다
    return None, rest


def code_may_write(lang, code, flags=()):
    if lang == "python":
        return python_may_write(code)
    if lang == "perl":
        return perl_may_write(code, flags)
    if lang == "node":
        return node_may_write(code)
    if lang == "ruby":
        return ruby_may_write(code)
    return True


def ask(reason, data):
    # Codex PreToolUse는 permissionDecision=ask를 아직 지원하지 않는다.
    # 실패로 처리해 조용히 통과시키는 대신 모델에 명시적 경고를 넣는다.
    if data.get("turn_id"):
        print(json.dumps({"systemMessage": reason + " 사용자에게 명시적 승인을 받은 뒤에만 진행한다."}, ensure_ascii=False))
        sys.exit(0)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def is_protected(r):
    if r.startswith(".."):
        return False
    r = r.replace(os.sep, "/")
    return any(r == p or (p.endswith("/") and r.startswith(p)) for p in PROTECTED)


def mentions(text):
    return any(p in text for p in PROTECTED)


def git_info(path):
    """path 가 속한 git 작업 트리의 (최상위, git-dir, common-dir). 못 읽으면 None."""
    d = path
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    try:
        out = subprocess.run(
            ["git", "-C", d, "rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) < 3:
        return None
    top = os.path.realpath(lines[0])
    git_dir = os.path.realpath(os.path.join(d, lines[1]))
    common = os.path.realpath(os.path.join(d, lines[2]))
    return top, git_dir, common


class Judge:
    """대상 경로 하나가 「묻는 핵심 경로」(공유 체크아웃의 핵심 경로)인지 판정한다."""

    def __init__(self, root):
        self.root = os.path.realpath(root)
        info = git_info(self.root)
        self.common = info[2] if info else None
        # 주 작업 트리 = common-dir(.git) 의 부모. 세션이 워크트리에서 떠도 주 트리를 찾는다.
        if self.common and os.path.basename(self.common) == ".git":
            main = os.path.dirname(self.common)
        else:
            main = self.root
        self.isolated_prefixes = (
            os.path.join(main, ".claude", "worktrees") + os.sep,
            os.path.join(os.path.dirname(main), ".work") + os.sep,
        ) + tuple(os.path.join(main, d) + os.sep for d in INNER_DIRS)

    @staticmethod
    def resolve(base, path):
        return os.path.realpath(os.path.join(base, os.path.expanduser(path)))

    def protected(self, abspath):
        if any(abspath.startswith(p) for p in self.isolated_prefixes):
            return False
        info = git_info(abspath)
        if info is None:
            return is_protected(os.path.relpath(abspath, self.root))
        top, git_dir, common = info
        if git_dir != common:
            return False  # 링크된 워크트리 = 격리 작업 공간
        if self.common and common != self.common:
            return False  # 다른 저장소
        return is_protected(os.path.relpath(abspath, top))


def writes_protected(judge, base, args, redirs):
    """단순 명령 하나(위치 base)가 공유 체크아웃의 핵심 경로를 쓰는가."""
    for op, target in redirs:
        if ">" in op and target and target != "/dev/null" and not target.startswith("&") \
                and mentions(target) and judge.protected(judge.resolve(base, target)):
            return True
    if not args:
        return False
    head, rest = os.path.basename(args[0]), args[1:]
    if head == "git" and rest[:1] in (["mv"], ["rm"], ["checkout"], ["restore"]):
        head, rest = "mv", rest[1:]
    if head == "sed" and any(a.startswith("-i") for a in rest):
        head = "mv"
    if head in WRITE_CMDS:
        for a in rest:
            a = a.split("=")[-1]
            if mentions(a) and judge.protected(judge.resolve(base, a)):
                return True
    lang = inline_lang(head)
    if lang:
        text = " ".join(rest)
        if not mentions(text):
            return False
        code, others = inline_code(lang, rest)
        if code is None:  # 스크립트 파일 실행 · 표준 입력: 예전 기준(쓰기 낌새가 있으면 경로를 본다)
            hit = re.search(r"open\(|write|unlink|rename|replace", text) or (lang == "perl" and perl_may_write("", rest))
        else:
            hit = code_may_write(lang, code, others)
        if hit:
            for t in PATH_TOKEN.findall(text):
                if mentions(t) and judge.protected(judge.resolve(base, t)):
                    return True
    return False


def bash_writes_protected(judge, base, cmd):
    try:
        steps = list(shellwords.walk(cmd, base))
    except ValueError:
        return True  # 따옴표가 깨졌다: 핵심 경로를 언급한 명령이라 묻는다(부르는 쪽이 mentions 를 먼저 봤다)
    for wheres, args, redirs in steps:
        for where in wheres:
            if where is None:
                if any(mentions(a) for a in args) or any(mentions(t) for _, t in redirs):
                    return True  # 위치를 모르는 곳에서 핵심 경로를 다룬다
                continue
            if writes_protected(judge, where, args, redirs):
                return True
    # heredoc 을 코드로 받는 명령(python3 - <<EOF · sh <<EOF)은 본문이 핵심 경로를 쓰는지 본다
    for header, body in shellwords.heredocs(cmd):
        if not mentions(body):
            continue
        m = re.search(r"(?:^|[\s;&|(])(python[0-9.]*|perl|ruby|node|sh|bash|zsh)\b", header)
        if not m:
            continue
        lang = inline_lang(m.group(1))
        if lang is None:  # 셸 본문: 예전 기준
            hit = re.search(r"open\(|write|unlink|rename|replace|>", body)
        elif re.search(r"<<-?\s*[A-Za-z_]", header) and re.search(r"[$`]", body):
            hit = True  # 따옴표 없는 heredoc: 셸이 본문의 $ · 백틱을 먼저 바꿔 코드를 확정할 수 없다
        else:
            hit = code_may_write(lang, body)
        if hit:
            for t in PATH_TOKEN.findall(body):
                if mentions(t) and judge.protected(judge.resolve(base, t)):
                    return True
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    cwd = data.get("cwd") or os.getcwd()
    root = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tin.get("file_path") or tin.get("notebook_path") or ""
        if path and mentions(path):
            judge = Judge(root)
            abspath = judge.resolve(cwd, path)
            if judge.protected(abspath):
                ask(f"공유 체크아웃의 하네스 규칙 파일 `{abspath}` 을 고치려 한다. {WHERE}", data)
    elif tool == "apply_patch":
        patch = tin.get("command") or tin.get("patch") or tin.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(map(str, patch))
        if mentions(patch):
            judge = Judge(root)
            files = [f.strip() for f in PATCH_FILE.findall(patch)]
            if not files:
                files = [PROTECTED[0]]  # 파일 머리를 못 읽으면 cwd 가 격리 워크트리인지로 판정한다
            if any(mentions(f) and judge.protected(judge.resolve(cwd, f)) for f in files):
                ask(f"패치가 공유 체크아웃의 하네스 규칙 파일을 바꿀 수 있다. {WHERE}", data)
    elif tool in ("Bash", "exec_command"):
        cmd = tin.get("command") or tin.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(map(str, cmd))
        if mentions(cmd) and bash_writes_protected(Judge(root), cwd, cmd):
            ask(f"명령이 공유 체크아웃의 하네스 규칙 파일({' · '.join(PROTECTED)})을 바꿀 수 있다. {WHERE}", data)
    sys.exit(0)


if __name__ == "__main__":
    main()
