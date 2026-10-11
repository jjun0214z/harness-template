#!/usr/bin/env python3
"""codex-project-binding.py 의 대응 규칙과 --fix 흐름(앱서버 mock) 검증."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "harness" / "skeleton" / "plugin" / "scripts" / "codex_project_binding.py"
SPEC = importlib.util.spec_from_file_location("codex_project_binding", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

HOME = Path("/home/tester")
PROJECTS_ROOT = HOME / "work" / "projects"
FRONT_ID = "p-front"
APP_ID = "p-app"
PROJECTS = {
    FRONT_ID: {"id": FRONT_ID, "name": "front", "roots": [str(PROJECTS_ROOT / "front")]},
    APP_ID: {"id": APP_ID, "name": "app", "roots": [str(PROJECTS_ROOT / "app")]},
}


def no_git(_cwd: str) -> None:
    return None


def make_db(path: Path, threads: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE project_roots (project_id TEXT, position INTEGER, path TEXT);
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, cwd TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '', name TEXT, archived INTEGER NOT NULL DEFAULT 0,
            project_id TEXT, updated_at INTEGER NOT NULL, updated_at_ms INTEGER
        );
        """
    )
    for pid, project in PROJECTS.items():
        conn.execute("INSERT INTO projects VALUES (?, ?, 0)", (pid, project["name"]))
        for root in project["roots"]:
            conn.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (pid, root))
    conn.executemany(
        "INSERT INTO threads (id, cwd, archived, project_id, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?)",
        threads,
    )
    conn.commit()
    conn.close()


class ResolveProjectTest(unittest.TestCase):
    def test_root_and_subpath(self) -> None:
        self.assertEqual(MOD.resolve_project(str(PROJECTS_ROOT / "front"), PROJECTS, HOME, no_git), FRONT_ID)
        self.assertEqual(
            MOD.resolve_project(str(PROJECTS_ROOT / "front" / "src" / "app"), PROJECTS, HOME, no_git), FRONT_ID
        )
        # 이름이 앞부분만 같은 다른 폴더는 하위가 아니다
        self.assertIsNone(MOD.resolve_project(str(PROJECTS_ROOT / "frontend"), PROJECTS, HOME, no_git))

    def test_orca_workspace_maps_by_repo_name(self) -> None:
        cwd = str(HOME / "orca" / "workspaces" / "app" / "login-hero")
        self.assertEqual(MOD.resolve_project(cwd, PROJECTS, HOME, no_git), APP_ID)
        unknown = str(HOME / "orca" / "workspaces" / "other" / "x")
        self.assertIsNone(MOD.resolve_project(unknown, PROJECTS, HOME, no_git))

    def test_git_common_dir_parent_matches_root(self) -> None:
        cwd = str(HOME / ".codex" / "worktrees" / "fix-x" / "front")
        common = str(PROJECTS_ROOT / "front" / ".git")
        self.assertEqual(MOD.resolve_project(cwd, PROJECTS, HOME, lambda _c: common), FRONT_ID)
        elsewhere = str(HOME / "elsewhere" / ".git")
        self.assertIsNone(MOD.resolve_project(cwd, PROJECTS, HOME, lambda _c: elsewhere))

    def test_parent_folder_of_roots_is_not_a_project(self) -> None:
        self.assertIsNone(MOD.resolve_project(str(PROJECTS_ROOT), PROJECTS, HOME, no_git))


class ClassifyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "state_7.sqlite"
        (root / "state_3.sqlite").write_bytes(b"")
        make_db(
            self.db,
            [
                ("t-front", str(PROJECTS_ROOT / "front"), 0, None, 100, 100000),
                ("t-app-bound", str(PROJECTS_ROOT / "app"), 0, APP_ID, 200, 200000),
                ("t-orca", str(HOME / "orca" / "workspaces" / "app" / "x"), 0, None, 300, 300000),
                ("t-conflict", str(PROJECTS_ROOT / "front"), 0, None, 400, 400000),
                ("t-projectless", str(PROJECTS_ROOT / "front"), 0, None, 500, 500000),
                ("t-archived", str(PROJECTS_ROOT / "app"), 1, None, 600, 600000),
                ("t-outside", str(HOME / "other"), 0, None, 700, 700000),
            ],
        )
        self.global_state = root / ".codex-global-state.json"
        self.global_state.write_text(
            json.dumps(
                {
                    "local-projects": {
                        "l-front": {
                            "id": "l-front",
                            "name": "front",
                            "rootPaths": [str(PROJECTS_ROOT / "front")],
                        },
                        "l-app": {
                            "id": "l-app",
                            "name": "app",
                            "rootPaths": [str(PROJECTS_ROOT / "app")],
                        },
                    },
                    "thread-project-assignments": {
                        "t-front": {"projectKind": "local", "projectId": "l-front"},
                        "t-conflict": {"projectKind": "local", "projectId": "l-app"},
                    },
                    "projectless-thread-ids": ["t-projectless"],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _report(self, include_archived: bool = False, include_projectless: bool = False) -> dict:
        conn = MOD.open_readonly(self.db)
        try:
            projects = MOD.load_projects(conn)
            threads = MOD.load_threads(conn, include_archived)
        finally:
            conn.close()
        desktop = MOD.load_desktop_state(self.global_state)
        return MOD.classify(threads, projects, desktop, HOME, include_projectless, no_git)

    def test_find_state_db_picks_highest_number(self) -> None:
        self.assertEqual(MOD.find_state_db(Path(self.tmp.name)), self.db)

    def test_default_excludes_archived_and_projectless_and_flags_conflict(self) -> None:
        report = self._report()
        self.assertEqual(sorted(i["id"] for i in report["unbound"]), ["t-front", "t-orca"])
        self.assertEqual([i["id"] for i in report["bound"]], ["t-app-bound"])
        self.assertEqual([i["id"] for i in report["conflicts"]], ["t-conflict"])
        self.assertIn("app", report["conflicts"][0]["reason"])
        self.assertEqual([i["id"] for i in report["projectless_excluded"]], ["t-projectless"])
        self.assertEqual([i["id"] for i in report["skipped"]], ["t-outside"])
        self.assertEqual(
            sorted(i["id"] for i in report["desktop_unbound"]),
            ["t-app-bound", "t-orca"],
        )
        self.assertEqual(report["projects"][FRONT_ID], {"name": "front", "bound": 0, "unbound": 1})
        self.assertEqual(report["projects"][APP_ID], {"name": "app", "bound": 1, "unbound": 1})

    def test_include_flags(self) -> None:
        report = self._report(include_archived=True, include_projectless=True)
        ids = sorted(i["id"] for i in report["unbound"])
        self.assertEqual(ids, ["t-archived", "t-front", "t-orca", "t-projectless"])
        self.assertEqual(report["projectless_excluded"], [])

    def test_render_and_json(self) -> None:
        report = self._report()
        text = MOD.render_report(report, HOME, self.db)
        self.assertIn("| front | 0 | 1 |", text)
        self.assertIn("t-conflict", text)
        self.assertIn("~/orca/workspaces/app/x", text)
        payload = MOD.report_to_json(report)
        self.assertEqual(payload["projectless_excluded"], ["t-projectless"])
        self.assertEqual(payload["unbound"][0]["project_name"], "app")  # 최신순
        json.dumps(payload, ensure_ascii=False)

    def test_main_exit_code(self) -> None:
        argv = ["--check", "--state", str(self.db), "--global-state", str(self.global_state), "--json", "--home", str(HOME)]
        with mock.patch.object(MOD, "git_common_dir", no_git):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                code = MOD.main(argv)
        self.assertEqual(code, 1)
        parsed = json.loads(out.getvalue())
        self.assertEqual(len(parsed["unbound"]), 2)

    def test_main_thread_id_limits_scope(self) -> None:
        argv = [
            "--check",
            "--state",
            str(self.db),
            "--global-state",
            str(self.global_state),
            "--thread-id",
            "t-app-bound",
            "--json",
            "--home",
            str(HOME),
        ]
        with mock.patch.object(MOD, "git_common_dir", no_git):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                code = MOD.main(argv)
        self.assertEqual(code, 1)  # 앱서버는 귀속됐지만 데스크톱 배치표가 비어 있다.
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["db_threads"], 1)
        self.assertEqual([item["id"] for item in parsed["desktop_unbound"]], ["t-app-bound"])


class FakeServer:
    def __init__(
        self,
        listed: list[dict],
        fail_thread: str | None = None,
        on_update: object | None = None,
        on_read: object | None = None,
        fail_rollback: bool = False,
    ) -> None:
        self.listed = listed
        self.fail_thread = fail_thread
        self.on_update = on_update
        self.on_read = on_read
        self.fail_rollback = fail_rollback
        self.calls: list[tuple[str, dict]] = []
        self.assigned: dict[str, str | None] = {}
        self.read_count = 0

    def initialize(self) -> dict:
        self.calls.append(("initialize", {}))
        return {}

    def call(self, method: str, params: dict | None = None, timeout: float = 0) -> dict:
        params = params or {}
        self.calls.append((method, params))
        if method == "project/list":
            return {"data": self.listed}
        if method == "thread/metadata/update":
            if params["threadId"] == self.fail_thread:
                raise RuntimeError("update rejected")
            if self.fail_rollback and params["projectId"] == "":
                raise RuntimeError("rollback rejected")
            self.assigned[params["threadId"]] = params["projectId"] or None
            if callable(self.on_update):
                self.on_update(params)
            return {}
        if method == "thread/read":
            self.read_count += 1
            if callable(self.on_read):
                self.on_read(params, self.read_count)
            return {"thread": {"id": params["threadId"], "projectId": self.assigned.get(params["threadId"])}}
        raise AssertionError(method)


class FixTest(unittest.TestCase):
    def _listed(self) -> list[dict]:
        return [
            {"id": pid, "name": p["name"], "roots": [{"path": r} for r in p["roots"]]}
            for pid, p in PROJECTS.items()
        ]

    def _report(self) -> dict:
        return {
            "unbound": [
                {"id": "t1", "derived_project_id": FRONT_ID, "derived_project_name": "front"},
                {"id": "t2", "derived_project_id": APP_ID, "derived_project_name": "app"},
            ]
        }

    def test_fix_updates_and_verifies(self) -> None:
        server = FakeServer(self._listed(), fail_thread="t2")
        results = MOD.run_fix(self._report(), PROJECTS, server)
        self.assertEqual([(r["id"], r["ok"]) for r in results], [("t1", True), ("t2", False)])
        self.assertEqual(server.calls[0][0], "initialize")
        self.assertEqual(server.calls[1][0], "project/list")
        self.assertIn(("thread/metadata/update", {"threadId": "t1", "projectId": FRONT_ID}), server.calls)
        self.assertIn(("thread/read", {"threadId": "t1", "includeTurns": False}), server.calls)
        self.assertIn("통과 · bound", MOD.render_fix(results))

    def test_fix_aborts_when_projects_differ(self) -> None:
        listed = self._listed()
        listed[0]["roots"] = [{"path": "/somewhere/else"}]
        server = FakeServer(listed)
        with self.assertRaisesRegex(RuntimeError, "다르다"):
            MOD.run_fix(self._report(), PROJECTS, server)
        self.assertFalse(any(m == "thread/metadata/update" for m, _ in server.calls))

    def test_fix_never_moves_explicit_projectless_thread(self) -> None:
        server = FakeServer(self._listed())
        desktop = {"projectless": {"t1"}}
        results = MOD.run_fix(self._report(), PROJECTS, server, desktop=desktop)
        first = next(result for result in results if result["id"] == "t1")
        self.assertFalse(first["ok"])
        self.assertIn("건드리지 않음", first["detail"])
        self.assertNotIn(
            ("thread/metadata/update", {"threadId": "t1", "projectId": FRONT_ID}),
            server.calls,
        )

    def test_desktop_race_rolls_back_appserver_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            base = {
                "local-projects": {
                    "l-front": {
                        "id": "l-front",
                        "name": "front",
                        "rootPaths": [str(PROJECTS_ROOT / "front")],
                    }
                }
            }
            path.write_text(json.dumps(base), encoding="utf-8")

            def mark_projectless(params: dict) -> None:
                if params["projectId"] != FRONT_ID:
                    return
                changed = json.loads(path.read_text(encoding="utf-8"))
                changed["projectless-thread-ids"] = ["t1"]
                path.write_text(json.dumps(changed), encoding="utf-8")

            server = FakeServer(self._listed(), on_update=mark_projectless)
            results = MOD.run_fix(
                {"unbound": [self._report()["unbound"][0]]},
                PROJECTS,
                server,
                desktop_path=path,
                desktop=MOD.load_desktop_state(path),
            )

        self.assertFalse(results[0]["ok"])
        self.assertIn("projectless; appserver-rolled-back", results[0]["detail"])
        self.assertIsNone(server.assigned["t1"])
        self.assertIn(
            ("thread/metadata/update", {"threadId": "t1", "projectId": ""}),
            server.calls,
        )

    def test_rollback_failure_reports_partial_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            base = {
                "local-projects": {
                    "l-front": {
                        "id": "l-front",
                        "name": "front",
                        "rootPaths": [str(PROJECTS_ROOT / "front")],
                    }
                }
            }
            path.write_text(json.dumps(base), encoding="utf-8")

            def mark_projectless(params: dict) -> None:
                if params["projectId"] == FRONT_ID:
                    changed = json.loads(path.read_text(encoding="utf-8"))
                    changed["projectless-thread-ids"] = ["t1"]
                    path.write_text(json.dumps(changed), encoding="utf-8")

            server = FakeServer(
                self._listed(), on_update=mark_projectless, fail_rollback=True
            )
            results = MOD.run_fix(
                {"unbound": [self._report()["unbound"][0]]},
                PROJECTS,
                server,
                desktop_path=path,
                desktop=MOD.load_desktop_state(path),
            )

        self.assertFalse(results[0]["ok"])
        self.assertIn("partial-change:projectless", results[0]["detail"])
        self.assertIn("rollback-error:rollback rejected", results[0]["detail"])
        self.assertEqual(server.assigned["t1"], FRONT_ID)

    def test_final_desktop_recheck_catches_flush_after_appserver_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            base = {
                "local-projects": {
                    "l-front": {
                        "id": "l-front",
                        "name": "front",
                        "rootPaths": [str(PROJECTS_ROOT / "front")],
                    }
                }
            }
            path.write_text(json.dumps(base), encoding="utf-8")

            def flush_on_final_read(_params: dict, read_count: int) -> None:
                if read_count == 3:
                    path.write_text(json.dumps(base), encoding="utf-8")

            server = FakeServer(self._listed(), on_read=flush_on_final_read)
            results = MOD.run_fix(
                {"unbound": [self._report()["unbound"][0]]},
                PROJECTS,
                server,
                desktop_path=path,
                desktop=MOD.load_desktop_state(path),
            )
            final_data = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(results[0]["ok"])
        self.assertIn(
            "desktop-final-missing; appserver-rolled-back", results[0]["detail"]
        )
        self.assertIsNone(server.assigned["t1"])
        self.assertNotIn("t1", final_data.get("thread-project-assignments", {}))

    def test_final_desktop_recheck_reports_rollback_failure_as_partial_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            base = {
                "local-projects": {
                    "l-front": {
                        "id": "l-front",
                        "name": "front",
                        "rootPaths": [str(PROJECTS_ROOT / "front")],
                    }
                }
            }
            path.write_text(json.dumps(base), encoding="utf-8")

            def flush_on_final_read(_params: dict, read_count: int) -> None:
                if read_count == 3:
                    path.write_text(json.dumps(base), encoding="utf-8")

            server = FakeServer(
                self._listed(),
                on_read=flush_on_final_read,
                fail_rollback=True,
            )
            results = MOD.run_fix(
                {"unbound": [self._report()["unbound"][0]]},
                PROJECTS,
                server,
                desktop_path=path,
                desktop=MOD.load_desktop_state(path),
            )

        self.assertFalse(results[0]["ok"])
        self.assertIn("partial-change:desktop-final-missing", results[0]["detail"])
        self.assertIn("rollback-error:rollback rejected", results[0]["detail"])
        self.assertEqual(server.assigned["t1"], FRONT_ID)

    def _front_state(self, path: Path) -> None:
        path.write_text(json.dumps({"local-projects": {"l-front": {
            "id": "l-front", "name": "front", "rootPaths": [str(PROJECTS_ROOT / "front")]}}}), encoding="utf-8")

    def test_desktop_write_exception_still_rolls_back_appserver(self) -> None:
        """앱서버를 바꾼 뒤 데스크톱 파일이 깨져 예외가 나도 보상한다(재시도는 보상 못 함)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            self._front_state(path)

            def corrupt(params: dict) -> None:
                if params["projectId"] == FRONT_ID:
                    path.write_text("{깨진 JSON", encoding="utf-8")

            server = FakeServer(self._listed(), on_update=corrupt)
            results = MOD.run_fix(
                {"unbound": [self._report()["unbound"][0]]},
                PROJECTS,
                server,
                desktop_path=path,
                desktop={"projectless": set(), "assignments": {}, "projects": {}},
            )

        self.assertFalse(results[0]["ok"])
        self.assertIn("desktop-error:", results[0]["detail"])
        self.assertIn("appserver-rolled-back", results[0]["detail"])
        self.assertIsNone(server.assigned["t1"])

    def test_rollback_accepts_empty_string_as_unassigned(self) -> None:
        class EmptyUnassignServer(FakeServer):
            def call(self, method: str, params: dict | None = None, timeout: float = 0) -> dict:
                out = super().call(method, params, timeout)
                if method == "thread/read" and out["thread"]["projectId"] is None:
                    out["thread"]["projectId"] = ""
                return out

        server = EmptyUnassignServer(self._listed())
        server.assigned["t1"] = FRONT_ID
        self.assertEqual(MOD.rollback_appserver_assignment(server, "t1", FRONT_ID, None), "rolled-back")


class DesktopAssignmentWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".codex-global-state.json"
        self.base = {
            "unrelated": {"kept": True},
            "local-projects": {
                "l-front": {
                    "id": "l-front",
                    "name": "front",
                    "rootPaths": [str(PROJECTS_ROOT / "front")],
                },
                "l-app": {
                    "id": "l-app",
                    "name": "app",
                    "rootPaths": [str(PROJECTS_ROOT / "app")],
                },
            },
            "thread-project-assignments": {
                "other": {"projectKind": "local", "projectId": "l-front"}
            },
        }

    def write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.path.chmod(0o640)

    def test_atomic_update_preserves_mode_and_unrelated_changes(self) -> None:
        self.write(self.base)
        result = MOD.write_desktop_assignment(self.path, "t-app", PROJECTS[APP_ID])
        self.assertEqual(result, "desktop-bound")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["unrelated"], {"kept": True})
        self.assertEqual(data["thread-project-assignments"]["other"]["projectId"], "l-front")
        self.assertEqual(
            data["thread-project-assignments"]["t-app"],
            {"projectKind": "local", "projectId": "l-app"},
        )
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_projectless_and_existing_conflict_are_untouched(self) -> None:
        projectless = dict(self.base)
        projectless["projectless-thread-ids"] = ["t-app"]
        self.write(projectless)
        before = self.path.read_bytes()
        self.assertEqual(
            MOD.write_desktop_assignment(self.path, "t-app", PROJECTS[APP_ID]),
            "projectless",
        )
        self.assertEqual(self.path.read_bytes(), before)

        conflict = dict(self.base)
        conflict["thread-project-assignments"] = {
            "t-app": {"projectKind": "local", "projectId": "l-front"}
        }
        self.write(conflict)
        before = self.path.read_bytes()
        self.assertEqual(
            MOD.write_desktop_assignment(self.path, "t-app", PROJECTS[APP_ID]),
            "conflict-desktop",
        )
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_change_rechecks_latest_file_before_replace(self) -> None:
        self.write(self.base)
        checks = {"count": 0}

        def change_once(path: Path, expected: bytes, old_stat: os.stat_result) -> bool:
            checks["count"] += 1
            if checks["count"] == 1:
                changed = json.loads(path.read_text(encoding="utf-8"))
                changed["app-change"] = "preserved"
                path.write_text(json.dumps(changed), encoding="utf-8")
                path.chmod(0o640)
                return False
            return MOD._same_file_version(path, expected, old_stat)

        result = MOD.write_desktop_assignment(
            self.path,
            "t-app",
            PROJECTS[APP_ID],
            sleep_fn=lambda _seconds: None,
            version_check=change_once,
        )
        self.assertEqual(result, "desktop-bound")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["app-change"], "preserved")
        self.assertEqual(data["thread-project-assignments"]["t-app"]["projectId"], "l-app")


FAKE_CODEX = """#!/usr/bin/env python3
# 가짜 codex app-server: 알림과 응답을 한 번의 write 로 같이 보낸다(버퍼 있는 readline + select 재현용).
import json, sys
sys.stderr.write("fake app-server started\\n")
for line in sys.stdin:
    req = json.loads(line)
    method = req["method"]
    if method == "die":
        sys.stderr.write("boom: fatal\\n")
        sys.exit(3)
    if method == "initialize":
        result = {"userAgent": "fake"}
    elif method == "project/list":
        result = {"data": req["params"].get("expect", [])}
    else:
        result = {"echo": method}
    out = json.dumps({"jsonrpc": "2.0", "method": "notice/pre", "params": {"n": 1}}) + "\\n"
    out += json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\\n"
    out += json.dumps({"jsonrpc": "2.0", "method": "notice/post", "params": {"n": 2}}) + "\\n"
    sys.stdout.write(out)
    sys.stdout.flush()
"""


class AppServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.codex = Path(self.tmp.name) / "codex"
        self.codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_response_bundled_with_notifications_in_one_write(self) -> None:
        with MOD.AppServer(str(self.codex)) as server:
            self.assertEqual(server.initialize(), {"userAgent": "fake"})
            for _ in range(3):
                self.assertEqual(server.call("thread/read", {"threadId": "x"}, timeout=3), {"echo": "thread/read"})
            self.assertEqual(server.call("project/list", {"expect": [{"id": "p"}]}, timeout=3), {"data": [{"id": "p"}]})
        self.assertIsNotNone(server.proc.poll())

    def test_server_death_reports_stderr_head(self) -> None:
        server = MOD.AppServer(str(self.codex))
        try:
            with self.assertRaisesRegex(RuntimeError, r"(?s)앱서버가 닫혔다.*boom: fatal"):
                server.call("die", {}, timeout=5)
        finally:
            server.close()
        self.assertIsNotNone(server.proc.poll())

    def test_find_codex_prefers_explicit(self) -> None:
        self.assertEqual(MOD.find_codex(str(self.codex)), str(self.codex))


if __name__ == "__main__":
    unittest.main()
