"""기본 셋업 질문 2개(fix-simple) · 윈도우 실사용 결함(fix-win-run) · Orca 자동 설치(fix-orca-auto)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest

from helpers import SCRIPTS, Sandbox

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env).stdout.strip()


class TwoQuestions(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def simple(self, cwd, text, *extra):
        return self.sb.bootstrap("run", "--offline", "--skip-install", "--no-orca", *extra, input_text=text, cwd=cwd)

    def test_kids_enter_enter_in_folder_named_kids(self):
        kids = self.sb.tmp / "kids"
        kids.mkdir()
        r = self.simple(kids, "kids\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"여기에 만듭니다: {kids.resolve()} · 엔진 Claude · 저장소는 나중에(add-repo)", r.stdout)
        self.assertTrue((kids / "orchestrator" / "harness.json").is_file(), "kids\\kids 로 겹치면 안 된다")
        self.assertFalse((kids / "kids").exists())
        cfg = json.loads((kids / "orchestrator" / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["project"]["slug"], cfg["repos"], cfg["engines"], cfg["project"]["owner_title"]),
                         ("kids", [], ["claude"], "대표님"))
        self.assertEqual(cfg["skills"]["fill"], list(hl.FILL_SKILLS))
        # 같은 폴더에서 다시: 질문 없이 기존 하네스를 쓴다
        r = self.simple(kids, "")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("이미 만든 하네스를 쓴다", r.stdout)

    def test_nonempty_folder_gets_slug_subfolder(self):
        dev = self.sb.tmp / "dev"
        dev.mkdir()
        (dev / "다른것.txt").write_text("x", encoding="utf-8")
        r = self.simple(dev, "kids\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((dev / "kids" / "orchestrator" / "harness.json").is_file())

    def test_nonempty_folder_named_like_slug_gets_subfolder(self):
        """폴더 이름이 슬러그와 같아도 코드가 든 git 저장소면 그 안에 섞지 않고 <폴더>/<슬러그> 에 만든다."""
        kids = self.sb.tmp / "kids"
        kids.mkdir()
        git(kids, "init", "-q")
        (kids / "main.py").write_text("print(1)\n", encoding="utf-8")
        r = self.simple(kids, "kids\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"여기에 만듭니다: {(kids / 'kids').resolve()}", r.stdout)
        self.assertTrue((kids / "kids" / "orchestrator" / "harness.json").is_file())
        self.assertFalse((kids / "orchestrator").exists())
        self.assertEqual(git(kids, "rev-parse", "--verify", "-q", "HEAD"), "", "원래 저장소에 커밋을 만들면 안 된다")

    def test_reviewer_sandbox_three_folders(self):
        """검토원 재현(sb9): 남의 프로젝트가 든 dev/ · 코드 든 git 저장소 kids/ · 빈 폴더. 빈 폴더만 그 자리, 나머지는 <슬러그> 아래."""
        sb9 = self.sb.tmp / "sb9"
        dev, kids, empty = sb9 / "dev", sb9 / "kids", sb9 / "empty"
        (dev / "other-project").mkdir(parents=True)
        (dev / "other-project" / "package.json").write_text("{}", encoding="utf-8")
        kids.mkdir()
        git(kids, "init", "-q")
        (kids / "app.js").write_text("1\n", encoding="utf-8")
        empty.mkdir()
        for cwd, want in ((dev, dev / "kids"), (kids, kids / "kids"), (empty, empty)):
            r = self.simple(cwd, "kids\n\n\n")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((want / "orchestrator" / "harness.json").is_file(), f"{cwd} -> {want}")
        self.assertEqual(sorted(p.name for p in dev.iterdir()), ["kids", "other-project"])
        self.assertEqual(sorted(p.name for p in kids.iterdir()), [".git", "app.js", "kids"])

    def test_slug_subfolder_taken_asks_other_path(self):
        """<폴더>/<슬러그> 도 남의 것으로 차 있으면 만들지 않고 다른 경로를 묻는다. 안내에 --force 는 없다."""
        dev = self.sb.tmp / "dev"
        (dev / "kids").mkdir(parents=True)
        (dev / "kids" / "남의것.txt").write_text("x", encoding="utf-8")
        r = self.simple(dev, "kids\n\n\n")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("빈 폴더에서 다시 실행하거나", r.stdout)
        self.assertNotIn("--force", r.stdout + r.stderr)
        self.assertFalse((dev / "kids" / "orchestrator").exists())
        # 다른 경로를 치면 그곳에. 그곳도 차 있으면 한 번 더 묻는다
        busy = self.sb.tmp / "busy"
        busy.mkdir()
        (busy / "x.txt").write_text("x", encoding="utf-8")
        other = self.sb.tmp / "새 자리"
        r = self.simple(dev, f"kids\n\n{busy}\n{other}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((other / "orchestrator" / "harness.json").is_file())
        self.assertFalse((busy / "orchestrator").exists())
        self.assertEqual((dev / "kids" / "남의것.txt").read_text(encoding="utf-8"), "x")

    def test_empty_folder_other_name_is_used_itself(self):
        box = self.sb.tmp / "빈폴더"
        box.mkdir()
        r = self.simple(box, "Kids App\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((box / "orchestrator" / "harness.json").is_file())
        self.assertEqual(json.loads((box / "orchestrator" / "harness.json").read_text(encoding="utf-8"))["project"]["slug"], "kids-app")

    def test_korean_name_slug_from_folder(self):
        box = self.sb.tmp / "myapp"
        box.mkdir()
        r = self.simple(box, "우리 앱\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = json.loads((box / "orchestrator" / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["project"]["name"], cfg["project"]["slug"]), ("우리 앱", "myapp"))

    def test_engine_default_is_installed_one(self):
        self.sb.fake_tools(("codex",))
        box = self.sb.tmp / "c"
        box.mkdir()
        r = self.simple(box, "c\n\n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("엔진 (1) Claude (2) Codex (3) 둘 다 [2]", r.stdout)
        self.assertEqual(json.loads((box / "orchestrator" / "harness.json").read_text(encoding="utf-8"))["engines"], ["codex"])

    def test_summary_n_stops(self):
        box = self.sb.tmp / "s"
        box.mkdir()
        r = self.simple(box, "s\n\nn\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--detail", r.stderr)
        self.assertFalse((box / "orchestrator").exists())


class DetailChecks(unittest.TestCase):
    """--detail 에서 즉시 검사: 하네스 폴더가 git 이 아니면 git init 제안, 저장소 키 · 연결 경로는 틀리면 다시 묻는다."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.env = self.sb.env()

    def test_harness_local_non_git_offers_git_init(self):
        h = self.sb.tmp / "ops"
        h.mkdir()
        (h / "메모.txt").write_text("원래 파일", encoding="utf-8")
        answers = "\n".join(["Ops", "ops", "", "", "대표님",
                             "2", str(self.sb.tmp / "없는곳"), str(h), "y", "",   # 없는 경로 → 다시 → git 아님 → git init 예 · 브랜치
                             "",                                               # 저장소 나중에
                             "y", "y", "y", "y", "1", "n"]) + "\n"
        r = self.sb.bootstrap("run", "--detail", "--offline", "--skip-install", input_text=answers, cwd=self.sb.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("폴더가 없다", r.stdout)
        self.assertIn("여기에 git init 해서 하네스로 쓸까요", r.stdout)
        tracked = git(h, "ls-files", env=self.env).split("\n")
        self.assertIn("harness.json", tracked)
        self.assertNotIn("메모.txt", tracked, "원래 있던 파일을 커밋했다")

    def test_repo_key_and_local_path_are_rechecked(self):
        target = self.sb.projects / "orchestrator"
        cfg = {"project": {"name": "K", "slug": "k", "owner_title": "대표님"}, "repos": [], "engines": ["claude"],
               "platform": {"python": "python3"}}
        p = self.sb.tmp / "k.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.sb.bootstrap("run", "--config", str(p), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plain = self.sb.tmp / "plain"
        plain.mkdir()
        repo = self.sb.tmp / "real"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env)
        answers = "\n".join(["1", "Web", "web", "3", str(plain), str(repo), "n", "3"]) + "\n"
        r = self.sb.bootstrap("add-repo", "--target", str(target), "--offline", "--skip-install", input_text=answers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("「1」 는 키로 쓸 수 없다", r.stdout)
        self.assertIn("「Web」 는 키로 쓸 수 없다", r.stdout)
        self.assertIn("git 저장소가 아니다", r.stdout)
        cfg = json.loads((target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["repos"][0]["key"], cfg["repos"][0]["source"], cfg["repos"][0]["dir"]), ("web", "local", "real"))

    def test_validate_rejects_digit_key(self):
        cfg = hl.normalize({"project": {"name": "x", "slug": "x", "owner_title": "대표님"}, "repos": [{"key": "1"}],
                            "engines": ["claude"]})
        self.assertTrue(any("repos[0].key" in e for e in hl.validate(cfg)))


class WindowsJudgments(unittest.TestCase):
    def test_install_state(self):
        self.assertEqual(hl.install_state("gh", 0, True)[0], "통과")
        self.assertEqual(hl.install_state("gh", 0, False)[0], "안내", "종료 코드 0 을 실패로 적으면 안 된다")
        self.assertEqual(hl.install_state("gh", 1, False)[0], "실패")
        self.assertEqual(hl.install_state("pnpm", 1, False)[0], "없음", "pnpm 실패는 치명이 아니다")

    def test_pnpm_on_windows_uses_npm_not_corepack(self):
        cmd, _ = hl.install_command("pnpm", "windows", lambda n: n in ("npm", "corepack"))
        self.assertEqual(cmd, ["npm", "install", "-g", "pnpm"])

    def test_orca_install_commands(self):
        self.assertEqual(hl.install_command("orca", "windows", lambda n: n == "winget")[0][:5],
                         ["winget", "install", "-e", "--id", "StablyAI.Orca"])
        self.assertEqual(hl.install_command("orca", "mac", lambda n: n == "brew")[0], ["brew", "install", "--cask", "stablyai/orca/orca"])
        self.assertEqual(hl.install_command("orca", "linux", lambda n: False)[0][0], "__download__")

    def test_refresh_windows_path_with_fake_registry(self):
        fake = types.SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2)

        class Key:
            def __init__(self, root): self.root = root
            def __enter__(self): return self
            def __exit__(self, *a): return False
        fake.OpenKey = lambda root, sub: Key(root)
        fake.QueryValueEx = lambda key, name: (r"C:\Windows;C:\Program Files\GitHub CLI" if key.root == 1 else r"C:\Users\u\AppData\Local\Microsoft\WinGet\Links", 1)
        env = {"PATH": "/old"}
        hl.refresh_windows_path(env, fake)
        self.assertTrue(env["PATH"].startswith(r"C:\Windows" + os.pathsep + r"C:\Program Files\GitHub CLI"))
        self.assertIn("WinGet", env["PATH"])
        self.assertTrue(env["PATH"].endswith("/old"))

    def test_find_bash_in_git_for_windows(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        bash = sb.tmp / "PF" / "Git" / "bin" / "bash.exe"
        bash.parent.mkdir(parents=True)
        bash.write_text("", encoding="utf-8")
        self.assertEqual(hl.find_bash("windows", {"PATH": "", "ProgramFiles": str(sb.tmp / "PF")}), str(bash))
        self.assertIsNone(hl.find_bash("windows", {"PATH": "", "ProgramFiles": str(sb.tmp / "none")}))

    def test_boot_cmd_per_os(self):
        self.assertEqual(hl.boot_cmd("update", "windows", "py -3"), "py -3 harness\\scripts\\bootstrap.py update")
        self.assertEqual(hl.boot_cmd("update", "mac"), "python3 harness/scripts/bootstrap.py update")

    def test_generated_docs_use_configured_python(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        cfg = {"project": {"name": "W", "slug": "w", "owner_title": "대표님"}, "repos": [], "engines": ["claude"],
               "platform": {"python": "py -3"}}
        p = sb.tmp / "w.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        target = sb.projects / "orchestrator"
        r = sb.bootstrap("run", "--config", str(p), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("py -3 harness/scripts/generate.py", claude_md)
        self.assertIn("py -3 harness/scripts/bootstrap.py add-repo", claude_md)
        self.assertNotIn("python3 harness/scripts", claude_md)


class OrcaAutoInstall(unittest.TestCase):
    """mac 흉내: brew 로 Orca 설치 → 앱 열기(open -a Orca) → CLI 응답 대기 → 저장소 등록 순서."""

    def test_install_open_wait_register(self):
        if hl.os_kind() != "mac":
            self.skipTest("mac 흐름 흉내")
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        log, bin_ = sb.log, sb.bin
        marker = sb.tmp / "orca-opened"
        orca = ('#!/bin/sh\necho "orca $*" >> "%s"\ncase "$1 $2" in\n  "repo list") [ -f "%s" ] || exit 1; '
                'echo \'{"result":{"repos":[]}}\' ;;\nesac\nexit 0\n') % (log, marker)
        brew = ('#!/bin/sh\necho "brew $*" >> "%s"\nif [ "$*" = "install --cask stablyai/orca/orca" ]; then\n'
                'cat > "%s/orca" <<\'EOF\'\n%sEOF\nchmod +x "%s/orca"\nfi\nexit 0\n') % (log, bin_, orca, bin_)
        opener = '#!/bin/sh\necho "open $*" >> "%s"\ntouch "%s"\nexit 0\n' % (log, marker)
        for name, body in (("brew", brew), ("open", opener)):
            (bin_ / name).write_text(body, encoding="utf-8")
            (bin_ / name).chmod(0o755)
        box = sb.tmp / "o"
        box.mkdir()
        r = sb.bootstrap("run", "--offline", input_text="o\n\n\ny\n", cwd=box, HARNESS_ORCA_WAIT="10")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| orca | 없음 | 설치(선택) | brew install --cask stablyai/orca/orca |", r.stdout)
        lines = log.read_text(encoding="utf-8").splitlines()
        i_install = lines.index("brew install --cask stablyai/orca/orca")
        i_open = lines.index("open -a Orca")
        i_ok = max(i for i, l in enumerate(lines) if l == "orca repo list --json")
        i_add = next(i for i, l in enumerate(lines) if l.startswith("orca repo add"))
        self.assertLess(i_install, i_open)
        self.assertLess(i_open, i_ok)
        self.assertLess(i_ok, i_add)
        cfg = json.loads((box / "orchestrator" / "harness.json").read_text(encoding="utf-8"))
        self.assertTrue(cfg["orca"]["enabled"])
        self.assertNotIn("_auto", cfg["orca"])
        self.assertTrue((box / "orchestrator" / "scripts" / "orca-worker.sh").is_file())

    def test_declined_orca_means_no_orca(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        box = sb.tmp / "d"
        box.mkdir()
        r = sb.bootstrap("run", "--offline", "--skip-install", input_text="d\n\n\nn\n", cwd=box)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = json.loads((box / "orchestrator" / "harness.json").read_text(encoding="utf-8"))
        self.assertFalse(cfg["orca"]["enabled"], "Orca 를 설치하지 않았는데 켜 두었다")
        self.assertFalse((box / "orchestrator" / "scripts" / "orca-worker.sh").exists())


if __name__ == "__main__":
    unittest.main()
