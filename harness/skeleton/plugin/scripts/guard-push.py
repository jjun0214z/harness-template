#!/usr/bin/env python3
"""git push 가드 (PreToolUse, Bash). 설정은 같은 플러그인의 config.json(생성기가 harness.json 에서 만든다).

- 강제 push(--force · -f · --force-with-lease · +refspec): 막는다.
- config.json 의 ask 에 적힌 (저장소, 브랜치) 로 가는 push: 결정권자에게 묻는다.
- 하네스 저장소 기준 브랜치로 가는 push 에 규칙 파일 변경이 들어 있으면: 바뀐 파일 목록을 들고 묻는다.
- 그 밖의 push 는 평소 권한 흐름을 따른다.
- Codex 는 ask 를 지원하지 않아 승인 전에는 막고, 승인 환경변수를 붙인 재실행만 통과시킨다.
- 명령은 shellwords 로 읽는다(`cd X &&` · `sh -c` · `$(…)` · heredoc). push 하는 저장소를 알 수 없거나
  (변수 · `cd -` · 글을 코드로 돌리는 명령) 판정 중에 깨지면 통과시키지 않고 묻는다.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellwords  # noqa: E402  같은 폴더


def plugin_root():
    return (os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_config():
    try:
        with open(os.path.join(plugin_root(), "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CFG = load_config()
OWNER = CFG.get("owner_title") or "결정권자"
APPROVE = CFG.get("approve_env") or "HARNESS_APPROVED_PUSH"
PROTECTED = tuple(CFG.get("protected") or ("CLAUDE.md", "AGENTS.md", ".claude/", ".codex/", "plugins/"))


def decide(decision, reason, data, approved=False):
    # Codex PreToolUse 는 permissionDecision=ask 를 지원하지 않는다(turn_id 로 구분).
    if decision == "ask" and data.get("turn_id"):
        if approved:
            sys.exit(0)
        decision = "deny"
        reason += f" {OWNER} 승인 뒤 명령 앞에 {APPROVE}=1 을 붙여 다시 실행한다."
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def git_out(cwd, *args):
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def repo_ids(url):
    """원격 URL 에서 ('조직/이름', '이름'). https · ssh · 로컬 경로 모두."""
    u = url.strip().rstrip("/").replace("\\", "/")
    if u.endswith(".git"):
        u = u[:-4]
    u = re.sub(r"^[^@/]+@([^:/]+):", r"\1/", u)
    parts = [p for p in u.split("/") if p]
    name = parts[-1] if parts else ""
    full = "/".join(parts[-2:]) if len(parts) >= 2 else name
    return full, name


def find_rule(url):
    """config 의 repos 중 이 원격에 맞는 것. 조직/이름이 먼저, 이름만은 그다음."""
    full, name = repo_ids(url)
    repos = CFG.get("repos") or {}
    if full in repos:
        return repos[full]
    for key, rule in repos.items():
        if key.split("/")[-1] == name:
            return rule
    return None


def is_rule_file(path):
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in PROTECTED)


def changed_rule_files(cwd, src, base):
    sha = git_out(cwd, "rev-parse", "--verify", "--quiet", f"{src}^{{commit}}")
    if not sha:
        return None
    try:
        out = subprocess.run(["git", "-C", cwd, "diff", "--name-only", f"origin/{base}...{sha}"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [f for f in out.stdout.splitlines() if is_rule_file(f)]


PUSH_VALUE_OPTS = {"--repo", "--receive-pack", "--exec", "--push-option", "-o", "--signed"}


def classify(rest):
    """push 뒤 인자 → (강제인가, 원격 이름, [(보낼 것, 받을 브랜치)])."""
    named, i, force = [], 0, False
    while i < len(rest):
        t = rest[i]
        if t == "--":
            named += rest[i + 1:]
            break
        if t.startswith("--"):
            opt, eq, _ = t.partition("=")
            if opt.startswith("--force") or opt in ("--mirror", "--prune"):
                force = True
            if opt in PUSH_VALUE_OPTS and not eq:
                i += 1
        elif t.startswith("-") and len(t) > 1:
            letters = t[1:]
            for j, ch in enumerate(letters):
                if ch == "f":
                    force = True
                if ch == "o":
                    if j == len(letters) - 1:
                        i += 1
                    break
        else:
            named.append(t)
        i += 1
    remote, refs = (named[0] if named else None), named[1:]
    if any(r.startswith("+") for r in refs):
        force = True
    pairs = [(r.split(":")[0], r.split(":")[-1].replace("refs/heads/", "")) for r in refs]
    return force, remote, pairs


def judge(command, base_cwd, data, approved):
    unknown = (f"push 하는 저장소를 알 수 없다(변수 · cd - · alias · 글을 코드로 돌리는 명령). "
               f"경로를 그대로 적어 다시 실행하거나 {OWNER} 확인을 받는다.")
    for wheres, args, _ in shellwords.walk(command, base_cwd):
        if shellwords.reads_stdin_as_code(args):
            decide("ask", unknown, data, approved)
        if args and os.path.basename(args[0]) in ("git", "git.exe") and any("alias." in a for a in args):
            decide("ask", unknown, data, approved)
        for where in wheres:
            hit = shellwords.git(where or base_cwd, args)
            if not hit:
                continue
            cwd, sub, rest = hit
            if sub == "subtree" and "push" in rest:
                rest = rest[rest.index("push") + 1:]
            elif sub != "push":
                continue
            if where is None or cwd is None:
                decide("ask", unknown, data, approved)
            configs = [args[k + 1] for k in range(len(args) - 1) if args[k] == "-c"]
            force, remote, pairs = classify(rest)
            if force or any(re.search(r"mirror|\.push=\+", c) for c in configs):
                decide("deny", f"강제 push 는 하네스가 막는다. 필요하면 {OWNER}이 직접 실행한다.", data)
            if not pairs:
                pairs = [("HEAD", git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD"))]
            url = git_out(cwd, "remote", "get-url", remote or "origin")
            if not url:
                continue
            check_rule(url, cwd, pairs, data, approved)


def check_rule(url, cwd, pairs, data, approved):
    full, _ = repo_ids(url)
    rule = find_rule(url)
    if not rule:
        return
    for _, dst in pairs:
        meaning = (rule.get("ask") or {}).get(dst)
        if meaning is not None:
            what = f"{meaning}({full} {dst})" if meaning else f"{full} {dst}"
            decide("ask", f"{what}으로 가는 push 다. {OWNER} 확인이 필요하다.", data, approved)
    if rule.get("harness"):
        base = rule.get("base") or "main"
        for src, dst in pairs:
            if dst != base or not src:
                continue
            files = changed_rule_files(cwd, src, base)
            if files is None:
                decide("ask", f"하네스 {base} 로 가는 push 인데 바뀐 규칙 파일을 잴 수 없다(origin/{base} 비교 실패). {OWNER} 확인이 필요하다.", data, approved)
            if files:
                decide("ask", f"하네스 {base} 로 가는 push 에 규칙 변경이 들어 있다: {' · '.join(files)}. 규칙 변경은 {OWNER} 확인을 거친다.", data, approved)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    tin = data.get("tool_input") or {}
    command = tin.get("command") or tin.get("cmd") or ""
    if isinstance(command, list):
        command = " ".join(map(str, command))
    if not isinstance(command, str) or "push" not in re.sub(r"['\"\\]", "", command):  # pu''sh 처럼 끊어도 본다
        sys.exit(0)
    approved = bool(re.search(r"(?:^|\s)" + re.escape(APPROVE) + r"=1(?:\s|$)", command))
    try:
        judge(command, data.get("cwd") or os.getcwd(), data, approved)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  지키는 훅은 깨지면 닫힌다
        decide("ask", f"push 가드가 판정하지 못했다({type(exc).__name__}: {exc}). 열어 두지 않고 묻는다.", data, approved)
    sys.exit(0)


if __name__ == "__main__":
    main()
