#!/usr/bin/env python3
"""codex-bind-session.py 훅의 판정·유도·귀속(앱서버 mock) 검증."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
PLUGIN_SCRIPTS = SCRIPTS.parent / "harness" / "skeleton" / "plugin" / "scripts"
SCRIPT = PLUGIN_SCRIPTS / "codex-bind-session.py"
SPEC = importlib.util.spec_from_file_location("codex_bind_session", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

BINDING = MOD.load_binding_module()

HOME = Path("/home/tester")
PROJECTS_ROOT = HOME / "work" / "projects"
FRONT_ID = "p-front"
APP_ID = "p-app"
PROJECTS = {
    FRONT_ID: {"id": FRONT_ID, "name": "front", "roots": [str(PROJECTS_ROOT / "front")]},
    APP_ID: {"id": APP_ID, "name": "app", "roots": [str(PROJECTS_ROOT / "app")]},
}


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE project_roots (project_id TEXT, position INTEGER, path TEXT);
        CREATE TABLE threads (
            id TEXT PRIMARY KEY, cwd TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '', name TEXT, archived INTEGER NOT NULL DEFAULT 0,
            project_id TEXT, updated_at INTEGER NOT NULL DEFAULT 0, updated_at_ms INTEGER
        );
        """
    )
    for pid, project in PROJECTS.items():
        conn.execute("INSERT INTO projects VALUES (?, ?, 0)", (pid, project["name"]))
        for root in project["roots"]:
            conn.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (pid, root))
    conn.commit()
    conn.close()


def listed_projects() -> list[dict]:
    return [
        {"id": pid, "name": p["name"], "roots": [{"path": r} for r in p["roots"]]}
        for pid, p in PROJECTS.items()
    ]


class FakeServer:
    """codex-project-binding 의 FakeServer 를 본떠 최소로."""

    def __init__(
        self,
        listed: list[dict],
        current: str | None = None,
        fail: bool = False,
        on_update: object | None = None,
    ) -> None:
        self.listed = listed
        self.current = current
        self.fail = fail
        self.on_update = on_update
        self.calls: list[tuple[str, dict]] = []
        self.assigned: dict[str, str | None] = {}
        self.closed = False

    def initialize(self) -> dict:
        self.calls.append(("initialize", {}))
        return {}

    def call(self, method: str, params: dict | None = None, timeout: float = 0) -> dict:
        params = params or {}
        self.calls.append((method, params))
        if self.fail:
            raise RuntimeError("app-server down")
        if method == "project/list":
            return {"data": self.listed}
        if method == "thread/read":
            tid = params["threadId"]
            return {"thread": {"id": tid, "projectId": self.assigned.get(tid, self.current)}}
        if method == "thread/metadata/update":
            self.assigned[params["threadId"]] = params["projectId"] or None
            if callable(self.on_update):
                self.on_update(params)
            return {}
        raise AssertionError(method)

    def close(self) -> None:
        self.closed = True


class RejectingFactory:
    """server_factory 가 호출되면 실패시킨다(호출되면 안 되는 경우 검증)."""

    def __init__(self) -> None:
        self.called = False

    def __call__(self) -> object:
        self.called = True
        raise AssertionError("server_factory 가 호출되면 안 된다")


class IsCodexSessionTest(unittest.TestCase):
    def test_claude_env_is_not_codex(self) -> None:
        env = {"CLAUDE_PROJECT_DIR": "/x", "PLUGIN_ROOT": "/y"}
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/x"}
        self.assertFalse(MOD.is_codex_session(payload, env))

    def test_codex_env_is_codex(self) -> None:
        env = {"CODEX_SESSION_ID": "s1"}
        self.assertTrue(MOD.is_codex_session({"cwd": "/x"}, env))

    def test_codex_payload_is_codex(self) -> None:
        self.assertTrue(MOD.is_codex_session({"session_id": "s1", "cwd": "/x"}, {}))

    def test_no_session_id_is_not_codex(self) -> None:
        self.assertFalse(MOD.is_codex_session({"cwd": "/x"}, {}))


