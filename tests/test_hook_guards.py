"""훅 보강: 공용 명령 해석(shellwords) · 규칙 변경 이름 알림 · Stop 지문 · 고친 뒤 할 일 알림."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import TEMPLATE, Sandbox


def run_hook(script, payload, env):
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip().startswith("{") else r.stdout.strip()


class Built(unittest.TestCase):
    """하네스 하나를 만들고(Claude · Codex) 첫 커밋까지 둔 상태."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        cfg = cls.sb.config(["claude", "codex"], "subagent")
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

    def env(self, **extra):
        return self.sb.env(PLUGIN_ROOT=str(self.plugin), CLAUDE_PROJECT_DIR=str(self.target), **extra)


class SharedParser(Built):
    def test_two_copies_are_the_same(self):
        a = (TEMPLATE / "harness" / "hooks" / "shellwords.py").read_bytes()
        b = (TEMPLATE / "harness" / "skeleton" / "plugin" / "scripts" / "shellwords.py").read_bytes()
        self.assertEqual(a, b, "shellwords.py 두 벌이 다르다(원본: harness/skeleton/plugin/scripts/)")
        self.assertEqual((self.plugin / "scripts" / "shellwords.py").read_bytes(), b, "플러그인에 실리지 않았다")
        self.assertEqual((self.target / "harness" / "hooks" / "shellwords.py").read_bytes(), b)


class PushGuard(Built):
    def push(self, command, cwd=None):
        out = run_hook(self.plugin / "scripts" / "guard-push.py",
                       {"tool_name": "Bash", "cwd": str(cwd or self.web), "tool_input": {"command": command}}, self.env())
        return out["hookSpecificOutput"]["permissionDecision"] if isinstance(out, dict) else None

    def test_force_hidden_in_wrappers(self):
        for cmd in ("sh -c 'git push -f origin develop'", "git push -fu origin develop", "git push --mirror",
                    "git -c remote.origin.mirror=true push", "bash -lc \"git push --force origin x\"",
                    "echo $(git push -f)"):
            self.assertEqual(self.push(cmd), "deny", cmd)

    def test_git_dash_c_target(self):
        self.assertEqual(self.push(f'git -C "{self.web}" push origin HEAD:main', cwd=self.sb.tmp), "ask")

    def test_unknown_place_asks(self):
        for cmd in ("cd $X && git push origin develop", "echo 'git push origin main' | sh", 'git push "origin'):
            self.assertEqual(self.push(cmd), "ask", cmd)

    def test_data_is_not_a_command(self):
        self.assertIsNone(self.push("cat <<'EOF' > note.md\ngit push -f origin main\nEOF"))
        self.assertIsNone(self.push("echo 'git push -f'"))
        self.assertIsNone(self.push("git push origin develop"))


class RuleGuard(Built):
    def rules(self, command, cwd=None):
        out = run_hook(self.target / "harness" / "hooks" / "guard-rules.py",
                       {"tool_name": "Bash", "cwd": str(cwd or self.target), "tool_input": {"command": command}}, self.env())
        return out["hookSpecificOutput"]["permissionDecision"] if isinstance(out, dict) else None

    def test_writes_through_wrappers_ask(self):
        t = self.target
        for cmd in (f'cd "{t}" && echo x > CLAUDE.md', "sh -c 'echo x >> CLAUDE.md'",
                    "python3 - <<'EOF'\nopen('CLAUDE.md', 'w').write('x')\nEOF",
                    "cd $X && rm CLAUDE.md", "sed -i '' 's/a/b/' harness.json"):
            self.assertEqual(self.rules(cmd, cwd=self.sb.tmp if cmd.startswith("cd \"") else t), "ask", cmd)

    def test_reads_and_other_files_quiet(self):
        for cmd in ("cat CLAUDE.md", "grep -n x CLAUDE.md > /dev/null", 'echo "CLAUDE.md 를 본다" > 상황판.md',
                    "cat <<'EOF' > docs/기록/a.md\nCLAUDE.md 를 고쳤다\nEOF"):
            self.assertIsNone(self.rules(cmd), cmd)


