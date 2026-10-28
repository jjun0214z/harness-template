#!/usr/bin/env python3
"""하네스 저장소 동기화 훅.

SessionStart: 원격에서 fast-forward 로만 받아온다. 다른 기기에서 고친 하네스를 이어받기 위해서다.
  추적 브랜치가 없으면(새 워크트리 · 아직 push 전) 조용히 건너뛴다.
  받은 커밋이 규칙(harness.json rules.protected)을 건드렸으면 **바뀐 파일 이름을 대고** 알린다.
  「문서가 바뀌었을 수 있다」로 뭉뚱그리면 세션이 옛 절차를 그대로 쓴다.
  workflow 묶음을 켰으면 열린 작업과 누구 차례를 한 줄로 알린다(work.py --brief, 원격이 없어도).
Stop: 공유 체크아웃에 커밋 안 된 변경이나 push 안 된 커밋이 남아 있으면 막고 커밋·push 를 요구한다.
  격리 워크트리(작업자 자리)는 push 하지 않는 것이 규칙이라 커밋 안 된 변경만 막는다.
  **같은 세션에서 같은 상태로는 한 번만 막는다.** Stop 은 턴마다 돌아서, 매번 막으면 읽히지 않고 넘겨진다.
  상태(변경 · 커밋 목록)가 바뀌면 다시 막는다. 지문은 git 폴더 안에 두어 커밋에 섞이지 않는다.
  자동 커밋은 하지 않는다. 여러 세션이 같은 저장소를 고칠 수 있어 엉뚱한 변경이 섞여 올라간다.
"""
import hashlib
import json
import os
import subprocess
import sys

DEFAULT_PROTECTED = ("CLAUDE.md", "AGENTS.md", "harness.json", ".claude/", ".claude-plugin/", ".codex/", ".agents/", "plugins/", "harness/")
NOTICE = "harness-stop-notice"  # git 폴더 안의 지문 파일


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


def protected(root):
    try:
        with open(os.path.join(root, "harness.json"), encoding="utf-8") as f:
            rules = (json.load(f).get("rules") or {})
    except (OSError, ValueError, AttributeError):
        rules = {}
    return tuple(rules.get("protected") or DEFAULT_PROTECTED)


def is_rule_file(path, prot):
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prot)


def session_start(root):
    if upstream(root):
        _, before, _ = git(root, "rev-parse", "HEAD")
        code, _, err = git(root, "pull", "--ff-only", "--quiet")
        if code != 0:
            print(f"[하네스 동기화] 원격 하네스를 받지 못했다 (fast-forward 불가 또는 네트워크): {err[:200]}. "
                  "작업 전에 `git status` 와 `git log --oneline @{u}...HEAD` 를 확인한다.")
        else:
            _, after, _ = git(root, "rev-parse", "HEAD")
            if before and after and before != after:
                print(received_line(root, before, after))
    work_line(root)
    sys.exit(0)


def work_line(root):
    """workflow 묶음이면 열린 작업 한 줄. 느리거나 깨져도 세션을 막지 않는다."""
    try:
        with open(os.path.join(root, "harness.json"), encoding="utf-8") as f:
            packs = json.load(f).get("packs")
    except (OSError, ValueError, AttributeError):
        return
    script = os.path.join(root, "harness", "scripts", "work.py")
    if not isinstance(packs, list) or "workflow" not in packs or not os.path.isfile(script):
        return
    try:
        r = subprocess.run([sys.executable, script, "--root", root, "--brief"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.stdout.strip():
        print(r.stdout.strip())


def received_line(root, before, after):
    _, count, _ = git(root, "rev-list", "--count", f"{before}..{after}")
    _, names, _ = git(root, "diff", "--name-only", f"{before}..{after}")
    prot = protected(root)
    rules = [n for n in names.splitlines() if is_rule_file(n, prot)]
    line = f"[하네스 동기화] 원격에서 {count or '?'}커밋을 받았다."
    if not rules:
        return line
    shown = " · ".join(rules[:8]) + (f" 외 {len(rules) - 8}개" if len(rules) > 8 else "")
    return (line + f" 규칙이 바뀌었다: {shown}. 바뀐 규칙으로 일한다(관련 지침 · 스킬은 작업 전에 다시 읽는다. "
            f"`git diff {before[:9]}..{after[:9]} -- <파일>` 로 무엇이 바뀌었는지 본다).")


def is_bytecode_cache(line):
    """파이썬 실행 캐시는 하네스 변경이 아니다. .gitignore 에 없던 옛 하네스에서도 막지 않게 거른다."""
    # git() 이 출력 앞 공백을 잘라 첫 줄의 상태 칸 폭이 달라질 수 있어 줄 전체로 본다.
    path = line.rstrip().rstrip('"')
    return "__pycache__/" in path or path.endswith("__pycache__") or path.endswith(".pyc")


def stop(root, data):
    if data.get("stop_hook_active"):
        sys.exit(0)
    # 추적 안 된 폴더를 파일 단위로 펼쳐야 __pycache__ 만 든 폴더를 거를 수 있다
    _, dirty, _ = git(root, "status", "--porcelain", "--untracked-files=all")
    dirty = "\n".join(l for l in dirty.splitlines() if not is_bytecode_cache(l))
    ahead = ""
    up = upstream(root)
    if up and not linked_worktree(root):
        _, ahead, _ = git(root, "log", "--oneline", f"{up}..HEAD")
    notice = notice_path(root)
    if not dirty and not ahead:
        if notice and os.path.exists(notice):
            try:
                os.remove(notice)
            except OSError:
                pass
        sys.exit(0)
    parts = []
    if dirty:
        parts.append("커밋 안 된 변경:\n" + dirty)
    if ahead:
        parts.append("push 안 된 커밋:\n" + ahead)
    reason = ("하네스 변경이 원격에 올라가지 않았다. 다른 기기 · 세션이 이어받지 못한다.\n" + "\n".join(parts)
              + "\n내 변경이면 경로를 명시해 커밋하고(공유 체크아웃이면 push 까지) 한다. "
              "작업자가 아직 쓰는 중이거나 내 변경이 아니면 커밋하지 말고 그 사실을 보고한다.")
    mark = hashlib.sha1(f"{data.get('session_id', '')}\n{dirty}\n{ahead}".encode("utf-8")).hexdigest()
    if notice and read(notice) == mark:
        sys.exit(0)  # 이 세션에 같은 상태로 이미 막았다
    if notice:
        try:
            with open(notice, "w", encoding="utf-8") as f:
                f.write(mark)
        except OSError:
            pass
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def notice_path(root):
    _, gd, _ = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    return os.path.join(gd, NOTICE) if gd else None


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


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
