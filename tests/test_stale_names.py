"""이름 · 호칭을 바꾼 뒤 생성기가 덮지 못하는 자리(머리말 · 사람 본문)에 남은 옛 값을 찾아 알린다."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import Sandbox


class StaleNames(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        path = self.sb.config(["claude"], "subagent")
        self.target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(path), "--target", str(self.target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def generate(self):
        r = subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / "generate.py"), "--root", str(self.target)],
                           capture_output=True, text=True, env=self.sb.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def doctor(self):
        return self.sb.bootstrap("doctor", "--target", str(self.target), "--offline").stdout

    def rename(self, name, title):
        path = self.target / "harness.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["project"]["name"], data["project"]["owner_title"] = name, title
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_no_report_without_rename(self):
        self.assertNotIn("낡은 이름", self.generate())
        self.assertIn("| 낡은 이름 · 호칭 | 통과 |", self.doctor())

    def test_rename_lists_spots_until_fixed(self):
        self.rename("Beta 서비스", "팀장님")
        out = self.generate()
        self.assertIn("낡은 이름 · 호칭(Demo 프로젝트 · 주임님)이 남은 자리", out)
        self.assertRegex(out, r"plugins/demo/skills/absolute-rules/SKILL\.md:\d+ \(Demo 프로젝트\)")
        self.assertRegex(out, r"plugins/demo/skills/deploy/SKILL\.md:\d+ \(주임님\)")
        self.assertIn("| 낡은 이름 · 호칭 | 안내 |", self.doctor())
        # 관리 블록 안(생성기가 다시 쓰는 곳)은 새 값으로 바뀌었다
        self.assertIn("Beta 서비스", (self.target / "CLAUDE.md").read_text(encoding="utf-8"))
        # 사람이 고치면 더 알리지 않는다
        for p in (self.target / "plugins" / "demo").rglob("*.md"):
            p.write_text(p.read_text(encoding="utf-8").replace("Demo 프로젝트", "Beta 서비스").replace("주임님", "팀장님"),
                         encoding="utf-8")
        for rel in ("README.md", "상황판.md", "CLAUDE.md", "docs/기록/README.md"):
            p = self.target / rel
            if p.is_file():
                p.write_text(p.read_text(encoding="utf-8").replace("Demo 프로젝트", "Beta 서비스").replace("주임님", "팀장님"),
                             encoding="utf-8")
        self.assertNotIn("낡은 이름", self.generate())
        manifest = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("old_names", manifest, "남은 자리가 없어진 옛 이름은 더 찾지 않는다")
        self.assertEqual(manifest["names"], {"name": "Beta 서비스", "owner_title": "팀장님"})
        self.assertIn("| 낡은 이름 · 호칭 | 통과 |", self.doctor())

    def test_renaming_twice_remembers_both(self):
        self.rename("Beta 서비스", "팀장님")
        self.generate()
        self.rename("Gamma 앱", "팀장님")
        out = self.generate()
        self.assertIn("Demo 프로젝트", out)
        self.assertNotIn("(Beta 서비스)", out, "Beta 는 관리 블록에만 들어갔다가 다시 바뀌었다")


if __name__ == "__main__":
    unittest.main()
