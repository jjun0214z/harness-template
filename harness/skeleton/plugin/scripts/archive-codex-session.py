#!/usr/bin/env python3
"""끝난 Orca Codex 작업자 세션을 Codex 보관함으로 옮긴다 (SessionEnd, Orca 를 고른 하네스에만 들어간다).

Orca 작업 폴더(config.json 의 orca_workspaces, 기본 ~/orca/workspaces) 아래 세션과,
하네스 워크트리 폴더(config.json 의 works_dir, 안쪽 배치면 `.works`) 아래의 링크된 워크트리 세션만 다룬다.
`.works/` 쪽은 Orca 터미널에서 뜬 세션만 저절로 보관한다(다른 터미널에서 사람이 직접 연 세션은 두고, 정리 스크립트가 그 워크트리를 지울 때 함께 보관한다).
codex 실행 파일은 PATH · ~/.local/bin · macOS 앱 번들 순서로 찾고, 없으면 조용히 끝낸다.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def plugin_root():
    return (os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_config():
    try:
        with open(os.path.join(plugin_root(), "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def workspaces_root():
    value = load_config().get("orca_workspaces") or "~/orca/workspaces"
    return os.path.realpath(os.path.expanduser(value)) + os.sep


def in_workspace(cwd, explicit):
    """작업자 워크트리인가: Orca 작업 폴더 아래이거나, 하네스 워크트리 폴더(config.json works_dir, 예: .works) 아래의 링크된 git 워크트리.

    `.works/` 는 사람이 직접 세션을 여는 자리이기도 하다. 그래서 세션이 끝날 때 저절로 도는 경우(explicit 아님)에는
    **Orca 터미널에서 뜬 세션**(Orca 가 넣는 ORCA_TERMINAL_HANDLE 이 있다)만 보관한다. 다른 터미널에서 직접 연 세션은 건드리지 않는다.
    정리 스크립트가 워크트리를 지우며 경로를 주고 부른 경우(explicit)는 그 워크트리의 세션을 모두 보관한다(지워지는 자리다)."""
    if cwd.startswith(workspaces_root()):
        return True
    works = load_config().get("works_dir")
    if not works or works not in cwd.split(os.sep)[:-1]:
        return False
    if not explicit and not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return False
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    lines = r.stdout.split("\n")
    if r.returncode != 0 or len(lines) < 2:
        # 워크트리가 이미 지워졌다(정리 스크립트가 지운 뒤 다시 보관): 폴더가 없을 때만 이름으로 본다
        return not os.path.isdir(cwd)
    return os.path.realpath(lines[0]) != os.path.realpath(lines[1])


def find_codex():
    for cand in (shutil.which("codex"), str(Path.home() / ".local" / "bin" / "codex"),
                 "/Applications/ChatGPT.app/Contents/Resources/codex"):
        if cand and os.path.isfile(cand):
            return cand
    return None


def session_ids_for_cwd(cwd):
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
    if not root.is_dir():
        return []
    result = []
    for path in root.rglob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as handle:
                first = json.loads(handle.readline())
        except (OSError, ValueError):
            continue
        payload = first.get("payload", {})
        if payload.get("cwd") and os.path.realpath(payload["cwd"]) == cwd:
            sid = payload.get("session_id") or payload.get("id")
            if sid:
                result.append(sid)
    return result


def main():
    if os.environ.get("HARNESS_ARCHIVE_RUNNING") == "1":
        return
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        data = {}
    explicit = len(sys.argv) > 1
    sid = None if explicit else (os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
                                 or data.get("session_id") or data.get("thread_id"))
    cwd = os.path.realpath(sys.argv[1] if explicit else data.get("cwd") or os.getcwd())
    if not in_workspace(cwd, explicit):
        return
    ids = [sid] if sid else session_ids_for_cwd(cwd)
    codex = find_codex()
    if not ids or not codex:
        return
    env = os.environ.copy()
    env.pop("CODEX_SESSION_ID", None)
    env.pop("CODEX_THREAD_ID", None)
    env["HARNESS_ARCHIVE_RUNNING"] = "1"
    failed = 0
    for found in ids:
        if explicit:
            failed += subprocess.run([codex, "archive", found], check=False, env=env).returncode != 0
        else:
            # 세션이 끝난 뒤에 보관해야 하므로 1초 뒤 따로 돈다. 세션 종료를 막지 않는다.
            code = f"import time,subprocess;time.sleep(1);subprocess.run([{codex!r},'archive',{found!r}])"
            kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
            subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env, **kwargs)
    if failed:
        print(f"codex archive 실패 {failed}건", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
