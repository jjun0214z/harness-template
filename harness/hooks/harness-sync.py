#!/usr/bin/env python3
"""하네스 저장소 동기화 훅.

SessionStart: 원격에서 fast-forward 로만 받아온다. 다른 기기에서 고친 하네스를 이어받기 위해서다.
  추적 브랜치가 없으면(새 워크트리 · 아직 push 전) 조용히 건너뛴다.
Stop: 공유 체크아웃에 커밋 안 된 변경이나 push 안 된 커밋이 남아 있으면 한 번 막고 커밋·push 를 요구한다.
  격리 워크트리(작업자 자리)는 push 하지 않는 것이 규칙이라 커밋 안 된 변경만 막는다.
  자동 커밋은 하지 않는다. 여러 세션이 같은 저장소를 고칠 수 있어 엉뚱한 변경이 섞여 올라간다.
"""
import json
import os
import subprocess
import sys


def git(root, *args, timeout=20):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def upstream(root):
    code, up, _ = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return up if code == 0 and up else ""


def linked_worktree(root):
    _, gd, _ = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    _, common, _ = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(gd and common and os.path.realpath(gd) != os.path.realpath(common))


def session_start(root):
    if not upstream(root):
        sys.exit(0)
    code, _, err = git(root, "pull", "--ff-only", "--quiet")
    if code != 0:
        print(f"[하네스 동기화] 원격 하네스를 받지 못했다 (fast-forward 불가 또는 네트워크): {err[:200]}. "
              "작업 전에 `git status` 와 `git log --oneline @{u}...HEAD` 를 확인한다.")
    sys.exit(0)


def stop(root, data):
    if data.get("stop_hook_active"):
        sys.exit(0)
    _, dirty, _ = git(root, "status", "--porcelain")
    ahead = ""
    up = upstream(root)
    if up and not linked_worktree(root):
        _, ahead, _ = git(root, "log", "--oneline", f"{up}..HEAD")
    if not dirty and not ahead:
        sys.exit(0)
    parts = []
    if dirty:
        parts.append("커밋 안 된 변경:\n" + dirty)
    if ahead:
        parts.append("push 안 된 커밋:\n" + ahead)
    reason = ("하네스 변경이 원격에 올라가지 않았다. 다른 기기 · 세션이 이어받지 못한다.\n" + "\n".join(parts)
              + "\n내 변경이면 경로를 명시해 커밋하고(공유 체크아웃이면 push 까지) 한다. "
              "작업자가 아직 쓰는 중이거나 내 변경이 아니면 커밋하지 말고 그 사실을 보고한다.")
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    root = (os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CODEX_PROJECT_ROOT")
            or data.get("cwd") or os.getcwd())
    event = data.get("hook_event_name", "")
    if event == "SessionStart":
        session_start(root)
    elif event == "Stop":
        stop(root, data)
    sys.exit(0)


if __name__ == "__main__":
    main()
