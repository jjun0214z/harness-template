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
          → 남은 작업자 도구 · 검수 크롬 끄기 → Codex 세션 보관(실패는 경고만)
          → (Orca) worker-release · worktree rm / (Orca 없음) git worktree remove
남은 프로세스: 세션을 끝낸 뒤에도 남은 것 중
  - 끈다: 워크트리를 작업 폴더로 쓰거나 명령줄에 워크트리가 든 알려진 도구(node · next · pnpm · adb · python 등. 개발 서버 · 기기 연결),
    이 작업 전용 검수 크롬(--user-data-dir 이 워크트리 · 그 Claude 임시 폴더 안이거나 이름에 끝난 작업 이름이 든 것)과 그 헬퍼.
    신호 직전에 시작 시각을 다시 재서 같은 프로세스일 때만 TERM, 안 꺼지면 KILL(도구에만. 에이전트에는 KILL 을 쓰지 않는다)
  - 끄지 않고 멈춘다: 에이전트(claude · codex, node 로 도는 것 포함) · 셸 · 이름 모르는 프로세스 · 프로필 없는 크롬 ·
    사용자 기본 프로필(~/Library · ~/.config) 크롬 · crashpad
  사전 확인에서도 알려진 도구는 「세션 파일 없이 워크트리를 쓰는 프로세스」로 멈추지 않는다(부모가 끝나 떨어져 나간 개발 서버 등).
워크트리를 지운 뒤 임시 찌꺼기도 치운다(cleanup_temp.py): 그 워크트리와 없어진 작업 공간의 Claude 임시 폴더, /tmp 검수 찌꺼기.
  찌꺼기는 --dry-run 이 목록 · 합계를 계획에 적고 실제 실행은 그 계획 안에서만 지운다(Windows 는 건너뛴다).
마지막 줄: 「앱 보관 대상(Claude): <작업 이름>」. Claude 원격 세션 목록의 보관은 스크립트로 못 하므로 deploy 스킬의 「앱 세션 정리」가 한다.
Codex 세션이 있으면 「앱 보관 대상(Codex): <작업 이름> · <thread id> · 「제목」 · 시작 <시각>」도 낸다.
CLI 보관(codex archive)은 데스크톱 앱 「최근」 목록에 바로 반영되지 않을 수 있어 앱에서 손으로 보관한다.

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
sys.dont_write_bytecode = True  # 형제 모듈 import 가 __pycache__ 를 하네스 저장소에 남기지 않게 한다
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402
import cleanup_temp as ct  # noqa: E402

HARNESS_ROOT = HERE.parents[1]
KILL_WAIT = float(os.environ.get("HARNESS_KILL_WAIT", "5"))
CODEX_HOME = os.environ.get("HARNESS_CODEX_HOME") or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
# 작업자가 띄워 두고 가는 도구(개발 서버 · 빌드 감시 · 검수 크롬 · 기기 연결). 이 이름만 세션을 끝낸 뒤 끈다.
TOOL_NAME = re.compile(
    r"^(node|nodejs|next-server.*|pnpm|npm|npx|yarn|bun|deno|vite|esbuild|turbo|watchman|tsc|jest|vitest|playwright.*"
    r"|adb|python3?(\.\d+)?|Python|java|ffmpeg|caffeinate|headless_shell|chrome|chromium"
    r"|Google Chrome.*|Chromium.*|Chrome for Testing.*)$",
    re.I,
)
CHROME_BIN = re.compile(r"(Google Chrome|Chromium|Chrome for Testing|headless_shell|chrome-headless-shell|/chrome)( Helper[^/]*)?(\.app)?(/|$| )", re.I)
USER_DATA = re.compile(r"--user-data-dir=(\"[^\"]+\"|'[^']+'|\S+)")
AGENT_SCRIPT = re.compile(r"(^|/)(claude|codex)(\.[cm]?js)?$|@anthropic-ai/claude-code/|@openai/codex/", re.I)
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
        raise Stop(f"세션 상태를 읽지 못했다: {f} ({exc.__class__.__name__}). 쓰는 중일 수 있어 끝내지 않는다. 잠시 뒤 다시 부른다")
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int) or isinstance(data.get("pid"), bool) or not data.get("cwd"):
        raise Stop(f"세션 상태를 읽지 못했다: {f} (pid 가 정수가 아니거나 cwd 가 없다)")
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


