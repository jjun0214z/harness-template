#!/usr/bin/env python3
"""git push 가드 (PreToolUse, Bash). 설정은 같은 플러그인의 config.json(생성기가 harness.json 에서 만든다).

- 강제 push(--force · -f · --force-with-lease · +refspec): 막는다.
- config.json 의 ask 에 적힌 (저장소, 브랜치) 로 가는 push: 결정권자에게 묻는다.
- 하네스 저장소 기준 브랜치로 가는 push 에 규칙 파일 변경이 들어 있으면: 바뀐 파일 목록을 들고 묻는다.
- 그 밖의 push 는 평소 권한 흐름을 따른다.
- Codex 는 ask 를 지원하지 않아 승인 전에는 막고, 승인 환경변수를 붙인 재실행만 통과시킨다.
"""
import json
import os
import re
import shlex
import subprocess
import sys


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


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    tin = data.get("tool_input") or {}
    command = tin.get("command") or tin.get("cmd") or ""
    if isinstance(command, list):
        command = " ".join(map(str, command))
    approved = bool(re.search(r"(?:^|\s)" + re.escape(APPROVE) + r"=1(?:\s|$)", command))
    if "push" not in command or "git" not in command:
        sys.exit(0)

    base_cwd = data.get("cwd") or os.getcwd()
    for part in re.split(r"&&|\|\||;|\|", command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            continue
        tokens = [t for t in tokens if not re.match(r"^[A-Z_][A-Z0-9_]*=", t)] if tokens else tokens
        if len(tokens) >= 2 and tokens[0] == "cd":
            base_cwd = os.path.normpath(os.path.join(base_cwd, os.path.expanduser(tokens[1])))
            continue
        if not tokens or os.path.basename(tokens[0]) not in ("git", "git.exe") or "push" not in tokens:
            continue
        cwd = base_cwd
        if "-C" in tokens:
            i = tokens.index("-C")
            if i + 1 < len(tokens):
                cwd = os.path.normpath(os.path.join(base_cwd, tokens[i + 1]))
        args = tokens[tokens.index("push") + 1:]
        if any(a in ("--force", "-f", "--force-with-lease", "--force-if-includes") or a.startswith("--force-with-lease=")
               or (a.startswith("+") and len(a) > 1) for a in args):
            decide("deny", f"강제 push 는 하네스가 막는다. 필요하면 {OWNER}이 직접 실행한다.", data)

        positional = [a for a in args if not a.startswith("-")]
        refspecs = positional[1:]
        if refspecs:
            pairs = [(r.split(":")[0], r.split(":")[-1]) for r in refspecs]
        else:
            pairs = [("HEAD", git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD"))]
        pairs = [(src, dst.replace("refs/heads/", "")) for src, dst in pairs]
        url = git_out(cwd, "remote", "get-url", positional[0] if positional else "origin")
        if not url:
            continue
        full, _ = repo_ids(url)
        rule = find_rule(url)
        if rule:
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
    sys.exit(0)


if __name__ == "__main__":
    main()
