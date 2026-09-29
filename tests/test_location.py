"""프로젝트 위치: 실행한 셸의 현재 폴더 아래 <슬러그>/ (--root 로 바꿈), 템플릿 원본은 ~/.harness-template."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from helpers import SCRIPTS, TEMPLATE, Sandbox
from test_new_repos import new_config

sys.path.insert(0, str(SCRIPTS))
import bootstrap as boot  # noqa: E402


class ProjectRoot(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.dev = self.sb.tmp / "dev"
        self.dev.mkdir()
        # 비어 있지 않은 폴더(이름도 슬러그와 다름) → <폴더>/<슬러그>. 빈 폴더 · 같은 이름은 test_simple 이 본다
        (self.dev / "다른프로젝트.txt").write_text("x", encoding="utf-8")

    def run_boot(self, *extra, cwd=None, **env):
        return self.sb.bootstrap("run", "--config", str(new_config(self.sb)), "--offline", "--non-interactive",
                                 "--skip-install", *extra, cwd=cwd or self.dev, **env)

    def test_created_under_cwd_slug(self):
        r = self.run_boot()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        root = self.dev / "zero"
        self.assertTrue((root / "orchestrator" / "harness.json").is_file())
        self.assertTrue((root / "web" / ".git").exists())
        self.assertTrue((root / "api" / ".git").exists())
        self.assertFalse((self.sb.tmp / "orchestrator").exists(), "템플릿 옆 · 홈에 흩어지면 안 된다")
        self.assertIn(f"프로젝트 루트: {root.resolve()}", r.stdout)
        # 두 번째 실행(같은 자리)은 멈추지 않는다
        r = self.run_boot()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_root_option(self):
        custom = self.sb.tmp / "여기 말고" / "acme"
        r = self.run_boot("--root", str(custom))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((custom / "orchestrator" / "harness.json").is_file())
        self.assertFalse((self.dev / "zero").exists())

    def test_caller_cwd_env_wins(self):
        other = self.sb.tmp / "caller"
        other.mkdir()
        (other / "x.txt").write_text("x", encoding="utf-8")
        r = self.run_boot(HARNESS_CALLER_CWD=str(other))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((other / "zero" / "orchestrator" / "harness.json").is_file())

    def test_run_inside_template_goes_next_to_it(self):
        old = os.environ.get("HARNESS_CALLER_CWD")
        os.environ["HARNESS_CALLER_CWD"] = str(TEMPLATE / "harness" / "scripts")
        try:
            self.assertEqual(boot.caller_base(), TEMPLATE.resolve().parent)
        finally:
            if old is None:
                os.environ.pop("HARNESS_CALLER_CWD", None)
            else:
                os.environ["HARNESS_CALLER_CWD"] = old

    def test_nonempty_root_stops(self):
        root = self.dev / "zero"
        root.mkdir()
        (root / "남의파일.txt").write_text("x", encoding="utf-8")
        r = self.run_boot()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("프로젝트 루트", r.stderr)
        self.assertIn("빈 폴더에서 다시 실행하거나", r.stderr)
        self.assertNotIn("--force", r.stderr, "멈춤 안내에서 --force 를 권하지 않는다")
        self.assertFalse((root / "orchestrator").exists())
        r = self.run_boot("--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((root / "남의파일.txt").read_text(encoding="utf-8"), "x")

    def test_root_with_only_our_repo_folders_is_ok(self):
        (self.dev / "zero" / "web").mkdir(parents=True)  # 비어 있는 저장소 폴더만 먼저 있음
        r = self.run_boot()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def answers(self, slug, root_answer):
        return "\n".join([
            "Loc", slug, root_answer, "", "대표님",            # 이름 · 슬러그 · 「여기에 만듭니다」 · 조직 · 호칭
            "1", "orchestrator", "main",                         # 하네스 새로 만들기 · 폴더 · 브랜치
            "web", "1", "", "", "main", "", "3", "", "", "",     # 저장소 web 새로 만들기
            "",                                                  # 저장소 끝
            "y", "y", "y", "y", "1", "n",                        # 채울 자리 · 엔진 Claude · Orca 아니오
        ]) + "\n"

    def test_interview_shows_root_and_accepts_enter_or_path(self):
        r = self.sb.bootstrap("run", "--detail", "--offline", "--skip-install", input_text=self.answers("loc", ""), cwd=self.dev)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"여기에 만듭니다: {(self.dev / 'loc').resolve()}", r.stdout)
        self.assertTrue((self.dev / "loc" / "orchestrator" / "harness.json").is_file())
        custom = self.sb.tmp / "다른곳"
        r = self.sb.bootstrap("run", "--detail", "--offline", "--skip-install", input_text=self.answers("loc2", str(custom)), cwd=self.dev)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((custom / "orchestrator" / "harness.json").is_file())
        self.assertFalse((self.dev / "loc2").exists())


BASIC = ("bash", "sh", "uname", "sed", "grep", "head", "cat", "mkdir", "rm", "mv", "dirname", "ls", "sleep",
         "mktemp", "tar", "id", "tr", "env", "gzip")


class InstallKeepsCwd(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.path = self.sb.tmp / "path"
        self.path.mkdir()
        for tool in BASIC + ("git", "curl"):
            if shutil.which(tool):
                os.symlink(shutil.which(tool), self.path / tool)
        os.symlink(sys.executable, self.path / "python3")
        node = self.path / "node"
        node.write_text("#!/bin/sh\necho v22.1.0\n", encoding="utf-8")
        node.chmod(0o755)

    def env(self, **extra):
        e = {"HOME": str(self.sb.home), "PATH": str(self.path), "HARNESS_OS": "linux", "HARNESS_PATH_ONLY": "1",
             "HARNESS_NO_TTY": "1", "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@example.com"}
        e.update(extra)
        return e

    def upstream(self):
        src = self.sb.tmp / "upstream"
        shutil.copytree(TEMPLATE, src, ignore=shutil.ignore_patterns("__pycache__"))
        env = self.sb.env()
        subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "t"], check=True, env=env)
        return src

    def test_curl_pipe_bash_creates_under_cwd(self):
        dev = self.sb.tmp / "dev"
        dev.mkdir()
        (dev / "x.txt").write_text("x", encoding="utf-8")
        cfg = new_config(self.sb)
        script = (TEMPLATE / "install.sh").read_text(encoding="utf-8")
        r = subprocess.run(["bash", "-s", "--", "--yes", "--", "run", "--config", str(cfg), "--non-interactive",
                            "--offline", "--skip-install"], input=script, capture_output=True, text=True, cwd=str(dev),
                           env=self.env(HARNESS_TEMPLATE_URL=str(self.upstream())), timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.sb.home / ".harness-template" / ".harness-template").is_file(), "템플릿 원본은 숨김 폴더")
        self.assertTrue((dev / "zero" / "orchestrator" / "harness.json").is_file(), "실행한 폴더 아래에 만들지 않았다")
        self.assertFalse((self.sb.home / "zero").exists())

    def test_existing_v1_harness_is_announced(self):
        old = self.sb.home / "orchestrator"
        old.mkdir()
        (old / "harness.json").write_text("{}", encoding="utf-8")
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--dry-run"], capture_output=True, text=True,
                           env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn(f"이미 만든 하네스가 있다: {old}", r.stdout)
        self.assertIn("bootstrap.py update", r.stdout)

    def test_legacy_template_dir_is_reused(self):
        legacy = self.sb.home / "harness-template"
        legacy.mkdir()
        (legacy / ".harness-template").write_text("x", encoding="utf-8")
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--dry-run"], capture_output=True, text=True,
                           env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("예전 자리", r.stdout)
        self.assertIn(f"PLAN 템플릿: {legacy}", r.stdout)
        shutil.rmtree(legacy)
        r = subprocess.run(["bash", str(TEMPLATE / "install.sh"), "--dry-run"], capture_output=True, text=True,
                           env=self.env(), stdin=subprocess.DEVNULL, timeout=60)
        self.assertIn(f"PLAN 템플릿: {self.sb.home / '.harness-template'}", r.stdout)


if __name__ == "__main__":
    unittest.main()