RUNTIMES = {"node", "nodejs", "bun", "deno"}
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "login"}


def looks_like_claude(command: str) -> bool:
    """실행 파일 자리만 본다: 첫 토큰의 이름이 claude, 또는 node · bun 같은 런타임 다음 토큰이 claude 실행 스크립트.
    인자 속 claude(편집 중인 파일 이름 등)는 세지 않는다."""
    tokens = command.split()
    if not tokens:
        return False
    first = os.path.basename(tokens[0])
    if first == "claude":
        return True
    if first in RUNTIMES and len(tokens) > 1:
        script = tokens[1]
        return os.path.basename(script) == "claude" or "@anthropic-ai/claude-code/" in script
    return False


def process_table() -> dict:
    """pid → (ppid, 실행 이름). ps 로 한 번에 읽는다. 못 읽으면 멈춘다."""
    r = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise Stop("ps 로 프로세스 관계를 읽지 못했다. 끝내지 않는다")
    table = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2].strip()).lstrip("-"))
    return table


def session_family(sessions: List[dict], table: dict) -> set:
    """쉬는 세션과 함께 허용하는 프로세스: 세션의 자손(MCP 서버 등, cwd 를 물려받는다)과
    세션 바로 위로 이어진 셸 조상(터미널 · bash -c). 셸이 아닌 조상에서 멈춘다."""
    family = set()
    children: dict = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    for s in sessions:
        stack = [s["pid"]]
        while stack:
            pid = stack.pop()
            for c in children.get(pid, []):
                if c not in family:
                    family.add(c)
                    stack.append(c)
        parent = table.get(s["pid"], (0, ""))[0]
        while parent > 1 and parent in table and table[parent][1] in SHELLS:
            family.add(parent)
            parent = table[parent][0]
    return family


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


def ps_full() -> dict:
    """pid → (ppid, 시작 시각 문자열, 명령줄). 못 재면 멈춘다."""
    r = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,lstart=,command="], capture_output=True, text=True,
                       env={**os.environ, "LC_ALL": "C"})
    if r.returncode != 0 or not r.stdout.strip():
        raise Stop("ps 로 프로세스 목록을 읽지 못했다. 끄지도 지우지도 않는다")
    table = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 7)
        if len(parts) == 8 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), " ".join(parts[2:7]), parts[7])
    return table


def looks_like_agent(command: str) -> bool:
    """실행 파일 또는 런타임(node · bun) 다음 스크립트가 claude · codex 인가. 이름이 node 여도 에이전트면 끄지 않는다."""
    tokens = command.split()
    if not tokens:
        return False
    if AGENT_SCRIPT.search(tokens[0]):
        return True
    return os.path.basename(tokens[0]) in RUNTIMES and len(tokens) > 1 and bool(AGENT_SCRIPT.search(tokens[1]))


def user_data_dir(command: str) -> str:
    found = USER_DATA.search(command)
    return found.group(1).strip("\"'") if found else ""


def user_chrome(command: str) -> bool:
    """사용자가 쓰는 크롬: 프로필을 따로 주지 않았거나 기본 프로필 자리(~/Library · ~/.config)를 쓴다. crashpad 도 여기 넣는다."""
    if "crashpad" in command.lower():
        return True
    profile = user_data_dir(command)
    if not profile:
        return True
    home = real(Path.home())
    path = real(os.path.expanduser(profile))
    return any(inside(path, os.path.join(home, d)) for d in ("Library", ".config"))


def test_chromes(wt: str, finished: List[str], table: dict, in_wt: set) -> dict:
    """이 작업 전용 검수 크롬(본체 · 헬퍼). pid → 이유. 사용자 크롬 · crashpad 는 넣지 않는다."""
    claude_tmp = os.path.join(ct.CLAUDE_TMP_ROOT, ct.claude_tmp_name(wt))
    linked = {}
    for pid, (_pp, _st, command) in table.items():
        if not CHROME_BIN.search(command) or user_chrome(command):
            continue
        profile = real(user_data_dir(command))
        base = os.path.basename(profile)
        if inside(profile, wt):
            linked[profile] = "프로필이 워크트리 안"
        elif inside(profile, real(claude_tmp)):
            linked[profile] = "프로필이 이 작업의 Claude 임시 폴더 안"
        elif any(len(n) >= 6 and n in base for n in finished):
            linked[profile] = f"프로필 이름에 끝난 작업 이름({base})"
        elif pid in in_wt:
            linked[profile] = "워크트리에서 띄움"
    return {pid: f"검수 크롬 · {linked[real(user_data_dir(c))]}" for pid, (_pp, _st, c) in table.items()
            if CHROME_BIN.search(c) and not user_chrome(c) and real(user_data_dir(c)) in linked}


