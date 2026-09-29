"""생성된 훅이 설정 값대로 동작하는지: push 가드 · 규칙 보호 · 하네스 동기화 · Codex 귀속 훅(비 Codex 세션은 조용히)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import Sandbox


class GeneratedHooks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        cfg = cls.sb.config(["claude", "codex"], False)
        cls.target = cls.sb.projects / "orchestrator"
        r = cls.sb.bootstrap("run", "--config", str(cfg), "--target", str(cls.target), "--offline",
                             "--non-interactive", "--skip-install")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.plugin = cls.target / "plugins" / "demo"
        cls.web = cls.sb.projects / "web"
        subprocess.run(["git", "-C", str(cls.web), "remote", "set-url", "origin", "https://github.com/demo-org/web.git"],
                       check=True, env=cls.sb.env())

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def hook(self, script, payload, **env):
        e = self.sb.env(PLUGIN_ROOT=str(self.plugin), **env)
        r = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), capture_output=True,
                           text=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def push(self, command, cwd, **extra):
        payload = {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
        payload.update(extra)
        return self.hook(self.plugin / "scripts" / "guard-push.py", payload)

    def decision(self, out):
        return out and out["hookSpecificOutput"]["permissionDecision"]

    def test_force_push_denied(self):
        for cmd in ("git push --force origin develop", "git push -f", "git push origin +develop",
                    "git push --force-with-lease origin x"):
            self.assertEqual(self.decision(self.push(cmd, self.web)), "deny", cmd)

    def test_ask_branch_asks(self):
        out = self.push("git push origin develop:main", self.web)
        self.assertEqual(self.decision(out), "ask")
        self.assertIn("상용 배포", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("대표님", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_normal_push_passes(self):
        self.assertIsNone(self.push("git push origin develop", self.web))
        self.assertIsNone(self.push("git status", self.web))

    def test_cd_prefix_is_followed(self):
        out = self.push(f'cd "{self.web}" && git push origin HEAD:main', self.sb.tmp)
        self.assertEqual(self.decision(out), "ask")

    def test_codex_asks_become_deny_until_approved(self):
        out = self.push("git push origin develop:main", self.web, turn_id="t1")
        self.assertEqual(self.decision(out), "deny")
        self.assertIn("HARNESS_APPROVED_PUSH=1", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self.push("HARNESS_APPROVED_PUSH=1 git push origin develop:main", self.web, turn_id="t1"))

    def test_guard_rules_asks_in_shared_checkout_only(self):
        guard = self.target / "harness" / "hooks" / "guard-rules.py"
        env = self.sb.env(CLAUDE_PROJECT_DIR=str(self.target))
        payload = {"tool_name": "Edit", "cwd": str(self.target), "tool_input": {"file_path": str(self.target / "CLAUDE.md")}}
        r = subprocess.run([sys.executable, str(guard)], input=json.dumps(payload), capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")
        payload["tool_input"]["file_path"] = str(self.target / "상황판.md")
        r = subprocess.run([sys.executable, str(guard)], input=json.dumps(payload), capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "")

    def test_guard_rules_quiet_in_linked_worktree(self):
        env = self.sb.env()
        subprocess.run(["git", "-C", str(self.target), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.target), "commit", "-q", "-m", "init"], env=env)  # 이미 첫 커밋이 있으면 할 것 없음
        wt = self.sb.projects / ".work" / "orchestrator-x"
        subprocess.run(["git", "-C", str(self.target), "worktree", "add", "-q", str(wt), "-b", "x"], check=True, env=env)
        guard = wt / "harness" / "hooks" / "guard-rules.py"
        payload = {"tool_name": "Edit", "cwd": str(wt), "tool_input": {"file_path": str(wt / "CLAUDE.md")}}
        r = subprocess.run([sys.executable, str(guard)], input=json.dumps(payload), capture_output=True, text=True,
                           env=self.sb.env(CLAUDE_PROJECT_DIR=str(wt)))
        self.assertEqual(r.stdout.strip(), "")
        # 하네스 동기화: 추적 브랜치 없는 워크트리에서는 SessionStart 가 조용하다
        sync = wt / "harness" / "hooks" / "harness-sync.py"
        r = subprocess.run([sys.executable, str(sync)], input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(wt)}),
                           capture_output=True, text=True, env=self.sb.env(CLAUDE_PROJECT_DIR=str(wt)))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))
        # Stop: 커밋 안 된 변경이 있으면 막는다
        (wt / "메모.txt").write_text("x", encoding="utf-8")
        r = subprocess.run([sys.executable, str(sync)], input=json.dumps({"hook_event_name": "Stop", "cwd": str(wt)}),
                           capture_output=True, text=True, env=self.sb.env(CLAUDE_PROJECT_DIR=str(wt)))
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_codex_bind_is_silent_for_claude_session(self):
        script = self.plugin / "scripts" / "codex-bind-session.py"
        r = subprocess.run([sys.executable, str(script)], input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(self.target)}),
                           capture_output=True, text=True, env=self.sb.env(CLAUDE_PROJECT_DIR=str(self.target)))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_core_context_prints_summary(self):
        script = self.plugin / "scripts" / "core-context.py"
        r = subprocess.run([sys.executable, str(script)], input="{}", capture_output=True, text=True,
                           env=self.sb.env(PLUGIN_ROOT=str(self.plugin)))
        self.assertIn("Demo 프로젝트 핵심 규칙", r.stdout)
        self.assertIn("`web` `main`", r.stdout)


if __name__ == "__main__":
    unittest.main()