class RunHookTest(unittest.TestCase):
    def test_claude_session_does_not_spawn(self) -> None:
        spawned: list[tuple[str, str]] = []
        loader_called = {"n": 0}

        def loader() -> object:
            loader_called["n"] += 1
            return BINDING

        code = MOD.run_hook(
            {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/x"},
            {"CLAUDE_PROJECT_DIR": "/x"},
            spawn=lambda s, c: spawned.append((s, c)),
            mod_loader=loader,
        )
        self.assertEqual(code, 0)
        self.assertEqual(spawned, [])
        self.assertEqual(loader_called["n"], 0)

    def test_codex_session_spawns_binder(self) -> None:
        spawned: list[tuple[str, str]] = []

        class StubMod:
            @staticmethod
            def find_codex(_explicit: object) -> str:
                return "/fake/codex"

        code = MOD.run_hook(
            {"session_id": "s1", "cwd": "/work/app"},
            {"CODEX_SESSION_ID": "s1"},
            spawn=lambda s, c: spawned.append((s, c)),
            mod_loader=lambda: StubMod(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(spawned, [("s1", "/work/app")])

    def test_codex_session_without_codex_binary_does_not_spawn(self) -> None:
        spawned: list[tuple[str, str]] = []

        class NoCodexMod:
            @staticmethod
            def find_codex(_explicit: object) -> str:
                raise FileNotFoundError("no codex")

        # CODEX_APP_BIN 이 없는 기기에서만 이 경로가 유효하다.
        if Path(MOD.CODEX_APP_BIN).is_file():
            self.skipTest("이 기기에는 Codex 앱 바이너리가 있어 find_codex 로 내려가지 않는다")
        code = MOD.run_hook(
            {"session_id": "s1", "cwd": "/work/app"},
            {"CODEX_SESSION_ID": "s1"},
            spawn=lambda s, c: spawned.append((s, c)),
            mod_loader=lambda: NoCodexMod(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(spawned, [])


class BindWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.codex_home = Path(self.tmp.name)
        make_db(self.codex_home / "state_5.sqlite")
        self.global_state = self.codex_home / ".codex-global-state.json"
        self.write_desktop(
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
                }
            }
        )

    def write_desktop(self, data: dict) -> None:
        self.global_state.write_text(json.dumps(data), encoding="utf-8")

    def test_unmatched_cwd_skips_appserver(self) -> None:
        factory = RejectingFactory()
        status = MOD.bind_worker(
            "s-x",
            str(HOME / "somewhere" / "else"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=factory,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "no-project")
        self.assertFalse(factory.called)

    def test_orca_workspace_cwd_binds_to_project(self) -> None:
        cwd = str(HOME / "orca" / "workspaces" / "app" / "login-hero")
        server = FakeServer(listed_projects(), current=None)
        status = MOD.bind_worker(
            "s-app",
            cwd,
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "bound")
        self.assertEqual(server.assigned["s-app"], APP_ID)
        self.assertTrue(server.closed)
        desktop = json.loads(self.global_state.read_text(encoding="utf-8"))
        self.assertEqual(
            desktop["thread-project-assignments"]["s-app"],
            {"projectKind": "local", "projectId": "l-app"},
        )

    def test_project_root_subpath_binds(self) -> None:
        cwd = str(PROJECTS_ROOT / "front" / "src")
        server = FakeServer(listed_projects(), current=None)
        status = MOD.bind_worker(
            "s-front",
            cwd,
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "bound")
        self.assertEqual(server.assigned["s-front"], FRONT_ID)

    def test_already_bound_is_noop(self) -> None:
        cwd = str(PROJECTS_ROOT / "app")
        server = FakeServer(listed_projects(), current=APP_ID)
        status = MOD.bind_worker(
            "s-app",
            cwd,
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "bound")
        self.assertNotIn("thread/metadata/update", [m for m, _ in server.calls])
        desktop = json.loads(self.global_state.read_text(encoding="utf-8"))
        self.assertEqual(
            desktop["thread-project-assignments"]["s-app"]["projectId"], "l-app"
        )

    def test_appserver_conflict_is_untouched(self) -> None:
        cwd = str(PROJECTS_ROOT / "app")
        server = FakeServer(listed_projects(), current=FRONT_ID)  # 앱서버가 다른 프로젝트로 봄
        status = MOD.bind_worker(
            "s-app",
            cwd,
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "conflict-appserver")
        self.assertNotIn("thread/metadata/update", [m for m, _ in server.calls])

    def test_desktop_race_rolls_back_background_appserver_change(self) -> None:
        def mark_projectless(params: dict) -> None:
            if params["projectId"] != APP_ID:
                return
            changed = json.loads(self.global_state.read_text(encoding="utf-8"))
            changed["projectless-thread-ids"] = ["s-app"]
            self.global_state.write_text(json.dumps(changed), encoding="utf-8")

        server = FakeServer(
            listed_projects(), current=None, on_update=mark_projectless
        )
        status = MOD.bind_worker(
            "s-app",
            str(PROJECTS_ROOT / "app"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "projectless; appserver-rolled-back")
        self.assertIsNone(server.assigned["s-app"])

    def test_desktop_projectless_is_untouched(self) -> None:
        self.write_desktop(
            {
                "local-projects": {
                    "l-app": {
                        "id": "l-app",
                        "name": "app",
                        "rootPaths": [str(PROJECTS_ROOT / "app")],
                    }
                },
                "projectless-thread-ids": ["s-app"],
            }
        )
        factory = RejectingFactory()
        status = MOD.bind_worker(
            "s-app",
            str(PROJECTS_ROOT / "app"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=factory,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "projectless")
        self.assertFalse(factory.called)

    def test_desktop_conflict_is_untouched(self) -> None:
        self.write_desktop(
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
                    "s-app": {"projectKind": "local", "projectId": "l-front"}
                },
            }
        )
        factory = RejectingFactory()
        status = MOD.bind_worker(
            "s-app",
            str(PROJECTS_ROOT / "app"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=factory,
            sleep_fn=lambda _s: None,
            delay=0,
        )
        self.assertEqual(status, "conflict-desktop")
        self.assertFalse(factory.called)

    def test_dead_appserver_gives_up_quietly(self) -> None:
        cwd = str(PROJECTS_ROOT / "app")
        sleeps: list[float] = []
        server = FakeServer(listed_projects(), fail=True)
        status = MOD.bind_worker(
            "s-app",
            cwd,
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=lambda: server,
            sleep_fn=sleeps.append,
            delay=0,
            retries=3,
        )
        self.assertTrue(status.startswith("error:"))
        # delay=0 은 sleep 안 함, 재시도 사이 backoff 만 2번.
        self.assertEqual(len(sleeps), 2)

    def test_thread_ready_after_old_three_try_window_is_bound(self) -> None:
        attempts = {"count": 0}
        healthy = FakeServer(listed_projects(), current=None)

        def factory() -> FakeServer:
            attempts["count"] += 1
            if attempts["count"] <= 3:
                return FakeServer(listed_projects(), fail=True)
            return healthy

        status = MOD.bind_worker(
            "s-late",
            str(PROJECTS_ROOT / "app"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=factory,
            sleep_fn=lambda _s: None,
            delay=0,
            retries=4,
        )
        self.assertEqual(status, "bound")
        self.assertEqual(attempts["count"], 4)

    def test_transient_desktop_json_read_is_retried(self) -> None:
        original = BINDING.load_desktop_state
        attempts = {"count": 0}

        def flaky(path: Path) -> dict:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise json.JSONDecodeError("partial", "{", 1)
            return original(path)

        server = FakeServer(listed_projects(), current=None)
        with mock.patch.object(BINDING, "load_desktop_state", side_effect=flaky):
            status = MOD.bind_worker(
                "s-app",
                str(PROJECTS_ROOT / "app"),
                BINDING,
                self.codex_home,
                HOME,
                "/fake/codex",
                server_factory=lambda: server,
                sleep_fn=lambda _s: None,
                delay=0,
            )
        self.assertEqual(status, "bound")
        # 첫 읽기는 재시도되고, 성공 경로가 앱서버 최종 확인 뒤 데스크톱을 한 번 더 읽는다.
        self.assertEqual(attempts["count"], 3)

    def test_deadline_stops_retries(self) -> None:
        """재시도 15회 × 앱서버 시간 초과로 수십 분 머물지 않게 전체 마감에서 멈춘다."""
        now = {"t": 0.0}
        made = {"n": 0}

        def factory() -> FakeServer:
            made["n"] += 1
            now["t"] += 50.0  # 시도 하나가 50초 걸린 것처럼
            return FakeServer(listed_projects(), fail=True)

        status = MOD.bind_worker(
            "s-app",
            str(PROJECTS_ROOT / "app"),
            BINDING,
            self.codex_home,
            HOME,
            "/fake/codex",
            server_factory=factory,
            sleep_fn=lambda _s: None,
            delay=0,
            retries=15,
            deadline=120.0,
            clock=lambda: now["t"],
        )
        self.assertTrue(status.startswith("deadline-exceeded"))
        self.assertEqual(made["n"], 3)

    def test_log_keeps_background_result_with_private_permissions(self) -> None:
        log = self.codex_home / "logs" / "bind.log"
        MOD.log_result(log, "s1", "/work/app", "desktop-error: changed")
        self.assertIn("s1\tdesktop-error: changed\t/work/app", log.read_text())
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


class HookRobustnessTest(unittest.TestCase):
    def test_loader_syntax_error_does_not_fail_hook(self) -> None:
        def broken() -> object:
            raise SyntaxError("깨진 원본")

        with mock.patch("sys.stderr", io.StringIO()):
            code = MOD.run_hook(
                {"session_id": "s1", "cwd": "/work/app"},
                {"CODEX_SESSION_ID": "s1"},
                spawn=lambda s, c: self.fail("spawn 하면 안 된다"),
                mod_loader=broken,
            )
        self.assertEqual(code, 0)

    def test_spawn_logs_pid_and_keeps_stderr(self) -> None:
        """바인더가 로그 없이 사라졌던 일(10-09~10-10)을 다시 진단할 수 있게 띄운 기록과 stderr 를 남긴다."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "logs" / "bind.log"
            popen_kwargs: dict = {}

            class FakeProc:
                pid = 4242

            def fake_popen(cmd: list, **kwargs: object) -> FakeProc:
                popen_kwargs.update(kwargs, cmd=cmd)
                return FakeProc()

            with mock.patch.dict("os.environ", {"HARNESS_CODEX_BIND_LOG": str(log), "CODEX_HOME": tmp}), \
                    mock.patch.object(MOD.subprocess, "Popen", side_effect=fake_popen):
                MOD.spawn_binder("s1", "/work/app")
            self.assertIn("s1\tspawned pid=4242\t/work/app", log.read_text())
            self.assertIsInstance(popen_kwargs["stderr"], int)
            self.assertIn("-B", popen_kwargs["cmd"])
            err_log = log.with_suffix(".stderr.log")
            self.assertEqual(stat.S_IMODE(err_log.stat().st_mode), 0o600)


class BindingPathTest(unittest.TestCase):
    """codex_project_binding.py 를 기기별 절대경로 없이 같은 폴더 → HARNESS_BINDING_DIR 순으로 찾는다."""

    def test_plugin_local_copy_is_first(self) -> None:
        candidates = MOD._binding_candidates()
        self.assertEqual(candidates[0], PLUGIN_SCRIPTS / "codex_project_binding.py")
        self.assertTrue(candidates[0].is_file())

    def test_env_candidate_is_last(self) -> None:
        with mock.patch.dict("os.environ", {"HARNESS_BINDING_DIR": "/opt/x"}):
            self.assertEqual(MOD._binding_candidates()[-1], Path("/opt/x/codex_project_binding.py"))

    def test_missing_module_prints_one_stderr_line(self) -> None:
        def missing() -> object:
            raise ImportError("codex_project_binding.py 를 못 찾음")

        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code = MOD.run_hook(
                {"session_id": "s1", "cwd": "/work/app"},
                {"CODEX_SESSION_ID": "s1"},
                spawn=lambda s, c: self.fail("spawn 하면 안 된다"),
                mod_loader=missing,
            )
        self.assertEqual(code, 0)
        lines = err.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("귀속 건너뜀", lines[0])


if __name__ == "__main__":
    unittest.main()
