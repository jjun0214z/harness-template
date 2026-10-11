#!/usr/bin/env python3
"""Codex SessionStart 훅: 현재 스레드(세션)를 cwd 의 프로젝트에 자동 귀속.

배경: 세션을 Orca 가 만들든 Codex 앱이 만들든 같은 프로젝트 아래로 보여야 한다.
데스크톱 앱서버 귀속 동기화가 꺼져 있을 수 있어 이 훅이 세션 시작 때 cwd 로 프로젝트를 유도해
앱서버 projectId 와 데스크톱 `thread-project-assignments` 를 같은 프로젝트 이름으로 귀속시킨다.
cwd → 프로젝트 유도와 앱서버 클라이언트는 같은 폴더의 codex_project_binding.py 를 쓴다(경로 고정 없음).

이 hooks.json 은 Claude·Codex 공용이다. Claude 세션에서는 아무 것도 하지 않는다.

훅 모드(기본): stdin JSON 에서 session_id·cwd·hook_event_name 을 읽는다.
  Codex 세션이 아니거나 codex 를 못 찾으면 즉시 exit 0.
  SessionStart 시점에는 스레드 행이 아직 state db 에 없을 수 있으므로,
  짧게 지연(기본 3초) 뒤 귀속하는 별도 프로세스를 백그라운드로 띄우고 훅 자체는 즉시 exit 0 한다.
  귀속은 세션을 절대 막지 않는다.

바인드 모드(`--bind <session_id> <cwd>`): 백그라운드 프로세스가 쓴다.
  지연 뒤 cwd 로 프로젝트를 유도하고 앱서버 API 로 귀속한다:
  이미 같으면 아무 것도 안 함, 데스크톱 「프로젝트 없음」·충돌이면 건드리지 않음,
  앱서버가 죽어 있으면 세션은 막지 않고 재시도 뒤 진단 로그를 남긴다.
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
from typing import Callable

CODEX_APP_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"  # macOS 앱 번들. 없으면 PATH 를 본다
DEFAULT_DELAY = 3.0
DEFAULT_RETRIES = 15
# 재시도마다 앱서버를 새로 띄우고 호출 하나가 20초까지 걸려 15회면 수십 분 남을 수 있다. 전체 마감을 둔다.
DEFAULT_DEADLINE = 120.0


BINDING_NAME = "codex_project_binding.py"


def _binding_candidates() -> list[Path]:
    """codex_project_binding.py 후보. 플러그인에 같이 들어 있으므로 같은 폴더가 먼저다."""
    here = Path(__file__).resolve()
    candidates: list[Path] = [here.parent / BINDING_NAME]
    env = os.environ.get("HARNESS_BINDING_DIR")
    if env:
        candidates.append(Path(env) / BINDING_NAME)
    return candidates


def load_binding_module() -> ModuleType:
    """codex_project_binding.py 를 모듈로 읽는다(cwd→프로젝트 유도·앱서버 재사용)."""
    candidates = _binding_candidates()
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ImportError(f"{BINDING_NAME} 를 못 찾음: " + ", ".join(str(p) for p in candidates))
    spec = importlib.util.spec_from_file_location("codex_project_binding", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{BINDING_NAME} 를 못 읽음: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_codex_session(payload: dict, env: dict) -> bool:
    """Codex 세션이면 True. Claude 세션·페이로드에 Codex 신호가 없으면 False."""
    has_claude = any(key.startswith("CLAUDE_") for key in env)
    has_codex = any(key.startswith("CODEX_") for key in env)
    if has_claude and not has_codex:
        return False
    session_id = (
        env.get("CODEX_SESSION_ID")
        or env.get("CODEX_THREAD_ID")
        or payload.get("session_id")
        or payload.get("thread_id")
    )
    if not session_id:
        return False
    return True


def session_id_from(payload: dict, env: dict) -> str | None:
    return (
        env.get("CODEX_SESSION_ID")
        or env.get("CODEX_THREAD_ID")
        or payload.get("session_id")
        or payload.get("thread_id")
    )


def find_codex_bin(mod: ModuleType) -> str | None:
    """codex 실행 파일. 못 찾으면 None(조용히 포기)."""
    if os.path.isfile(CODEX_APP_BIN):
        return CODEX_APP_BIN
    try:
        return mod.find_codex(None)
    except FileNotFoundError:
        return None


def derive_target(
    cwd: str, mod: ModuleType, codex_home: Path, home: Path
) -> tuple[str | None, dict]:
    """cwd 에 대응하는 project id 와 프로젝트 표. 대응 안 되면 (None, projects)."""
    db_path = mod.find_state_db(codex_home)
    conn = mod.open_readonly(db_path)
    try:
        projects = mod.load_projects(conn)
    finally:
        conn.close()
    return mod.resolve_project(cwd, projects, home), projects


def bind_session(
    session_id: str,
    target_pid: str,
    projects: dict,
    mod: ModuleType,
    desktop: dict,
    server_factory: Callable[[], object],
    desktop_path: Path | None = None,
    retries: int = DEFAULT_RETRIES,
    sleep_fn: Callable[[float], None] = time.sleep,
    deadline: float = DEFAULT_DEADLINE,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """앱서버로 스레드 하나를 귀속. 상태 문자열을 돌려준다(세션은 막지 않는다)."""
    if session_id in desktop.get("projectless", set()):
        return "projectless"
    desktop_status, _desktop_target = mod.desktop_assignment_status(
        session_id, projects[target_pid], desktop
    )
    if desktop_status in ("conflict-desktop", "desktop-project-missing"):
        return desktop_status

    last = "failed"
    ends = clock() + deadline
    for attempt in range(max(1, retries)):
        if attempt and clock() >= ends:
            return f"deadline-exceeded: {last}"
        try:
            server = server_factory()
            try:
                server.initialize()
                listed = server.call("project/list", {}).get("data") or []
                problems = mod.projects_match(projects, listed)
                if problems:
                    return "projects-differ"
                sync = mod.sync_project_assignment(
                    server,
                    session_id,
                    target_pid,
                    projects[target_pid],
                    desktop_path=desktop_path,
                    sleep_fn=sleep_fn,
                )
            finally:
                close = getattr(server, "close", None)
                if callable(close):
                    close()
            return sync["status"]
        except (RuntimeError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last = f"error: {exc}"
        if attempt + 1 < max(1, retries):
            sleep_fn(1.0)
    return last


def bind_worker(
    session_id: str,
    cwd: str,
    mod: ModuleType,
    codex_home: Path,
    home: Path,
    codex_bin: str,
    server_factory: Callable[[], object] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    delay: float = DEFAULT_DELAY,
    retries: int = DEFAULT_RETRIES,
    deadline: float = DEFAULT_DEADLINE,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """지연 뒤 cwd 로 프로젝트를 유도해 귀속한다. 백그라운드 프로세스가 부른다."""
    if delay:
        sleep_fn(delay)
    try:
        target, projects = derive_target(cwd, mod, codex_home, home)
    except (OSError, FileNotFoundError, ValueError):
        return "no-db"
    if target is None:
        return "no-project"
    desktop_path = codex_home / ".codex-global-state.json"
    desktop = None
    desktop_error: Exception | None = None
    for attempt in range(3):
        try:
            desktop = mod.load_desktop_state(desktop_path)
            break
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            desktop_error = exc
            if attempt < 2:
                sleep_fn(0.2)
    if desktop is None:
        return f"desktop-state-error: {desktop_error}"
    if server_factory is None:
        server_factory = lambda: mod.AppServer(codex_bin)  # noqa: E731
    return bind_session(
        session_id,
        target,
        projects,
        mod,
        desktop,
        server_factory,
        desktop_path=desktop_path,
        retries=retries,
        sleep_fn=sleep_fn,
        deadline=deadline,
        clock=clock,
    )


def codex_home_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def open_binder_stderr(codex_home: Path) -> int | None:
    """바인더 stderr 를 받을 0600 파일. 못 열면 None(DEVNULL 로 버린다).

    10-09 뒤 Orca Codex 세션에서 바인더 로그가 한 줄도 안 남았다. 죽는 이유를 남기려고 버리지 않는다.
    """
    path = bind_log_path(codex_home).with_suffix(".stderr.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        return fd
    except OSError:
        return None


def spawn_binder(session_id: str, cwd: str) -> None:
    """지연 귀속을 하는 백그라운드 프로세스를 띄우고 돌아온다(세션을 막지 않는다)."""
    codex_home = codex_home_dir()
    err_fd = open_binder_stderr(codex_home)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-B", os.path.abspath(__file__), "--bind", session_id, cwd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_fd if err_fd is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        if err_fd is not None:
            os.close(err_fd)
    try:
        log_result(bind_log_path(codex_home), session_id, cwd, f"spawned pid={proc.pid}")
    except Exception:
        pass


def bind_log_path(codex_home: Path) -> Path:
    override = os.environ.get("HARNESS_CODEX_BIND_LOG")
    return Path(override).expanduser() if override else codex_home / "logs" / "harness-codex-bind-session.log"


def log_result(path: Path, session_id: str, cwd: str, status: str) -> None:
    """백그라운드 결과를 세션을 막지 않는 0600 로그에 한 줄 남긴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", closefd=True) as log:
            fcntl.flock(log.fileno(), fcntl.LOCK_EX)
            stamp = dt.datetime.now(dt.timezone.utc).isoformat()
            safe_status = status.replace("\t", " ").replace("\r", " ").replace("\n", " ")
            safe_cwd = cwd.replace("\t", " ").replace("\r", " ").replace("\n", " ")
            log.write(f"{stamp}\t{session_id}\t{safe_status}\t{safe_cwd}\n")
            log.flush()
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def run_hook(
    payload: dict,
    env: dict,
    spawn: Callable[[str, str], None] = spawn_binder,
    mod_loader: Callable[[], ModuleType] = load_binding_module,
) -> int:
    """훅 모드. Codex 세션이면 백그라운드 바인더를 띄우고 즉시 0 을 돌려준다."""
    if not is_codex_session(payload, env):
        return 0
    session_id = session_id_from(payload, env)
    if not session_id:
        return 0
    cwd = payload.get("cwd") or env.get("PWD") or os.getcwd()
    try:
        mod = mod_loader()
    except Exception as exc:  # SyntaxError 등도 세션을 막지 않는다
        print(f"harness codex-bind-session: 귀속 건너뜀 ({exc})", file=sys.stderr)
        return 0
    if find_codex_bin(mod) is None:
        print("harness codex-bind-session: 귀속 건너뜀 (codex 실행 파일 없음)", file=sys.stderr)
        return 0
    try:
        spawn(session_id, cwd)
    except Exception as exc:
        print(f"harness codex-bind-session: 바인더를 못 띄움 ({exc})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex SessionStart 세션 귀속 훅")
    parser.add_argument("--bind", nargs=2, metavar=("SESSION_ID", "CWD"))
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args(argv)

    if args.bind:
        session_id, cwd = args.bind
        codex_home = codex_home_dir()
        try:
            log_result(bind_log_path(codex_home), session_id, cwd, "started")
        except Exception as exc:
            print(f"harness codex-bind-session: 시작 로그 실패 ({exc})", file=sys.stderr)
        try:
            mod = load_binding_module()
        except Exception as exc:
            try:
                log_result(
                    bind_log_path(codex_home),
                    session_id,
                    cwd,
                    f"binding-module-error: {exc}",
                )
            except Exception:
                pass
            print(f"harness codex-bind-session: 귀속 건너뜀 ({exc})", file=sys.stderr)
            return 0
        codex_bin = find_codex_bin(mod)
        if codex_bin is None:
            try:
                log_result(
                    bind_log_path(codex_home),
                    session_id,
                    cwd,
                    "codex-binary-missing",
                )
            except Exception:
                pass
            print("harness codex-bind-session: 귀속 건너뜀 (codex 실행 파일 없음)", file=sys.stderr)
            return 0
        try:
            status = bind_worker(
                session_id,
                cwd,
                mod,
                codex_home,
                Path.home(),
                codex_bin,
                delay=args.delay,
            )
        except Exception as exc:
            status = f"unexpected-error: {type(exc).__name__}: {exc}"
        try:
            log_result(bind_log_path(codex_home), session_id, cwd, status)
        except Exception as exc:
            print(f"harness codex-bind-session: 로그 기록 실패 ({exc})", file=sys.stderr)
        if status not in ("bound", "already", "projectless", "conflict-desktop", "conflict-appserver", "no-project"):
            print(f"harness codex-bind-session: 귀속 실패 {session_id} ({status})", file=sys.stderr)
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        payload = {}
    try:
        return run_hook(payload, dict(os.environ))
    except Exception as exc:  # 훅 실패로 세션 시작을 막지 않는다
        print(f"harness codex-bind-session: 귀속 건너뜀 ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