def is_tool(pid: int, table: dict, names: dict) -> bool:
    command = table.get(pid, (0, "", ""))[2]
    name = names.get(pid, os.path.basename(command.split()[0]) if command.split() else "")
    if looks_like_agent(command) or name in SHELLS:
        return False
    if CHROME_BIN.search(command):
        return not user_chrome(command)
    return bool(TOOL_NAME.match(name))


def leftovers(wt: str, finished: List[str]) -> tuple:
    """남은 프로세스를 (끌 것, 남기면 멈출 것) 으로 나눈다. pid → 설명."""
    table = ps_full()
    names = {pid: comm for pid, (_pp, comm) in process_table().items()}
    in_wt = set(processes_in(wt))
    mine, pid = {os.getpid()}, os.getpid()
    while pid in table and table[pid][0] > 1:  # 나를 부른 셸 · 에이전트 사슬은 건드리지 않는다
        pid = table[pid][0]
        mine.add(pid)
    found = set(in_wt) | {p for p, (_pp, _st, c) in table.items() if wt in c}
    kill, block = {}, {}
    for pid in sorted(found - mine):
        if pid not in table:
            continue
        label = f"pid {pid} ({names.get(pid) or '?'})"
        if is_tool(pid, table, names):
            kill[pid] = label
        elif CHROME_BIN.search(table[pid][2]):
            block[pid] = f"{label} 사용자 크롬일 수 있음"
        else:
            block[pid] = label
    for pid, why in test_chromes(wt, finished, table, in_wt).items():
        if pid not in mine and pid not in block:
            kill.setdefault(pid, f"pid {pid} ({why})")
    return kill, block


def stop_tools(targets: dict) -> List[str]:
    """시작 시각이 그대로인 것만 TERM, KILL_WAIT 안에 안 꺼지면 KILL. 끈 것 설명 목록."""
    if not targets:
        return []
    before = ps_full()
    started = {pid: before[pid][1] for pid in targets if pid in before}
    for pid in started:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + KILL_WAIT
    while time.time() < deadline and any(pid_alive(p) for p in started):
        time.sleep(0.2)
    now = ps_full()
    for pid, start in started.items():
        if pid in now and now[pid][1] == start:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return [targets[p] for p in started]


def codex_threads(wt: str) -> List[dict]:
    """이 워크트리에서 돈 Codex 세션(보관 전 · 후 둘 다). rollout 첫 줄의 cwd 로 맞추고 제목은 상태 DB 에서 읽는다(못 읽어도 id 는 낸다)."""
    found: dict = {}
    for folder, pattern in (("sessions", "**/rollout-*.jsonl"), ("archived_sessions", "rollout-*.jsonl")):
        root = Path(CODEX_HOME) / folder
        for f in root.glob(pattern) if root.is_dir() else []:
            try:
                with f.open(encoding="utf-8") as fh:
                    payload = json.loads(fh.readline()).get("payload") or {}
            except (OSError, ValueError, AttributeError):
                continue
            sid = payload.get("session_id") or payload.get("id")
            if sid and payload.get("cwd") and real(payload["cwd"]) == wt:
                found[sid] = {"id": sid, "started": str(payload.get("timestamp") or "")[:16], "title": ""}
    if found:
        try:
            import sqlite3
            dbs = sorted(Path(CODEX_HOME).glob("state_*.sqlite"))
            if dbs:
                conn = sqlite3.connect(f"file:{dbs[-1]}?mode=ro", uri=True, timeout=2)
                marks = ",".join("?" * len(found))
                for sid, name, title in conn.execute(f"select id, name, title from threads where id in ({marks})", list(found)):
                    text = name or title or ""
                    found[sid]["title"] = text.splitlines()[0][:60] if text else ""
                conn.close()
        except Exception:  # 제목은 찾기 도움일 뿐이다
            pass
    return sorted(found.values(), key=lambda t: t["started"])