class SyncAndStop(unittest.TestCase):
    """원격이 있는 하네스: 다른 곳에서 올린 커밋을 받을 때 · 턴이 끝날 때."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        cfg = self.sb.config(["claude"], "subagent")
        self.target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        e = self.sb.env()
        self.bare = self.sb.remotes / "orchestrator.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True, env=e)
        subprocess.run(["git", "-C", str(self.target), "remote", "add", "origin", str(self.bare)], check=True, env=e)
        subprocess.run(["git", "-C", str(self.target), "push", "-q", "-u", "origin", "main"], check=True, env=e)
        self.other = self.sb.tmp / "other"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.bare), str(self.other)], check=True, env=e)
        self.sync = self.target / "harness" / "hooks" / "harness-sync.py"

    def git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=self.sb.env(), capture_output=True)

    def push_from_other(self, files):
        for rel in files:
            p = self.other / rel
            p.write_text(p.read_text(encoding="utf-8") + "\n바뀜\n", encoding="utf-8")
        self.git(self.other, "commit", "-qam", "다른 곳에서")
        self.git(self.other, "push", "-q", "origin", "main")

    def hook(self, event, **extra):
        return run_hook(self.sync, {"hook_event_name": event, "cwd": str(self.target), **extra},
                        self.sb.env(CLAUDE_PROJECT_DIR=str(self.target)))

    def test_session_start_names_changed_rules(self):
        self.push_from_other(["CLAUDE.md", "상황판.md"])
        out = self.hook("SessionStart")
        self.assertIn("원격에서 1커밋을 받았다", out)
        self.assertIn("규칙이 바뀌었다: CLAUDE.md", out)
        self.assertNotIn("상황판.md", out, "상황판은 규칙이 아니다")

    def test_session_start_without_rule_change(self):
        self.push_from_other(["상황판.md"])
        out = self.hook("SessionStart")
        self.assertIn("원격에서 1커밋을 받았다.", out)
        self.assertNotIn("규칙이 바뀌었다", out)
        self.assertEqual(self.hook("SessionStart"), "", "받을 것이 없으면 조용하다")

    def test_stop_blocks_once_per_state(self):
        (self.target / "메모.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.hook("Stop", session_id="s1")["decision"], "block")
        self.assertEqual(self.hook("Stop", session_id="s1"), "", "같은 세션 같은 상태는 다시 막지 않는다")
        self.assertEqual(self.hook("Stop", session_id="s2")["decision"], "block", "새 세션은 한 번 막는다")
        (self.target / "메모2.txt").write_text("y", encoding="utf-8")
        self.assertEqual(self.hook("Stop", session_id="s2")["decision"], "block", "상태가 바뀌면 다시 막는다")
        for name in ("메모.txt", "메모2.txt"):
            (self.target / name).unlink()
        self.assertEqual(self.hook("Stop", session_id="s2"), "")
        gd = subprocess.run(["git", "-C", str(self.target), "rev-parse", "--path-format=absolute", "--git-dir"],
                            capture_output=True, text=True, env=self.sb.env()).stdout.strip()
        self.assertFalse(Path(gd, "harness-stop-notice").exists(), "깨끗해지면 지문을 지운다")
        status = subprocess.run(["git", "-C", str(self.target), "status", "--porcelain"], capture_output=True,
                                text=True, env=self.sb.env()).stdout
        self.assertEqual(status, "", "지문 · 표시 파일이 작업 트리에 생기면 안 된다")


class Remind(Built):
    def remind(self, path, session="s1", **extra):
        return run_hook(self.target / "harness" / "hooks" / "remind-check.py",
                        {"tool_name": "Edit", "cwd": str(self.target), "session_id": session,
                         "tool_input": {"file_path": str(path)}, **extra}, self.env())

    def test_config_edit_reminds_generate_once(self):
        out = self.remind(self.target / "harness.json", session="cfg")
        self.assertIn("harness/scripts/generate.py", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.remind(self.target / "harness.json", session="cfg"), "", "같은 세션에 두 번 말하지 않는다")
        self.assertIsInstance(self.remind(self.target / "harness.json", session="cfg2"), dict)

    def test_plugin_edit_reminds_doctor(self):
        out = self.remind(self.plugin / "core.md", session="hk")
        self.assertIn("bootstrap.py doctor", out["hookSpecificOutput"]["additionalContext"])

    def test_other_files_quiet(self):
        self.assertEqual(self.remind(self.target / "상황판.md", session="q"), "")
        self.assertEqual(self.remind(self.target / "docs" / "기록" / "README.md", session="q"), "")

    def test_codex_gets_system_message(self):
        out = run_hook(self.target / "harness" / "hooks" / "remind-check.py",
                       {"tool_name": "apply_patch", "cwd": str(self.target), "turn_id": "t9",
                        "tool_input": {"command": "*** Begin Patch\n*** Update File: harness.json\n*** End Patch"}}, self.env())
        self.assertIn("generate.py", out["systemMessage"])

    def test_wired_in_settings(self):
        claude = json.loads((self.target / ".claude" / "settings.json").read_text(encoding="utf-8"))
        cmds = [h["command"] for g in claude["hooks"]["PostToolUse"] for h in g["hooks"]]
        self.assertTrue(any("remind-check.py" in c for c in cmds))
        codex = json.loads((self.target / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        cmds = [h["command"] for g in codex["hooks"]["PostToolUse"] for h in g["hooks"]]
        self.assertTrue(any("remind-check.py" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
