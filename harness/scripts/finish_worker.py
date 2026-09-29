#!/usr/bin/env python3
"""끝난 작업자 하나를 정리한다: 안전 조건을 전부 확인한 뒤에만 에이전트를 끝내고 워크트리를 지운다.

불변식: 「오프라인 = 끝난 작업」. 작업 중이거나 응답을 기다리는 에이전트는 절대 끝내지 않는다.

안전 조건(하나라도 어긋나거나 모르면 멈추고 이유를 적는다):
  1. 링크된 워크트리다(본 체크아웃이 아니다)
  2. 이 명령을 부른 세션 자신의 워크트리가 아니다
  3. 커밋 안 된 변경이 없다
  4. 원격(origin)에 없는 커밋이 0 개다
  5. (Orca) 그 Dispatch 가 끝난 상태다
  6. 그 워크트리에서 도는 Claude 세션(~/.claude/sessions/<pid>.json)이 모두 idle 이다. busy · 응답 대기 · 모르는 상태면 멈춘다

정리 순서: Claude 세션 정상 종료(SIGTERM, 기다림, 안 죽으면 멈추고 보고, SIGKILL 은 쓰지 않는다)
          → Codex 세션 보관(실패는 경고만) → (Orca) worker-release · worktree rm / (Orca 없음) git worktree remove
마지막 줄: 「앱 보관 대상(Claude): <작업 이름>」. Claude 원격 세션 목록의 보관은 스크립트로 못 하므로 deploy 스킬의 「앱 세션 정리」가 한다.

사용: python3 harness/scripts/finish_worker.py <워크트리> [--dispatch <id>] [--name <작업 이름>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402

HARNESS_ROOT = HERE.parents[1]
SETTLED = {"completed", "succeeded", "failed", "done", "settled", "cancelled", "canceled", "stopped", "released"}


class Stop(Exception):
    """안전 조건이 어긋나 멈춘다."""


def git(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def real(p) -> str:
    return os.path.realpath(str(p))


def inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if os.name != "nt":
        # 끝났지만 부모가 아직 거두지 않은 좀비는 끝난 것으로 본다
        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().startswith("Z"):
            return False
    return True


def sessions_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "sessions"


def read_session(f: Path) -> dict:
    """세션 파일 하나. 읽거나 해석하지 못하면 멈춘다(건너뛰면 작업 중 세션을 놓칠 수 있다)."""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"세션 상태를 읽지 못했다: {f.name} ({exc.__class__.__name__}). 쓰는 중일 수 있어 끝내지 않는다. 잠시 뒤 다시 부른다")
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int) or isinstance(data.get("pid"), bool) or not data.get("cwd"):
        raise Stop(f"세션 상태를 읽지 못했다: {f.name} (pid 가 정수가 아니거나 cwd 가 없다)")
    data["_file"] = str(f)
    return data


def claude_sessions(worktree: str) -> List[dict]:
    """그 워크트리(하위 포함)를 cwd 로 쓰는 살아 있는 Claude 세션. 세션 파일 하나라도 못 읽으면 멈춘다."""
    root = sessions_dir()
    found = []
    for f in sorted(root.glob("*.json")) if root.is_dir() else []:
        data = read_session(f)
        if inside(real(data["cwd"]), worktree) and pid_alive(data["pid"]):
            found.append(data)
    return found


def process_command(pid: int) -> Optional[str]:
    r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def looks_like_claude(command: str) -> bool:
    """실행 파일(또는 스크립트) 이름이 claude 인가. node 로 도는 경우 @anthropic-ai/claude-code 경로도 본다."""
    return bool(re.search(r"(^|[\s/])claude(\s|$)|@anthropic-ai/claude-code", command))


def lsof_path() -> Optional[str]:
    """lsof 는 시스템 도구라 PATH 에 없을 때(/usr/sbin 이 빠진 셸) 기본 자리도 본다."""
    return shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof", "/sbin/lsof") if os.path.isfile(p)), None)


def process_cwd(pid: int) -> Optional[str]:
    """프로세스의 실제 cwd. Linux 는 /proc, 그 밖은 lsof. 못 재면 None."""
    proc = Path(f"/proc/{pid}/cwd")
    if proc.exists():
        try:
            return real(os.readlink(proc))
        except OSError:
            return None
    lsof = lsof_path()
    if not lsof:
        return None
    r = subprocess.run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("n"):
            return real(line[1:])
    return None


def processes_in(worktree: str) -> List[int]:
    """cwd 가 워크트리 안인 모든 프로세스 pid. 목록을 못 재면 멈춘다."""
    if os.name == "nt":
        raise Stop("Windows 에서는 워크트리를 쓰는 프로세스 목록을 잴 수 없다. 작업자를 직접 닫고 git worktree remove 로 지운다")
    pids = []
    if Path("/proc/self/cwd").exists():
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    if inside(real(os.readlink(d / "cwd")), worktree):
                        pids.append(int(d.name))
                except OSError:
                    continue
        return pids
    lsof = lsof_path()
    if not lsof:
        raise Stop("lsof 가 없어 워크트리를 쓰는 프로세스를 잴 수 없다. 지우지 않는다")
    r = subprocess.run([lsof, "-d", "cwd", "-Fpn"], capture_output=True, text=True)
    if not r.stdout:
        raise Stop("lsof 로 프로세스 목록을 읽지 못했다. 지우지 않는다")
    pid = None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None and inside(real(line[1:]), worktree):
            pids.append(pid)
    return sorted({p for p in pids if p != os.getpid() and pid_alive(p)})


def check(worktree: Path, dispatch: Optional[str], orca: Optional[str], cfg: Optional[dict]) -> List[dict]:
    """안전 조건 1~6. 통과하면 끝낼 Claude 세션 목록을 돌려준다."""
    wt = real(worktree)
    if not worktree.is_dir():
        raise Stop(f"워크트리가 없다: {worktree}")
    gd, common = git(wt, "rev-parse", "--path-format=absolute", "--git-dir"), git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if gd.returncode or common.returncode:
        raise Stop(f"git 워크트리가 아니다: {wt}")
    if real(gd.stdout.strip()) == real(common.stdout.strip()):
        raise Stop(f"본 체크아웃이다(링크된 워크트리가 아니다). 지우지 않는다: {wt}")
    callers = [os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR", ""), os.environ.get("PWD", "")]
    for c in filter(None, callers):
        if inside(real(c), wt):
            raise Stop(f"이 명령을 부른 세션 자신의 워크트리다. 다른 자리에서 부른다: {wt}")
    status = git(wt, "status", "--porcelain")
    if status.returncode or status.stdout.strip():
        raise Stop(f"커밋 안 된 변경이 있다(또는 상태를 못 읽음): {wt}")
    if git(wt, "remote", "get-url", "origin").returncode == 0:
        git(wt, "fetch", "--quiet", "origin")
    unpushed = git(wt, "rev-list", "HEAD", "--not", "--remotes=origin")
    if unpushed.returncode:
        raise Stop("원격에 없는 커밋 수를 잴 수 없다")
    n = len(unpushed.stdout.split())
    if n:
        raise Stop(f"원격에 없는 커밋 {n}개가 있다")
    if dispatch:
        if not orca:
            raise Stop("--dispatch 를 줬는데 orca CLI 를 못 찾는다")
        ws = real(os.path.expanduser((cfg or {}).get("orca", {}).get("workspaces_dir", "~/orca/workspaces")))
        if not inside(wt, ws):
            raise Stop(f"Orca 작업 폴더({ws}) 밖이다: {wt}")
        r = subprocess.run([orca, "orchestration", "worker-show", "--dispatch", dispatch, "--json"], capture_output=True, text=True)
        try:
            d = (json.loads(r.stdout).get("result") or {}).get("dispatch") or {}
            state = str(d.get("status") or "").lower()
        except ValueError:
            state = ""
        if state not in SETTLED:
            raise Stop(f"작업자 Dispatch 가 끝난 상태가 아니다({state or '모름'}). worker_done 을 확인한 뒤 다시 부른다")
    sessions = claude_sessions(wt)
    for s in sessions:
        st = s.get("status")
        if st != "idle":
            raise Stop(f"Claude 세션 {s.get('name') or s.get('pid')} 가 쉬는 상태가 아니다(status={st or '모름'}). 작업 중이거나 응답 대기면 끝내지 않는다")
    others = [p for p in processes_in(wt) if p not in {s["pid"] for s in sessions}]
    if others:
        raise Stop(f"세션 파일 없이 이 워크트리를 쓰는 프로세스가 있다(pid {', '.join(map(str, others))}). 끝내지도 지우지도 않는다")
    return sessions


def verify_before_signal(s: dict, worktree: str) -> None:
    """신호 직전: 그 pid 가 정말 claude 이고, 실제 cwd 가 워크트리 안이고, 상태가 아직 idle 인가. 못 재면 멈춘다."""
    pid = s["pid"]
    command = process_command(pid)
    if not command or not looks_like_claude(command):
        raise Stop(f"pid {pid} 가 claude 프로세스가 아니다(재사용된 pid 일 수 있다: {command or '못 잼'}). 끝내지 않는다")
    cwd = process_cwd(pid)
    if not cwd or not inside(cwd, worktree):
        raise Stop(f"pid {pid} 의 실제 작업 폴더가 워크트리 안이 아니다({cwd or '못 잼'}). 끝내지 않는다")
    now = read_session(Path(s["_file"]))
    if now.get("pid") != pid or now.get("status") != "idle":
        raise Stop(f"Claude 세션 {s.get('name') or pid} 상태가 방금 바뀌었다(status={now.get('status') or '모름'}). 끝내지 않는다")


def terminate(sessions: List[dict], wait: float, worktree: str = "") -> None:
    if not sessions:
        return
    if os.name == "nt":
        raise Stop("Windows 는 정상 종료 신호가 없다. 그 Claude 세션을 직접 닫은 뒤 다시 부른다")
    for s in sessions:
        verify_before_signal(s, worktree)
    for s in sessions:
        try:
            os.kill(s["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + wait
    while time.time() < deadline and any(pid_alive(s["pid"]) for s in sessions):
        time.sleep(0.2)
    alive = [s for s in sessions if pid_alive(s["pid"])]
    if alive:
        names = ", ".join(str(s.get("name") or s["pid"]) for s in alive)
        raise Stop(f"Claude 세션이 {wait:g}초 안에 끝나지 않았다({names}). 강제로 끝내지 않는다. 그 세션을 직접 닫은 뒤 다시 부른다")


def archive_codex(worktree: str, cfg: Optional[dict]) -> Optional[str]:
    """Codex 세션 보관. 실패하면 경고 문장을 돌려준다(정리는 계속)."""
    if not cfg or "codex" not in cfg.get("engines", []):
        return None
    script = HARNESS_ROOT / "plugins" / cfg["project"]["slug"] / "scripts" / "archive-codex-session.py"
    if not script.is_file():
        return None
    r = subprocess.run([sys.executable, str(script), worktree], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        return f"경고: Codex 세션 보관 실패(정리는 계속한다): {(r.stderr or r.stdout).strip()[-200:]}"
    return None


def delete_branch(repo: str, branch: str) -> None:
    """-d 먼저. 병합 안 됨으로 실패하면 그 브랜치 커밋이 원격에 다 있을 때만 -D, 아니면 남기고 알린다."""
    if git(repo, "branch", "-d", branch).returncode == 0:
        return
    left = git(repo, "rev-list", branch, "--not", "--remotes=origin")
    if left.returncode == 0 and not left.stdout.strip():
        git(repo, "branch", "-D", branch)
    else:
        print(f"경고: 브랜치 {branch} 에 원격에 없는 커밋이 있어 남긴다", file=sys.stderr)


class CleanupLock:
    """동시 정리를 막는 잠금 파일: <프로젝트 폴더>/.work/cleanup.lock (pid · 시작 시각). 주인이 죽은 잠금은 넘겨받는다."""

    def __init__(self):
        parent = hl.git_common_parent(HARNESS_ROOT) or HARNESS_ROOT.parent
        self.path = Path(os.environ.get("HARNESS_CLEANUP_LOCK") or (parent / ".work" / "cleanup.lock"))

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    owner = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise Stop(f"정리 잠금 파일을 읽지 못했다: {self.path}. 다른 정리가 끝났는지 확인하고 지운다")
                if isinstance(owner.get("pid"), int) and pid_alive(owner["pid"]):
                    raise Stop(f"다른 정리가 진행 중이다(pid {owner['pid']}, {owner.get('started')}). 끝난 뒤 다시 부른다")
                self.path.unlink()
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
            return self
        raise Stop(f"정리 잠금을 잡지 못했다: {self.path}")

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="끝난 작업자 정리")
    ap.add_argument("worktree", type=Path)
    ap.add_argument("--dispatch", help="Orca Dispatch ID (Orca 작업자일 때)")
    ap.add_argument("--name", help="작업 이름(보고용, 기본: Claude 세션 이름 또는 폴더 이름)")
    ap.add_argument("--dry-run", action="store_true", help="확인만 하고 아무것도 끝내거나 지우지 않는다")
    ap.add_argument("--wait", type=float, default=float(os.environ.get("HARNESS_TERM_WAIT", "10")))
    args = ap.parse_args(argv)
    try:
        cfg = hl.load_config(HARNESS_ROOT / hl.CONFIG_NAME)
    except hl.ConfigError:
        cfg = None
    orca = hl.find_tool("orca", hl.os_kind(), hl.tool_extra_paths("orca", hl.os_kind())) if args.dispatch else None
    wt = real(args.worktree)
    try:
        sessions = check(args.worktree, args.dispatch, orca, cfg)
        names = [str(s.get("name")) for s in sessions if s.get("name")]
        name = args.name or (", ".join(names) if names else os.path.basename(wt))
        if args.dry_run:
            print(f"[미리보기] 안전 조건 통과: {wt}")
            print(f"  끝낼 Claude 세션: {', '.join(str(s['pid']) for s in sessions) or '없음'} · 지울 워크트리: {wt}")
            print(f"앱 보관 대상(Claude): {name}")
            return 0
        with CleanupLock():
            terminate(sessions, args.wait, wt)
            left = processes_in(wt)
            if left:
                raise Stop(f"세션을 끝낸 뒤에도 이 워크트리를 쓰는 프로세스가 남았다(pid {', '.join(map(str, left))}). 지우지 않는다")
            return remove(args, wt, cfg, orca, name)
    except Stop as exc:
        print(f"멈춤: {exc}", file=sys.stderr)
        return 1


def remove(args, wt: str, cfg: Optional[dict], orca: Optional[str], name: str) -> int:
    warn = archive_codex(wt, cfg)
    if warn:
        print(warn, file=sys.stderr)
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    main_repo = str(Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).parent)
    if args.dispatch and orca:
        subprocess.run([orca, "orchestration", "worker-release", "--dispatch", args.dispatch, "--json"], capture_output=True)
        r = subprocess.run([orca, "worktree", "rm", "--worktree", f"path:{wt}", "--run-hooks", "--json"], capture_output=True, text=True)
    else:
        r = git(main_repo, "worktree", "remove", wt)
        if r.returncode == 0 and branch and branch != "HEAD":
            delete_branch(main_repo, branch)
    if r.returncode != 0:
        print(f"멈춤: 워크트리 지우기 실패: {(r.stderr or r.stdout).strip()[-200:]}", file=sys.stderr)
        return 1
    print(f"정리 완료: {wt}")
    print(f"앱 보관 대상(Claude): {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
