#!/usr/bin/env python3
"""훅이 Bash 명령을 읽는 법. push 가드와 규칙 보호 훅이 같이 쓴다.

같은 파일이 두 자리에 있다: `harness/hooks/shellwords.py` 와 플러그인 `scripts/shellwords.py`.
훅이 서로 다른 폴더에서 돌기 때문이다. 두 벌은 시험이 같은지 본다(원본은 템플릿 `harness/skeleton/plugin/scripts/`).

셸을 다 흉내 내지 않는다. 훅이 판정에 쓰는 것만 푼다.

- heredoc 본문은 지우고 따로 돌려준다(`heredocs`). 데이터로 볼지 코드로 볼지는 받는 명령에 달려서 훅이 정한다.
- 홑따옴표 안의 글은 낱말 하나로 남아 명령으로 보지 않는다.
  큰따옴표 안의 `$(…)` · 백틱은 셸이 실행하므로 **명령으로 꺼내 다시 읽는다.**
- `&&` `||` `;` `|` `&` `(` `)` `$(` 백틱 · 줄바꿈에서 명령을 나눈다.
- 앞에 붙은 `VAR=값`, `env` `command` `exec` `time` `nohup` `nice` `sudo` `timeout` `xcrun` 을 벗긴다.
- `sh -c '…'` · `bash -o x -c '…'` · `eval '…'` · `bash <<< '…'` 는 안의 글을 다시 명령으로 읽는다.
- `cd X &&` 는 뒤 명령의 위치를 바꾼다. `cd X;` · `(cd X)` · `popd` 처럼 실제로 안 바뀔 수 있으면
  **바뀐 곳과 원래 곳을 둘 다** 위치 후보로 둔다. 알 수 없는 곳(`$X` · `cd -`)이면 후보에 None 이 든다.
  None 을 받은 훅은 **묻는다**. 모르는 곳을 아는 곳처럼 보지 않는다.

따옴표가 짝이 안 맞는 명령은 ValueError 를 낸다. 훅은 그것도 묻는 쪽으로 받는다.
"""
import os
import re
import shlex

SEP = {"&&", "||", ";", "|", "&", "(", ")", "|&", ";;", ";&"}
WRAP = {"env", "command", "exec", "time", "nohup", "nice", "sudo", "timeout", "builtin", "xcrun"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}"}
SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(cmd):
    """heredoc 본문 줄을 지운다. 머리 줄(`cat <<'EOF'`)은 남긴다."""
    out, waiting = [], []
    for line in cmd.split("\n"):
        if waiting:
            end, dash = waiting[0]
            if (line.lstrip("\t") if dash else line) == end:
                waiting.pop(0)
            continue
        out.append(line)
        waiting = [(m.group(3), bool(m.group(1))) for m in _HEREDOC.finditer(line)]
    return "\n".join(out)


