#!/usr/bin/env python3
"""끝난 Orca Codex 작업자 세션을 Codex 보관함으로 옮긴다 (SessionEnd, Orca 를 고른 하네스에만 들어간다).

Orca 작업 폴더(config.json 의 orca_workspaces, 기본 ~/orca/workspaces) 아래 세션만 다룬다.
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


def workspaces_root():
    try:
        with open(os.path.join(plugin_root(), "config.json"), encoding="utf-8") as f:
            value = json.load(f).get("orca_workspaces") or "~/orca/workspaces"
    except (OSError, ValueError):
        value = "~/orca/workspaces"
    return os.path.realpath(os.path.expanduser(value)) + os.sep


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
    if not cwd.startswith(workspaces_root()):
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