def codex_lines(name: str, threads: List[dict]) -> List[str]:
    return [f"앱 보관 대상(Codex): {name} · {t['id']}" + (f" · 「{t['title']}」" if t["title"] else "") + f" · 시작 {t['started'] or '모름'}"
            for t in threads]


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
    table = process_table()
    allowed = {s["pid"] for s in sessions} | (session_family(sessions, table) if sessions else set())
    others = [p for p in processes_in(wt) if p not in allowed]
    if others:
        # 부모가 끝나 떨어져 나간 개발 서버 같은 알려진 도구는 세션을 끝낸 뒤 끈다
        full = ps_full()
        names = {pid: comm for pid, (_pp, comm) in table.items()}
        others = [p for p in others if not is_tool(p, full, names)]
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
        # Claude 샌드박스 안에서는 codex archive 가 파일 시각 설정(set_times)에서 막힐 수 있다. 워크트리가 없어도 cwd 문자열로 다시 찾는다
        return (f"경고: Codex 세션 보관 실패(정리는 계속한다): {(r.stderr or r.stdout).strip()[-200:]}\n"
                f"다시 보관: 샌드박스 밖(사용자 터미널)에서 {sys.executable} {script} {worktree}")
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
        # 앱 목록 제목은 자동 생성이라 이름으로는 못 찾는다. 목록 링크 /code/<bridgeSessionId> 로 맞춘다. 종료 전에 읽어 둔다
        ids = [str(s["bridgeSessionId"]) for s in sessions if s.get("bridgeSessionId")]
        name = archive_line(name, ids, len(sessions))
        if args.dry_run:
            print(f"[미리보기] 안전 조건 통과: {wt}")
            print(f"  끝낼 Claude 세션: {', '.join(str(s['pid']) for s in sessions) or '없음'} · 지울 워크트리: {wt}")
            branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            try:
                kill, _block = leftovers(wt, finished_names(wt, branch))
            except Stop as exc:
                kill = {}
                print(f"  남은 프로세스를 재지 못함: {exc}")
            for line in sorted(kill.values()):
                print(f"  끌 것(지금 기준) : {line}")
            sweep_temp(wt, cfg, False, finished_names(wt, branch))
            for line in codex_lines(os.path.basename(wt), codex_threads(wt)):
                print(line)
            print(name)
            return 0
        with CleanupLock():
            # 순서: 세션 정상 종료(신호 직전 확인 포함) → (Orca) worker-release → 사후 확인 → Codex 보관 → 워크트리 삭제
            terminate(sessions, args.wait, wt)
            if args.dispatch and orca:
                subprocess.run([orca, "orchestration", "worker-release", "--dispatch", args.dispatch, "--json"], capture_output=True)
            left = wait_empty(wt, args.wait)
            branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            kill, block = leftovers(wt, finished_names(wt, branch))  # 워크트리 밖 검수 크롬도 본다
            if (kill or left) and not block:
                for line in stop_tools(kill):
                    print(f"끔 : {line}")
                left = wait_empty(wt, args.wait)
            if left:
                how = ("Orca 가 터미널을 아직 닫지 않았다. Orca 에서 그 작업자 터미널을 닫고 다시 부른다" if args.dispatch
                       else "작업자를 띄운 터미널(셸)이 아직 열려 있다. 그 터미널을 닫고 다시 부른다")
                desc = ", ".join(f"{p} {(process_command(p) or '?')[:60]}" for p in left)
                raise Stop(f"세션을 끝낸 뒤에도 이 워크트리를 쓰는 프로세스가 남았다({desc}). {how}. 지우지 않는다")
            return remove(args, wt, cfg, orca, name)
    except Stop as exc:
        print(f"멈춤: {exc}", file=sys.stderr)
        return 1


