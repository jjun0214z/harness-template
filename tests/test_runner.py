"""작업자 실행 방식(runner): 고른 방식만 깔린다 · 옛 설정(orca.enabled) 이전 · Orca 는 묻지 않고 켜지 않는다."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import SCRIPTS, Sandbox

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402
import runners  # noqa: E402


class RunnerUnit(unittest.TestCase):
    def test_from_choice(self):
        self.assertEqual(runners.from_choice(""), "subagent")
        self.assertEqual(runners.from_choice("2"), "worktree-session")
        self.assertEqual(runners.from_choice("3"), "orca")
        self.assertEqual(runners.from_choice("orca"), "orca")
        self.assertEqual(runners.from_choice("n"), "subagent", "모르는 답은 기본값")

    def test_migrate(self):
        self.assertEqual(runners.migrate({"orca": {"enabled": True, "workspaces_dir": "w"}}),
                         {"orca": {"workspaces_dir": "w"}, "runner": "orca"})
        self.assertEqual(runners.migrate({"orca": False})["runner"], "subagent")
        self.assertEqual(runners.migrate({})["runner"], "subagent")
        kept = runners.migrate({"runner": "worktree-session", "orca": {"enabled": True}})
        self.assertEqual(kept["runner"], "worktree-session", "이미 있는 runner 가 이긴다")
        self.assertNotIn("enabled", kept["orca"])

    def test_validate(self):
        cfg = hl.normalize({"project": {"name": "X", "slug": "x"}, "runner": "tmux"})
        self.assertTrue(any("runner" in e for e in hl.validate(cfg)))


class RunnerSetup(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def read(self, target, rel):
        return (target / rel).read_text(encoding="utf-8")

    def test_default_is_subagent_and_orca_is_not_offered(self):
        """질문을 Enter 로만 넘기면 서브에이전트. Orca 는 도구 표에도 파일에도 나오지 않는다."""
        box = self.sb.tmp / "s"
        box.mkdir()
        r = self.sb.bootstrap("run", "--offline", "--skip-install", input_text="s\n\n\n\n\n", cwd=box)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("질문 4개", r.stdout)
        self.assertIn("3) 작업자 실행 방식 (1) 서브에이전트 (2) 워크트리 세션 (3) Orca [1]:", r.stdout)
        self.assertIn("작업자 서브에이전트", r.stdout)
        self.assertNotIn("| orca |", r.stdout)
        target = box / "orchestrator"
        cfg = json.loads(self.read(target, "harness.json"))
        self.assertEqual(cfg["runner"], "subagent")
        self.assertNotIn("enabled", cfg["orca"])
        self.assertFalse((target / "scripts").exists(), "Orca 를 고르지 않았는데 scripts/ 가 생겼다")
        claude_md = self.read(target, "CLAUDE.md")
        self.assertIn("서브에이전트 `<키>-worker`(워크트리 격리)", claude_md)
        self.assertNotIn("Orca", claude_md)
        self.assertNotIn("Orca", self.read(target, "plugins/s/core.md"))

    def test_runner_flag_skips_question(self):
        box = self.sb.tmp / "w"
        box.mkdir()
        r = self.sb.bootstrap("run", "--offline", "--skip-install", "--runner", "worktree-session",
                              input_text="w\n\n\n\n", cwd=box)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("질문 3개", r.stdout)
        self.assertNotIn("작업자 실행 방식 (1)", r.stdout)
        target = box / "orchestrator"
        self.assertEqual(json.loads(self.read(target, "harness.json"))["runner"], "worktree-session")
        self.assertIn("`claude --worktree <작업이름>`", self.read(target, "CLAUDE.md"))
        self.assertIn("별도 워크트리 세션", self.read(target, "plugins/w/core.md"))
        self.assertFalse((target / "scripts").exists())

    def test_switching_runner_removes_orca_files(self):
        """orca → subagent 로 바꾸고 다시 생성하면 Orca 스크립트 · 문장이 사라진다(관리 파일 추적)."""
        cfg_path = self.sb.config(["claude"], True)
        target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(cfg_path), "--target", str(target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((target / "scripts" / "orca-worker.sh").is_file())
        data = json.loads(self.read(target, "harness.json"))
        self.assertEqual(data["runner"], "orca")
        data["runner"] = "subagent"
        (target / "harness.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        r = subprocess.run([sys.executable, str(target / "harness" / "scripts" / "generate.py"), "--root", str(target)],
                           capture_output=True, text=True, env=self.sb.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((target / "scripts" / "orca-worker.sh").exists())
        self.assertNotIn("orca-worker.sh", self.read(target, "CLAUDE.md"))


class RunnerMigration(unittest.TestCase):
    """옛 하네스(orca.enabled)를 새 생성기로 다시 만들면 harness.json 이 runner 로 옮겨진다."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def build(self, orca: bool):
        cfg_path = self.sb.config(["claude"], orca)
        target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(cfg_path), "--target", str(target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 옛 모양으로 되돌린다: runner 를 빼고 orca.enabled 를 넣는다
        path = target / "harness.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("runner")
        data["orca"]["enabled"] = orca
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    def generate(self, target):
        return subprocess.run([sys.executable, str(target / "harness" / "scripts" / "generate.py"), "--root", str(target)],
                              capture_output=True, text=True, env=self.sb.env())

    def test_orca_enabled_becomes_runner_orca(self):
        target = self.build(True)
        r = self.generate(target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("설정 이전", r.stdout)
        data = json.loads((target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(data["runner"], "orca")
        self.assertNotIn("enabled", data["orca"])
        keys = list(data)
        self.assertEqual(keys.index("runner"), keys.index("engines") + 1, "runner 는 engines 바로 뒤에")
        self.assertTrue((target / "scripts" / "orca-worker.sh").is_file())
        r = self.generate(target)
        self.assertNotIn("설정 이전", r.stdout, "두 번째부터는 옮길 것이 없다")

    def test_orca_disabled_becomes_subagent(self):
        target = self.build(False)
        r = self.generate(target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads((target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(data["runner"], "subagent")


if __name__ == "__main__":
    unittest.main()
