#!/usr/bin/env python3
"""고친 뒤 할 일 알림 (PostToolUse: Edit · Write · MultiEdit · NotebookEdit · apply_patch).

막지 않고 알리기만 한다. 같은 세션에서 같은 종류는 한 번만 말한다(매번 말하면 읽히지 않는다).

| 고친 것 | 알리는 것 |
| --- | --- |
| `harness.json` | 생성기를 돌려야 생성물에 반영된다 |
| 훅 · 플러그인 · 엔진 설정(`harness/hooks/` · `plugins/` · `.claude/settings.json` · `.codex/hooks.json`) | 점검(doctor)으로 훅이 도는지 본다 |

Bash 로 고친 것(sed -i 등)은 보지 않는다. 어떤 경우에도 0 으로 끝난다.
"""
import json
import os
import re
import subprocess
import sys

PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
KINDS = (
    ("config", lambda r: r == "harness.json"),
    ("hooks", lambda r: r.startswith(("harness/hooks/", "plugins/")) or r in (".claude/settings.json", ".codex/hooks.json")),
)


def root_of(data):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CODEX_PROJECT_ROOT")
                            or data.get("cwd") or os.getcwd())


def python_cmd(root):
    try:
        with open(os.path.join(root, "harness.json"), encoding="utf-8") as f:
            return (json.load(f).get("platform") or {}).get("python") or "python3"
    except (OSError, ValueError, AttributeError):
        return "python3"


def message(kind, py):
    if kind == "config":
        return (f"harness.json 을 고쳤다. 생성기를 돌려야 생성물(CLAUDE.md · 플러그인 · 훅 설정)에 반영된다: "
                f"`{py} harness/scripts/generate.py`. 그다음 `{py} harness/scripts/bootstrap.py doctor`.")
    return f"훅 · 플러그인 설정을 고쳤다. 끝내기 전에 `{py} harness/scripts/bootstrap.py doctor` 로 훅이 도는지 본다."


def targets(data):
    tin = data.get("tool_input") or {}
    tool = data.get("tool_name", "")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tin.get("file_path") or tin.get("notebook_path") or ""
        return [path] if path else []
    if tool == "apply_patch":
        patch = tin.get("command") or tin.get("patch") or tin.get("input") or ""
        if isinstance(patch, list):
            patch = "\n".join(map(str, patch))
        return [f.strip() for f in PATCH_FILE.findall(patch)]
    return []


def git_dir(root):
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-dir"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def once(root, kind, session):
    """이 세션에서 이 종류를 처음 말하면 True. 표시는 git 폴더 안에 둔다(커밋에 섞이지 않는다)."""
    gd = git_dir(root)
    if not gd:
        return True
    mark = os.path.join(gd, f"harness-remind-{kind}")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read().strip() == session:
                return False
    except OSError:
        pass
    try:
        with open(mark, "w", encoding="utf-8") as f:
            f.write(session)
    except OSError:
        pass
    return True


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    root = root_of(data)
    cwd = data.get("cwd") or root
    kinds = []
    for path in targets(data):
        rel = os.path.relpath(os.path.realpath(os.path.join(cwd, os.path.expanduser(path))), root).replace(os.sep, "/")
        for kind, hit in KINDS:
            if not rel.startswith("..") and hit(rel) and kind not in kinds:
                kinds.append(kind)
    session = str(data.get("session_id") or data.get("turn_id") or "")
    lines = [message(k, python_cmd(root)) for k in kinds if once(root, k, session)]
    if not lines:
        return
    text = "[하네스] " + " ".join(lines)
    if data.get("turn_id"):  # Codex: 추가 문맥 대신 시스템 메시지
        print(json.dumps({"systemMessage": text}, ensure_ascii=False))
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}},
                         ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001  알림 훅은 턴을 막지 않는다
        pass
    sys.exit(0)