def heredocs(cmd):
    """[(머리 줄, 본문)] : 본문을 데이터로 볼지 코드로 볼지는 받는 명령에 달려서 훅이 정한다."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        heads = [(m.group(3), bool(m.group(1))) for m in _HEREDOC.finditer(lines[i])]
        header, i = lines[i], i + 1
        for end, dash in heads:
            body = []
            while i < len(lines) and (lines[i].lstrip("\t") if dash else lines[i]) != end:
                body.append(lines[i])
                i += 1
            i += 1
            out.append((header, "\n".join(body)))
    return out


def _lex(cmd):
    text = strip_heredocs(cmd).replace("$'", "'").replace("`", " ; ").replace("$(", " ( ").replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def substitutions(cmd):
    """홑따옴표 밖의 `$(…)` · 백틱 안쪽 글. 큰따옴표 안이어도 셸이 실행한다."""
    text, out, i, single = strip_heredocs(cmd), [], 0, False
    while i < len(text):
        c = text[i]
        if c == "'" and not single and _in_double(text, i):
            i += 1
            continue
        if c == "'":
            single = not single
        elif not single and text.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            out.append(text[i + 2:j - 1])
            i = j
            continue
        elif not single and c == "`":
            j = text.find("`", i + 1)
            j = len(text) if j < 0 else j
            out.append(text[i + 1:j])
            i = j + 1
            continue
        i += 1
    return out


def _in_double(text, i):
    """i 자리가 큰따옴표 안인가 (홑따옴표는 큰따옴표 안에서 글자일 뿐이다)."""
    n, single = 0, False
    for k, c in enumerate(text[:i]):
        if c == "'" and n % 2 == 0:
            single = not single
        elif c == '"' and not single and (k == 0 or text[k - 1] != "\\"):
            n += 1
    return n % 2 == 1


def is_redirect(tok):
    return bool(tok) and set(tok) <= set("<>&|") and ("<" in tok or ">" in tok)


def commands(cmd):
    """[(낱말들, [(리다이렉트, 대상)], 뒤 구분자, 괄호 깊이)] : 단순 명령 하나씩."""
    out, args, redirs, depth = [], [], [], 0
    toks = _lex(cmd)
    i = 0

    def cut(sep):
        nonlocal args, redirs
        if args or redirs:
            out.append((args, redirs, sep, depth))
        args, redirs = [], []

    while i < len(toks):
        t = toks[i]
        if t in SEP or t == "$":
            cut(t)
            if t == "(":
                depth += 1
            elif t == ")":
                depth = max(0, depth - 1)
                out.append(([], [], ")", depth))   # 서브셸이 끝났다는 표시
        elif is_redirect(t):
            if args and args[-1].isdigit():   # 2> 1> 의 숫자는 낱말이 아니다
                args.pop()
            target = toks[i + 1] if i + 1 < len(toks) and toks[i + 1] not in SEP else ""
            redirs.append((t, target))
            i += 1
        else:
            args.append(t)
        i += 1
    cut("")
    return out


def unwrap(args):
    """(벗긴 낱말들, 다시 읽을 안쪽 글 또는 None). `sh -c` · `eval` 의 안쪽 글을 꺼낸다."""
    args = list(args)
    while args:
        if args[0] in KEYWORDS:              # if [ -f x ]; then … : 제어 낱말 뒤가 명령이다
            args.pop(0)
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]):
            args.pop(0)
            continue
        head = os.path.basename(args[0])
        if head in WRAP:
            args.pop(0)
            # 감싼 명령의 옵션 · 값 · 시간 인자를 넘긴다
            while args and (args[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0])
                            or (head == "timeout" and re.match(r"^\d", args[0]))):
                opt = args.pop(0)
                if opt in ("-u", "-n", "-g", "-s", "-k", "-C") and args:
                    args.pop(0)
            continue
        if head == "eval":
            return [], " ".join(args[1:])
        if head in SHELLS:
            j = 1
            while j < len(args) and (args[j].startswith("-") or args[j].startswith("+")):
                a = args[j]
                if a in SHELL_VALUE_OPTS:
                    j += 2
                    continue
                if not a.startswith("--") and "c" in a[1:]:
                    return [], (args[j + 1] if j + 1 < len(args) else "")
                j += 1
        break
    return args, None


def reads_stdin_as_code(args):
    """표준 입력이나 인자 글을 코드로 돌리는 해석기인가. `| sh` · `| xargs` · `python3 -` · `perl -e` 따위."""
    if not args:
        return False
    head = os.path.basename(args[0])
    rest = [a for a in args[1:] if not a.startswith("-")]
    if head in ("xargs", "parallel"):
        return True                           # stdin 이 명령의 인자가 된다
    if head in SHELLS:
        return not rest                       # 스크립트 파일 없이 돌면 stdin 이 코드다
    if re.match(r"^(python[0-9.]*|perl|ruby|node|php|osascript)$", head):
        return not rest or any(a in ("-", "-c", "-e", "-E", "-r") for a in args[1:])
    return False


def git(cwd, args):
    """git 명령이면 (대상 저장소 위치, 서브커맨드, 그 뒤 인자). 아니면 None.

    전역 옵션(`-C` · `-c` · `--git-dir` · `--work-tree` · `--no-pager` …)을 넘긴다.
    위치를 알 수 없으면(`-C $X`) None 이다."""
    if not args or os.path.basename(args[0]) != "git":
        return None
    target, i = cwd, 1
    while i < len(args) and args[i].startswith("-"):
        opt, _, val = args[i].partition("=")
        if opt in GIT_VALUE_OPTS and not val:
            val = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        if opt in ("-C", "--git-dir", "--work-tree"):
            if target is None or "$" in val or not val:
                target = None
            else:
                path = os.path.normpath(os.path.join(target, os.path.expanduser(val)))
                if opt == "--git-dir" and os.path.basename(path) == ".git":
                    path = os.path.dirname(path)
                target = path
        i += 1
    if i >= len(args):
        return None
    return target, args[i], args[i + 1:]


def _cd(cwd, args):
    if cwd is None:
        return None
    if len(args) < 2:
        return os.path.expanduser("~")
    dest = [a for a in args[1:] if not (a.startswith("-") and a != "-")]
    if not dest:
        return os.path.expanduser("~")
    d = dest[0]
    if d == "-" or "$" in d:
        return None
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(d)))


def walk(cmd, cwd, depth=0):
    """(위치 후보들, 낱말들, 리다이렉트) 를 단순 명령마다 낸다. 후보에 None 이 들 수 있다."""
    where = (cwd,)
    stack, seen = [], [where]
    parts = commands(cmd)
    for n, (args, redirs, sep, level) in enumerate(parts):
        if sep == ")" and not args:
            if stack:
                inside = where
                where = stack.pop()
                where = tuple(dict.fromkeys(where + inside))   # 서브셸 안의 cd 가 새는 것처럼 본다
            continue
        while len(stack) < level:
            stack.append(where)
        args, inner = unwrap(args)
        if inner is not None:
            if depth < 4:
                for w in where:
                    yield from walk(inner, w, depth + 1)
            continue
        for op, target in redirs:
            if op == "<<<" and reads_stdin_as_code(args) and depth < 4:
                for w in where:
                    yield from walk(target, w, depth + 1)
        if args and args[0] in ("cd", "pushd", "popd"):
            if args[0] == "popd":
                where = tuple(dict.fromkeys(w for ws in seen for w in ws))
            else:
                moved = tuple(dict.fromkeys(_cd(w, args) for w in where))
                where = moved if sep == "&&" and level == 0 else tuple(dict.fromkeys(where + moved))
            seen.append(where)
            continue
        yield where, args, redirs
    if depth == 0:
        for inner in substitutions(cmd):
            if depth < 4:
                yield from walk(inner, cwd, depth + 1)
