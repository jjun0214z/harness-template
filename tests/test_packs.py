"""설치 최소화 · 선택 묶음(packs): node 는 쓸 때만 · 고르지 않은 묶음은 파일 · 훅 · 자동 동작이 없다 · 옛 설정은 동작 그대로."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from helpers import SCRIPTS, Sandbox

sys.path.insert(0, str(SCRIPTS))
import bootstrap  # noqa: E402
import finish_worker  # noqa: E402
import harnesslib as hl  # noqa: E402


def cfg_with(**over):
    raw = {"project": {"name": "P", "slug": "p"}, "engines": ["claude"], "runner": "subagent", "packs": []}
    raw.update(over)
    return hl.normalize(raw)


class NodeOnlyWhenNeeded(unittest.TestCase):
    def test_claude_only_needs_no_node(self):
        self.assertNotIn("node", bootstrap.needed_tools(cfg_with()))

    def test_codex_needs_node(self):
        self.assertIn("node", bootstrap.needed_tools(cfg_with(engines=["codex"])))

    def test_node_stack_needs_node(self):
        c = cfg_with(repos=[{"key": "web", "stack": "node"}])
        self.assertIn("node", bootstrap.needed_tools(c))


class PackConfig(unittest.TestCase):
    def test_new_default_is_empty(self):
        self.assertEqual(hl.default_config()["packs"], [])

    def test_missing_packs_keeps_old_behavior(self):
        """packs 칸이 없던 옛 설정은 그때 있던 묶음을 켠 것으로 읽는다(갱신으로 동작이 바뀌지 않게). workflow 는 그때 없었다."""
        c = hl.normalize({"project": {"name": "P", "slug": "p"}})
        self.assertEqual(c["packs"], list(hl.LEGACY_PACKS))

    def test_validate(self):
        self.assertTrue(any("packs" in e for e in hl.validate(cfg_with(packs=["tmux"]))))
        self.assertTrue(any("packs" in e for e in hl.validate(cfg_with(packs=["cleanup", "cleanup"]))))
        self.assertEqual([e for e in hl.validate(cfg_with(packs=["cleanup"])) if "packs" in e], [])


class PackFiles(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def build(self, packs):
        path = self.sb.config(["claude", "codex"], "subagent")
        data = json.loads(path.read_text(encoding="utf-8"))
        if packs is not None:
            data["packs"] = packs
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(path), "--target", str(target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return target

    def scripts_and_start(self, target):
        scripts = {p.name for p in (target / "plugins" / "demo" / "scripts").iterdir()}
        hooks = json.loads((target / "plugins" / "demo" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        start = [h["command"] for h in hooks["hooks"]["SessionStart"][0]["hooks"]]
        return scripts, start

    def test_codex_without_desktop_pack(self):
        scripts, start = self.scripts_and_start(self.build([]))
        self.assertNotIn("codex-bind-session.py", scripts)
        self.assertNotIn("codex_project_binding.py", scripts)
        self.assertFalse(any("codex-bind-session" in c for c in start))

    def test_codex_with_desktop_pack(self):
        scripts, start = self.scripts_and_start(self.build(["codex-desktop"]))
        self.assertIn("codex-bind-session.py", scripts)
        self.assertTrue(any("codex-bind-session" in c for c in start))

    def test_old_config_file_is_migrated_in_place(self):
        target = self.build(None)
        path = target / "harness.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["packs"], list(hl.LEGACY_PACKS), "packs 없던 설정은 그때 있던 묶음 전부로 적힌다")
        data.pop("packs")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(target / "harness" / "scripts" / "generate.py"), "--root", str(target)],
                           capture_output=True, text=True, env=self.sb.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = list(data)
        self.assertEqual(data["packs"], list(hl.LEGACY_PACKS))
        self.assertEqual(keys.index("packs"), keys.index("engines") + 1)

    def test_detail_asks_packs(self):
        answers = "\n".join(["Ops", "ops", "", "", "", "1", "orchestrator", "main", "",
                             "y", "y", "y", "y", "1", "1", "n", "y"]) + "\n"
        r = self.sb.bootstrap("run", "--detail", "--offline", "--skip-install", input_text=answers, cwd=self.sb.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cleanup:", r.stdout)
        self.assertNotIn("codex-desktop:", r.stdout, "Codex 를 안 골랐으면 묻지 않는다")
        cfg = json.loads((self.sb.tmp / "ops" / "orchestrator" / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["packs"], ["cleanup"])


class CleanupPack(unittest.TestCase):
    def test_sweep_skipped_without_pack(self):
        with mock.patch.object(finish_worker.ct, "claude_tmp", side_effect=AssertionError("돌면 안 된다")), \
                mock.patch.object(finish_worker.ct, "sweep", side_effect=AssertionError("돌면 안 된다")):
            finish_worker.sweep_temp("/nowhere", cfg_with(packs=[]), True, [])

    def test_sweep_runs_with_pack(self):
        if finish_worker.os.name == "nt":
            self.skipTest("임시 찌꺼기 정리는 macOS · Linux")
        with mock.patch.object(finish_worker.ct, "claude_tmp") as tmp, \
                mock.patch.object(finish_worker.ct, "sweep", return_value=[]) as sweep, \
                mock.patch.object(finish_worker, "all_worktrees", return_value=[]):
            finish_worker.sweep_temp("/nowhere", cfg_with(packs=["cleanup"]), True, [])
        self.assertTrue(tmp.called and sweep.called)


if __name__ == "__main__":
    unittest.main()