def archive_line(name: str, ids: List[str], n_sessions: int) -> str:
    """마지막 줄. 세션 ID 가 있으면 「앱 보관 대상(Claude): <이름> · <ID>」, 없으면 이름만과 건너뜀 안내."""
    if ids:
        return f"앱 보관 대상(Claude): {name} · {' · '.join(ids)}"
    why = "끝낸 Claude 세션 없음" if n_sessions == 0 else "세션 파일에 bridgeSessionId 없음"
    return f"앱 보관 대상(Claude): {name} (세션 ID 모름: {why}. 브라우저 보관은 건너뛰고 보고한다)"


def wait_empty(wt: str, wait: float) -> List[int]:
    """세션이 끝나며 자식 · 셸도 따라 끝날 시간을 준다. 끝까지 남은 pid 를 돌려준다."""
    deadline = time.time() + wait
    left = processes_in(wt)
    while left and time.time() < deadline:
        time.sleep(0.2)
        left = processes_in(wt)
    return left


def finished_names(wt: str, branch: str) -> List[str]:
    """끝난 작업 이름: /tmp 검수 찌꺼기 중 이 이름이 든 것은 24시간이 안 됐어도 치운다."""
    return sorted({os.path.basename(wt), (branch or "").rsplit("/", 1)[-1]} - {"", "HEAD"})


def all_worktrees(cfg: Optional[dict]) -> Optional[List[str]]:
    """하네스 · 설정 저장소 전부의 지금 있는 워크트리 경로. 설정이 없거나 하나라도 못 읽으면 None."""
    if not cfg:
        return None
    parent = hl.git_common_parent(HARNESS_ROOT) or HARNESS_ROOT.parent
    paths: List[str] = []
    for repo in [HARNESS_ROOT] + [hl.repo_path(r, parent) for r in cfg["repos"]]:
        r = git(repo, "worktree", "list", "--porcelain")
        if r.returncode != 0:
            return None
        paths += [line[len("worktree "):] for line in r.stdout.splitlines() if line.startswith("worktree ")]
    return paths


def sweep_temp(wt: str, cfg: Optional[dict], apply: bool, finished: List[str]) -> None:
    """그 워크트리의 Claude 임시 폴더 → 없어진 작업 공간의 것 → /tmp 검수 찌꺼기. 실패는 경고만."""
    if os.name == "nt":
        return
    try:
        if apply:
            ct.claude_tmp([wt], True)
        else:
            ct.claude_tmp_preview(wt)
    except Exception as exc:
        print(f"경고: Claude 임시 폴더를 보지 못했다({type(exc).__name__}: {exc})", file=sys.stderr)
    roots = [os.path.realpath(os.path.expanduser(cfg["orca"]["workspaces_dir"]))] if cfg else []
    parent = hl.git_common_parent(HARNESS_ROOT)
    if parent:
        roots.append(str(parent / ".work"))
    try:
        failed = ct.sweep(apply, finished, roots, all_worktrees(cfg))
    except Exception as exc:  # 찌꺼기 정리가 실패해도 작업자 정리 결과는 바꾸지 않는다
        print(f"경고: 임시 찌꺼기를 보지 못했다({type(exc).__name__}: {exc})", file=sys.stderr)
        return
    if failed:
        print("경고: 임시 찌꺼기 일부를 남겼다(위 이유). 워크트리 정리는 끝났다.", file=sys.stderr)


def remove(args, wt: str, cfg: Optional[dict], orca: Optional[str], name: str) -> int:
    threads = codex_threads(wt)  # 보관하면 rollout 이 옮겨지므로 먼저 모은다
    warn = archive_codex(wt, cfg)
    if warn:
        print(warn, file=sys.stderr)
    branch = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    main_repo = str(Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).parent)
    if args.dispatch and orca:
        r = subprocess.run([orca, "worktree", "rm", "--worktree", f"path:{wt}", "--run-hooks", "--json"], capture_output=True, text=True)
    else:
        r = git(main_repo, "worktree", "remove", wt)
        if r.returncode == 0 and branch and branch != "HEAD":
            delete_branch(main_repo, branch)
    if r.returncode != 0:
        print(f"멈춤: 워크트리 지우기 실패: {(r.stderr or r.stdout).strip()[-200:]}", file=sys.stderr)
        return 1
    print(f"정리 완료: {wt}")
    sweep_temp(wt, cfg, True, finished_names(wt, branch))
    for line in codex_lines(os.path.basename(wt), threads):
        print(line)
    print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
