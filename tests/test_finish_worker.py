"""작업자 정리(finish_worker.py): 「오프라인 = 끝난 작업」 불변식과 안전 조건.

작업 중 · 응답 대기 · 모르는 상태 · 종료 신호를 무시하는 에이전트는 절대 끝내지 않고, 워크트리도 지우지 않는다.
Claude 세션은 임시 HOME 의 ~/.claude/sessions/<pid>.json 과 실제 자식 프로세스(sleep)로 흉내 낸다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import Sandbox

SLEEPER = "import time; time.sleep(120)"
STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"


@unittest.skipIf(os.name == "nt", "SIGTERM 흉내는 posix 에서만")
class FinishWorker(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.fake_tools(("orca", "codex"))
        self.ws = self.sb.tmp / "orca-ws"
        cfg = self.sb.config(["claude", "codex"], True)  # orca.workspaces_dir = <tmp>/orca-ws
        self.harness = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.harness), "--offline",
                              "--non-interactive", "--skip-install")
        assert r.returncode == 0, r.stdout + r.stderr
        self.repo = self.sb.projects / "web"   # 로컬 원격에서 clone 된 저장소
        self.env = self.sb.env()
        self.procs = []

    def tearDown(self):
        for p in self.procs:  # 시험 정리(스크립트가 아니라 시험이 끝내는 것)
            if p.poll() is None:
                p.kill()
                p.wait()

    def worktree(self, name="task", under_ws=False):
        base = self.ws if under_ws else self.sb.tmp / "work"
        base.mkdir(parents=True, exist_ok=True)
        wt = base / name
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", name, str(wt), "origin/develop"],
                       check=True, env=self.env)
        return wt

    def claude_exe(self, code):
        """이름이 claude 인 가짜 에이전트 실행 파일(ps 의 명령에 .../claude 로 보인다)."""
        d = self.sb.tmp / "agent-bin" / str(len(self.procs))
        d.mkdir(parents=True, exist_ok=True)
        exe = d / "claude"
        exe.write_text(f"#!{sys.executable}\n{code}\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def spawn(self, argv, cwd):
        p = subprocess.Popen(argv, cwd=str(cwd))
        self.procs.append(p)
        return p

    def write_session(self, pid, cwd, status, name="작업-가", raw=None):
        d = self.sb.home / ".claude" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else json.dumps({"pid": pid, "cwd": str(cwd), "status": status, "name": name})
        (d / f"{pid}.json").write_text(text, encoding="utf-8")

    def session(self, cwd, status, code=SLEEPER, name="작업-가"):
        cwd = Path(cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        p = self.spawn([str(self.claude_exe(code))], cwd)
        self.write_session(p.pid, cwd, status, name)
        import time
        time.sleep(0.3)  # 파이썬이 뜨고(신호 설정까지) ps 에 보일 때까지
        return p

    def finish(self, wt, *args, cwd=None, **env):
        cwd = str(cwd or self.sb.tmp)
        e = self.sb.env(PWD=cwd, **env)
        e.pop("CLAUDE_PROJECT_DIR", None)
        return subprocess.run([sys.executable, str(self.harness / "harness/scripts/finish_worker.py"), str(wt), "--wait", "1", *args],
                              capture_output=True, text=True, env=e, cwd=cwd, timeout=60)

    def assertKept(self, r, wt, proc=None, why=""):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(why, r.stderr)
        self.assertTrue(wt.is_dir(), "멈춰야 하는데 워크트리를 지웠다")
        if proc is not None:
            self.assertIsNone(proc.poll(), "작업 중인 에이전트를 끝냈다")

    # ---------------------------------------------------------------- 불변식

    def test_idle_session_is_terminated_and_worktree_removed(self):
        wt = self.worktree()
        p = self.session(wt, "idle")
        r = self.finish(wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p.wait(timeout=5)
        self.assertEqual(p.returncode, -15, "SIGTERM 으로 끝나야 한다")
        self.assertFalse(wt.exists())
        self.assertIn("앱 보관 대상(Claude): 작업-가", r.stdout)
        branches = subprocess.run(["git", "-C", str(self.repo), "branch"], capture_output=True, text=True, env=self.env).stdout
        self.assertNotIn("task", branches)

    def test_busy_is_never_terminated(self):
        wt = self.worktree()
        p = self.session(wt, "busy")
        self.assertKept(self.finish(wt), wt, p, "쉬는 상태가 아니다(status=busy)")

    def test_waiting_or_unknown_status_is_never_terminated(self):
        for i, status in enumerate(("waiting", "awaiting_input", "", "shell")):
            wt = self.worktree(f"t{i}")
            p = self.session(wt / "sub" if i == 0 else wt, status)
            self.assertKept(self.finish(wt), wt, p, "쉬는 상태가 아니다")

    def test_mixed_sessions_kill_none(self):
        wt = self.worktree()
        idle = self.session(wt, "idle")
        busy = self.session(wt, "busy", name="작업-나")
        self.assertKept(self.finish(wt), wt, busy, "작업-나")
        self.assertIsNone(idle.poll(), "하나라도 작업 중이면 쉬는 세션도 끝내지 않는다")

    def test_stubborn_process_stops_without_sigkill(self):
        wt = self.worktree()
        p = self.session(wt, "idle", code=STUBBORN)
        self.assertKept(self.finish(wt), wt, p, "강제로 끝내지 않는다")

    # ---------------------------------------------------------------- 검토 반려 재현 A · B · C

    def test_A_unreadable_session_file_stops(self):
        wt = self.worktree()
        p = self.spawn([str(self.claude_exe(SLEEPER))], wt)
        self.write_session(p.pid, wt, "idle", raw='{"pid": %d, "cwd": "%s", "sta' % (p.pid, wt))  # 쓰다 만 JSON
        self.assertKept(self.finish(wt), wt, p, "세션 상태를 읽지 못했다")

    def test_A_bad_pid_or_missing_cwd_stops(self):
        for i, raw in enumerate(('{"pid": "123", "cwd": "/x", "status": "idle"}', '{"pid": 123, "status": "idle"}')):
            wt = self.worktree(f"a{i}")
            d = self.sb.home / ".claude" / "sessions"
            d.mkdir(parents=True, exist_ok=True)
            (d / "bad.json").write_text(raw, encoding="utf-8")
            self.assertKept(self.finish(wt), wt, None, "pid 가 정수가 아니거나 cwd 가 없다")
            (d / "bad.json").unlink()

    def test_B_reused_pid_is_not_signalled(self):
        wt = self.worktree()
        other = self.sb.tmp / "elsewhere"
        other.mkdir()
        sleeper = self.spawn(["sleep", "120"], other)  # 세션 파일의 pid 가 재사용돼 무관한 프로세스를 가리키는 경우
        self.write_session(sleeper.pid, wt, "idle")
        import time
        time.sleep(0.2)
        self.assertKept(self.finish(wt), wt, sleeper, "claude 프로세스가 아니다")

    def test_B_claude_but_cwd_elsewhere_is_not_signalled(self):
        wt = self.worktree()
        other = self.sb.tmp / "elsewhere"
        other.mkdir()
        p = self.spawn([str(self.claude_exe(SLEEPER))], other)
        self.write_session(p.pid, wt, "idle")
        import time
        time.sleep(0.3)
        self.assertKept(self.finish(wt), wt, p, "실제 작업 폴더가 워크트리 안이 아니다")

    def test_C_process_without_session_file_blocks_removal(self):
        wt = self.worktree()
        agent = self.spawn(["sleep", "120"], wt)  # 세션 파일 없이 워크트리에서 도는 에이전트
        import time
        time.sleep(0.2)
        self.assertKept(self.finish(wt), wt, agent, "세션 파일 없이 이 워크트리를 쓰는 프로세스")

    # ---------------------------------------------------------------- 잠금 · 브랜치

    def test_lock_held_by_live_process_stops(self):
        wt = self.worktree()
        lock = self.sb.tmp / "cleanup.lock"
        lock.write_text(json.dumps({"pid": os.getpid(), "started": "x"}), encoding="utf-8")
        r = self.finish(wt, HARNESS_CLEANUP_LOCK=str(lock))
        self.assertKept(r, wt, None, "다른 정리가 진행 중")

    def test_stale_lock_is_taken_over_and_released(self):
        wt = self.worktree()
        lock = self.sb.tmp / "cleanup.lock"
        lock.write_text(json.dumps({"pid": 999999, "started": "x"}), encoding="utf-8")
        r = self.finish(wt, HARNESS_CLEANUP_LOCK=str(lock))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(lock.exists(), "잠금을 풀지 않았다")

    def test_branch_with_commits_not_on_remote_is_kept(self):
        wt = self.worktree()
        # 워크트리 HEAD 는 원격과 같지만, 같은 브랜치에 원격에 없는 커밋을 따로 붙이면 -d · -D 둘 다 하지 않는다
        r = self.finish(wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        branches = subprocess.run(["git", "-C", str(self.repo), "branch"], capture_output=True, text=True, env=self.env).stdout
        self.assertNotIn("task", branches, "원격에 다 있는 브랜치는 지운다")

    # ---------------------------------------------------------------- 안전 조건

    def test_dirty_worktree(self):
        wt = self.worktree()
        (wt / "x.txt").write_text("x", encoding="utf-8")
        self.assertKept(self.finish(wt), wt, None, "커밋 안 된 변경")

    def test_unpushed_commit(self):
        wt = self.worktree()
        (wt / "x.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(wt), "add", "x.txt"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "x"], check=True, env=self.env)
        self.assertKept(self.finish(wt), wt, None, "원격에 없는 커밋 1개")

    def test_callers_own_worktree(self):
        wt = self.worktree()
        self.assertKept(self.finish(wt, cwd=wt), wt, None, "세션 자신의 워크트리")

    def test_main_checkout_refused(self):
        r = self.finish(self.repo)
        self.assertEqual(r.returncode, 1)
        self.assertIn("본 체크아웃", r.stderr)
        self.assertTrue(self.repo.is_dir())

    def test_dry_run_changes_nothing(self):
        wt = self.worktree()
        p = self.session(wt, "idle")
        r = self.finish(wt, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[미리보기]", r.stdout)
        self.assertIn("앱 보관 대상(Claude): 작업-가", r.stdout)
        self.assertIsNone(p.poll())
        self.assertTrue(wt.is_dir())

    # ---------------------------------------------------------------- Orca · Codex

    def test_orca_dispatch_not_settled_stops(self):
        wt = self.worktree(under_ws=True)
        r = self.finish(wt, "--dispatch", "ctx_1", FAKE_DISPATCH_STATUS="running")
        self.assertKept(r, wt, None, "끝난 상태가 아니다(running)")

    def test_orca_outside_workspaces_stops(self):
        wt = self.worktree()
        self.assertKept(self.finish(wt, "--dispatch", "ctx_1"), wt, None, "Orca 작업 폴더")

    def test_codex_archive_failure_warns_and_continues(self):
        wt = self.worktree(under_ws=True)
        sess = self.sb.home / ".codex" / "sessions" / "2026"
        sess.mkdir(parents=True)
        (sess / "a.jsonl").write_text(json.dumps({"payload": {"cwd": str(wt.resolve()), "id": "thread-1"}}) + "\n", encoding="utf-8")
        r = self.finish(wt, "--dispatch", "ctx_1", FAKE_CODEX_ARCHIVE="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("경고: Codex 세션 보관 실패", r.stderr)
        calls = self.sb.tool_calls()
        self.assertTrue(any(c["tool"] == "codex" and c["args"] == ["archive", "thread-1"] for c in calls))
        self.assertTrue(any(c["tool"] == "orca" and c["args"][:2] == ["worktree", "rm"] for c in calls))
        self.assertTrue(any(c["tool"] == "orca" and c["args"][:2] == ["orchestration", "worker-release"] for c in calls))


class GeneratedCleanupDocs(unittest.TestCase):
    def test_deploy_and_claude_md_have_app_session_cleanup(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        r = sb.bootstrap("run", "--config", str(sb.config(["claude"], False)), "--target", str(target),
                         "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        deploy = (target / "plugins/demo/skills/deploy/SKILL.md").read_text(encoding="utf-8")
        for must in ("오프라인 = 끝난 작업", "finish_worker.py", "claude.ai/code", "오프라인」인 세션만 보관", "삭제가 아니라 보관",
                     "브라우저 도구가 없으면", "동시에 정리 중이면 하지 않는다", "정확히 같고", "같은 제목이 둘 이상이면 어느 것도 보관하지 않고",
                     "cleanup.lock", "재사용된 pid"):
            self.assertIn(must, deploy)
        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("8. push 가 끝나면 `deploy` 스킬 「작업 공간 · 앱 세션 정리」", claude_md)


if __name__ == "__main__":
    unittest.main()
