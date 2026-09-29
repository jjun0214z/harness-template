#!/usr/bin/env python3
"""Codex 스레드(세션)의 프로젝트 귀속을 점검하고 앱서버 API 로 채운다.

배경: Codex 앱(데스크톱)은 스레드를 프로젝트 아래 보여 주지만 앱서버 sqlite(`~/.codex/state_N.sqlite`)
의 `threads.project_id` 는 비어 있을 수 있다. 그러면 모바일 Remote 에서 프로젝트 없는 스레드로 보인다.
하네스는 이 모듈을 SessionStart 훅(codex-bind-session.py)과 수동 점검에 같이 쓴다.
원본은 하네스 플러그인 scripts/ 에 같이 들어 있어 플러그인 캐시에서도 경로를 고정하지 않고 찾는다.

--check(기본): sqlite 를 읽기 전용으로 열어 스레드 cwd 를 프로젝트에 대응시키고
  프로젝트별 귀속/미귀속 표와 미귀속 목록을 낸다. 미귀속이 있으면 exit 1.
--fix: `codex app-server --listen stdio://` 를 띄워 미귀속 스레드마다
  `thread/metadata/update` 를 부르고 `thread/read` 로 재확인한다.

대응 규칙(cwd → 프로젝트):
  1. `project_roots.path` 와 같거나 그 하위
  2. `~/orca/workspaces/<레포이름>/...` 이면 같은 이름의 프로젝트
  3. 그 밖의 경로는 `git rev-parse --git-common-dir` 로 본 저장소를 찾아 그 부모가 project root 이면 대응
데스크톱 옛 귀속과 cwd 유도 결과가 다르면 「충돌」로 표시하고 --fix 에서도 건드리지 않는다.
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from typing import Callable

STATE_PATTERN = re.compile(r"^state_(\d+)\.sqlite$")


# ---------- 입력 읽기 ----------


def find_state_db(codex_home: Path) -> Path:
    """`state_N.sqlite` 중 N 이 가장 큰 파일."""
    candidates: list[tuple[int, Path]] = []
    for entry in codex_home.iterdir():
        match = STATE_PATTERN.match(entry.name)
        if match and entry.is_file():
            candidates.append((int(match.group(1)), entry))
    if not candidates:
        raise FileNotFoundError(f"state_N.sqlite 가 없다: {codex_home}")
    return max(candidates)[1]


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_projects(conn: sqlite3.Connection) -> dict[str, dict]:
    """project id → {name, roots[]}."""
    projects: dict[str, dict] = {}
    for row in conn.execute("SELECT id, name, position FROM projects ORDER BY position, id"):
        projects[row["id"]] = {"id": row["id"], "name": row["name"], "roots": []}
    for row in conn.execute(
        "SELECT project_id, path FROM project_roots ORDER BY project_id, position"
    ):
        if row["project_id"] in projects:
            projects[row["project_id"]]["roots"].append(os.path.normpath(row["path"]))
    return projects


def load_threads(conn: sqlite3.Connection, include_archived: bool) -> list[dict]:
    sql = (
        "SELECT id, cwd, archived, project_id, "
        "COALESCE(updated_at_ms, updated_at * 1000) AS updated_ms, "
        "COALESCE(NULLIF(name, ''), NULLIF(title, ''), preview) AS label "
        "FROM threads"
    )
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY updated_ms DESC, id"
    return [dict(row) for row in conn.execute(sql)]


def load_desktop_state(path: Path) -> dict:
    """데스크톱 앱 상태: 로컬 프로젝트 이름 · 옛 귀속 · 「프로젝트 없음」 명시 목록."""
    if not path.is_file():
        return {"local_project_names": {}, "assignments": {}, "projectless": set()}
    data = json.loads(path.read_text(encoding="utf-8"))
    local_projects = data.get("local-projects") or {}
    names = {pid: (p or {}).get("name") for pid, p in local_projects.items()}
    assignments: dict[str, str] = {}
    for thread_id, assignment in (data.get("thread-project-assignments") or {}).items():
        if not isinstance(assignment, dict):
            continue
        if assignment.get("projectKind") not in (None, "local"):
            continue
        name = names.get(assignment.get("projectId"))
        if name:
            assignments[thread_id] = name
    projectless = set(data.get("projectless-thread-ids") or [])
    return {"local_project_names": names, "assignments": assignments, "projectless": projectless}


# ---------- cwd → 프로젝트 ----------


def git_common_dir(cwd: str) -> str | None:
    if not os.path.isdir(cwd):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _is_within(path: str, root: str) -> bool:
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def resolve_project(
    cwd: str,
    projects: dict[str, dict],
    home: Path,
    common_dir_fn: Callable[[str], str | None] = git_common_dir,
) -> str | None:
    """cwd 에 대응하는 project id. 대응 안 되면 None."""
    if not cwd:
        return None
    cwd = os.path.normpath(cwd)
    # 1. project root 와 같거나 그 하위
    for pid, project in projects.items():
        for root in project["roots"]:
            if _is_within(cwd, root):
                return pid
    # 2. ~/orca/workspaces/<레포이름>/...
    orca_root = os.path.normpath(str(home / "orca" / "workspaces"))
    if _is_within(cwd, orca_root) and cwd != orca_root:
        rest = os.path.relpath(cwd, orca_root).split(os.sep)
        repo_name = rest[0]
        for pid, project in projects.items():
            if project["name"] == repo_name:
                return pid
    # 3. git 본 저장소의 부모 경로가 project root 인가
    common = common_dir_fn(cwd)
    if common:
        parent = os.path.normpath(os.path.dirname(os.path.normpath(common)))
        for pid, project in projects.items():
            for root in project["roots"]:
                if os.path.normpath(root) == parent:
                    return pid
    return None


# ---------- 분류 ----------


def classify(
    threads: list[dict],
    projects: dict[str, dict],
    desktop: dict,
    home: Path,
    include_projectless: bool = False,
    common_dir_fn: Callable[[str], str | None] = git_common_dir,
) -> dict:
    """스레드를 bound · unbound · conflict · projectless(제외) · skipped 로 나눈다."""
    bound: list[dict] = []
    unbound: list[dict] = []
    conflicts: list[dict] = []
    projectless: list[dict] = []
    skipped: list[dict] = []
    cache: dict[str, str | None] = {}

    for thread in threads:
        cwd = thread["cwd"] or ""
        if cwd not in cache:
            cache[cwd] = resolve_project(cwd, projects, home, common_dir_fn)
        derived = cache[cwd]
        if derived is None:
            skipped.append(thread)
            continue
        item = dict(thread)
        item["derived_project_id"] = derived
        item["derived_project_name"] = projects[derived]["name"]

        desktop_name = desktop["assignments"].get(thread["id"])
        if desktop_name and desktop_name != item["derived_project_name"]:
            item["reason"] = (
                f"데스크톱 옛 귀속 {desktop_name} ≠ cwd 유도 {item['derived_project_name']}"
            )
            conflicts.append(item)
            continue

        current = thread.get("project_id")
        if current:
            if current != derived:
                current_name = projects.get(current, {}).get("name", current)
                item["reason"] = (
                    f"앱서버 귀속 {current_name} ≠ cwd 유도 {item['derived_project_name']}"
                )
                conflicts.append(item)
            else:
                bound.append(item)
            continue

        if thread["id"] in desktop["projectless"] and not include_projectless:
            projectless.append(item)
            continue
        unbound.append(item)

    per_project: dict[str, dict] = {
        pid: {"name": p["name"], "bound": 0, "unbound": 0} for pid, p in projects.items()
    }
    for item in bound:
        per_project[item["derived_project_id"]]["bound"] += 1
    for item in unbound:
        per_project[item["derived_project_id"]]["unbound"] += 1

    return {
        "db_threads": len(threads),
        "projects": per_project,
        "bound": bound,
        "unbound": unbound,
        "conflicts": conflicts,
        "projectless_excluded": projectless,
        "skipped": skipped,
    }


# ---------- 출력 ----------


def _short_cwd(cwd: str, home: Path) -> str:
    home_s = str(home)
    if cwd == home_s or cwd.startswith(home_s + os.sep):
        return "~" + cwd[len(home_s):]
    return cwd


def _fmt_time(ms: int | None) -> str:
    if not ms:
        return "-"
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def render_report(report: dict, home: Path, db_path: Path | None = None) -> str:
    lines: list[str] = []
    if db_path is not None:
        lines.append(f"sqlite: {db_path} (읽기 전용) · 스레드 {report['db_threads']}개")
    lines.append("")
    lines.append("| 프로젝트 | 귀속됨 | 미귀속 |")
    lines.append("| --- | ---: | ---: |")
    for entry in sorted(report["projects"].values(), key=lambda p: p["name"]):
        lines.append(f"| {entry['name']} | {entry['bound']} | {entry['unbound']} |")
    total_bound = sum(p["bound"] for p in report["projects"].values())
    total_unbound = len(report["unbound"])
    lines.append(f"| 합계 | {total_bound} | {total_unbound} |")
    lines.append("")
    lines.append(
        f"충돌 {len(report['conflicts'])} · 프로젝트 없음(데스크톱 명시, 제외) "
        f"{len(report['projectless_excluded'])} · 대응 안 됨(건너뜀) {len(report['skipped'])}"
    )
    if report["unbound"]:
        lines.append("")
        lines.append("미귀속 스레드")
        lines.append("| 스레드 | 프로젝트 | cwd | 갱신 |")
        lines.append("| --- | --- | --- | --- |")
        for item in report["unbound"]:
            lines.append(
                f"| {item['id']} | {item['derived_project_name']} | "
                f"{_short_cwd(item['cwd'], home)} | {_fmt_time(item.get('updated_ms'))} |"
            )
    if report["conflicts"]:
        lines.append("")
        lines.append("충돌 (건드리지 않음)")
        for item in report["conflicts"]:
            lines.append(f"- {item['id']} · {_short_cwd(item['cwd'], home)} · {item['reason']}")
    return "\n".join(lines)


def report_to_json(report: dict) -> dict:
    def slim(item: dict) -> dict:
        return {
            "id": item["id"],
            "cwd": item["cwd"],
            "archived": bool(item.get("archived")),
            "updated_ms": item.get("updated_ms"),
            "project_id": item.get("derived_project_id"),
            "project_name": item.get("derived_project_name"),
            "reason": item.get("reason"),
        }

    return {
        "db_threads": report["db_threads"],
        "projects": report["projects"],
        "unbound": [slim(i) for i in report["unbound"]],
        "conflicts": [slim(i) for i in report["conflicts"]],
        "projectless_excluded": [i["id"] for i in report["projectless_excluded"]],
        "skipped": [{"id": i["id"], "cwd": i["cwd"]} for i in report["skipped"]],
    }


# ---------- 앱서버 ----------


class AppServer:
    """`codex app-server --listen stdio://` 와 JSON-RPC 로 말한다. with 문으로 반드시 닫는다.

    stdout 은 전용 리더 스레드가 줄 단위로 큐에 넣는다. select + 버퍼 있는 readline 은
    앱서버가 알림과 응답을 한 번에 쓰면 두 번째 줄이 파이썬 버퍼에만 남아 시간 초과가 났다(검토 2026-09-27).
    stderr 는 임시 파일로 보내고 앱서버가 죽었을 때만 앞 500자를 보여 준다.
    """

    def __init__(self, codex_bin: str, client_name: str = "harness-project-binding") -> None:
        self._stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            [codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._next_id = 0
        self.client_name = client_name
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._reader = threading.Thread(target=self._pump, name="codex-app-server-stdout", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _stderr_head(self) -> str:
        try:
            self._stderr_file.seek(0)
            return self._stderr_file.read(500)
        except (OSError, ValueError):
            return ""

    def __enter__(self) -> "AppServer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        assert self.proc.stdin is not None
        self._next_id += 1
        request_id = self._next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"앱서버가 닫혔다: {self._stderr_head()}") from exc
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"{method} 응답 없음 ({timeout}s)") from exc
            if line is None:
                raise RuntimeError(f"앱서버가 닫혔다: {self._stderr_head()}")
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == request_id:
                if "error" in parsed:
                    raise RuntimeError(f"{method}: {parsed['error']}")
                return parsed.get("result") or {}

    def initialize(self) -> dict:
        return self.call(
            "initialize",
            {
                "clientInfo": {"name": self.client_name, "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._reader.join(timeout=5)
        try:
            self._stderr_file.close()
        except OSError:
            pass


def find_codex(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "codex"
    if local.is_file():
        return str(local)
    raise FileNotFoundError("codex 실행 파일을 찾지 못했다 (--codex 로 지정)")


def projects_match(sqlite_projects: dict[str, dict], server_projects: list[dict]) -> list[str]:
    """sqlite 와 앱서버 project/list 의 id·root 가 같은지. 다른 점을 문장으로 돌려준다."""
    problems: list[str] = []
    server_map: dict[str, list[str]] = {}
    for project in server_projects:
        roots = [os.path.normpath(r.get("path", "")) for r in project.get("roots") or []]
        server_map[project.get("id", "")] = roots
    for pid, project in sqlite_projects.items():
        if pid not in server_map:
            problems.append(f"앱서버에 없는 프로젝트 {project['name']} ({pid})")
        elif sorted(server_map[pid]) != sorted(project["roots"]):
            problems.append(f"root 가 다름 {project['name']}: sqlite {project['roots']} / 앱서버 {server_map[pid]}")
    for pid in server_map:
        if pid not in sqlite_projects:
            problems.append(f"sqlite 에 없는 프로젝트 {pid}")
    return problems


def run_fix(report: dict, projects: dict[str, dict], server: AppServer) -> list[dict]:
    """미귀속 스레드마다 thread/metadata/update → thread/read 재확인. 결과 목록을 돌려준다."""
    server.initialize()
    listed = server.call("project/list", {}).get("data") or []
    problems = projects_match(projects, listed)
    if problems:
        raise RuntimeError("프로젝트 목록이 sqlite 와 다르다. 중단: " + "; ".join(problems))

    results: list[dict] = []
    for item in report["unbound"]:
        target = item["derived_project_id"]
        outcome = {"id": item["id"], "project_name": item["derived_project_name"], "ok": False, "detail": ""}
        try:
            server.call("thread/metadata/update", {"threadId": item["id"], "projectId": target})
            read = server.call("thread/read", {"threadId": item["id"], "includeTurns": False})
            actual = (read.get("thread") or {}).get("projectId")
            if actual == target:
                outcome["ok"] = True
                outcome["detail"] = "재확인 일치"
            else:
                outcome["detail"] = f"재확인 불일치: {actual}"
        except (RuntimeError, TimeoutError) as exc:
            outcome["detail"] = str(exc)
        results.append(outcome)
    return results


def render_fix(results: list[dict]) -> str:
    lines = ["", "| 스레드 | 프로젝트 | 결과 |", "| --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['project_name']} | {'통과' if r['ok'] else '실패'} · {r['detail']} |")
    ok = sum(1 for r in results if r["ok"])
    lines.append(f"| 합계 | | {ok}/{len(results)} 통과 |")
    return "\n".join(lines)


# ---------- main ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="점검만 (기본)")
    mode.add_argument("--fix", action="store_true", help="앱서버로 미귀속 스레드를 귀속시킨다")
    parser.add_argument("--include-archived", action="store_true", help="보관된 스레드도 포함")
    parser.add_argument(
        "--include-projectless",
        action="store_true",
        help="데스크톱이 「프로젝트 없음」으로 명시한 스레드도 포함",
    )
    parser.add_argument("--json", action="store_true", help="기계용 JSON 출력")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--state", type=Path, help="state_N.sqlite 경로 (기본: 번호가 가장 큰 파일)")
    parser.add_argument("--global-state", type=Path, help=".codex-global-state.json 경로")
    parser.add_argument("--codex", help="codex 실행 파일 (기본: PATH 또는 ~/.local/bin/codex)")
    parser.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    home = args.home
    db_path = args.state or find_state_db(args.codex_home)
    global_state = args.global_state or (args.codex_home / ".codex-global-state.json")

    conn = open_readonly(db_path)
    try:
        projects = load_projects(conn)
        threads = load_threads(conn, args.include_archived)
    finally:
        conn.close()
    desktop = load_desktop_state(global_state)
    report = classify(threads, projects, desktop, home, args.include_projectless)

    fix_results: list[dict] | None = None
    if args.fix and report["unbound"]:
        with AppServer(find_codex(args.codex)) as server:
            fix_results = run_fix(report, projects, server)

    if args.json:
        payload = report_to_json(report)
        payload["db_path"] = str(db_path)
        if fix_results is not None:
            payload["fix"] = fix_results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_report(report, home, db_path))
        if fix_results is not None:
            print(render_fix(fix_results))

    if fix_results is not None:
        return 0 if all(r["ok"] for r in fix_results) else 1
    return 1 if report["unbound"] else 0


if __name__ == "__main__":
    sys.exit(main())
