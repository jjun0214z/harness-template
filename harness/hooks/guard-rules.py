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
2. 대상이 속한 git 작업 트리가 링크된 워크트리다(`git rev-parse --git-dir` != `--git-common-dir`).
Bash 는 핵심 경로가 쓰기 대상일 때만 묻는다. 명령은 shellwords 로 읽는다(`cd X &&` · `sh -c` · `$(…)` · heredoc).
위치를 알 수 없는 쓰기(`cd $X && …`)나 따옴표가 깨진 명령은 핵심 경로를 언급하면 묻는다. 완벽한 판정은 아니다.
Codex 는 permissionDecision=ask 를 지원하지 않아 systemMessage 경고로 대신한다.
"""
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
    return protected, owner


PROTECTED, OWNER = load_settings()
# 쓰기 명령: 인자 중 핵심 경로가 있으면 묻는다. 읽기 명령(ls · cat · grep · find · git log 등)은 묻지 않는다.
WRITE_CMDS = {"tee", "mv", "cp", "rm", "truncate", "ln", "touch", "mkdir", "rmdir", "install", "rsync", "patch"}
INLINE_CMDS = {"python", "python3", "perl", "node", "ruby"}  # 한 줄 코드가 핵심 경로를 다루면 묻는다
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
PATH_TOKEN = re.compile(r"[^\s\"'(),;=]+")
WHERE = f"규칙 변경은 격리 워크트리에서 하고, 기준 브랜치에 올릴 때 {OWNER} 확인을 거친다."


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
        )

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
    if head in INLINE_CMDS:
        text = " ".join(rest)
        if mentions(text) and re.search(r"open\(|write|unlink|rename|replace", text):
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
        if mentions(body) and re.search(r"open\(|write|unlink|rename|replace|>", body) \
                and re.search(r"(?:^|[\s;&|(])(?:python[0-9.]*|perl|ruby|node|sh|bash|zsh)\b", header):
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
