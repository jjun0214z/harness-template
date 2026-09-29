"""검토 반려 재현: 파이프 설치에서 자식이 스크립트를 먹지 않는다 · 질문 중 입력이 끊기면 멈춘다 · 남의 폴더 · 저장소를 커밋 · push 하지 않는다.
그리고 update 와 첫 생성의 결과가 같고, 압축의 링크 멤버는 거른다.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import unittest

from helpers import SCRIPTS, TEMPLATE, Sandbox
from test_new_repos import new_config

sys.path.insert(0, str(SCRIPTS))
import bootstrap as boot  # noqa: E402

BASIC = ("bash", "sh", "uname", "sed", "grep", "head", "cat", "mkdir", "rm", "mv", "dirname", "ls", "sleep",
         "mktemp", "tar", "id", "tr", "env", "gzip")


class PipedInstall(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.path = self.sb.tmp / "path"
        self.path.mkdir()
        for tool in BASIC:
            if shutil.which(tool):
                os.symlink(shutil.which(tool), self.path / tool)
        os.symlink(sys.executable, self.path / "python3")
        os.symlink(shutil.which("curl"), self.path / "curl")
        for name, body in (("apt-get", 'read x; echo "child-read:[$x]"; exit 0'), ("sudo", 'exec "$@"'), ("node", "echo v22.1.0")):
            p = self.path / name
            p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            p.chmod(0o755)
        self.tgz = self.sb.tmp / "t.tgz"
        with tarfile.open(self.tgz, "w:gz") as tf:
            tf.add(TEMPLATE, arcname="harness-template-main", filter=lambda ti: None if "__pycache__" in ti.name else ti)

    def pipe(self, script_text):
        env = {"HOME": str(self.sb.home), "PATH": str(self.path), "HARNESS_OS": "linux", "HARNESS_PATH_ONLY": "1",
               "HARNESS_DIR": str(self.sb.tmp / "harness-template"), "HARNESS_NO_TTY": "1", "LANG": "C.UTF-8",
               "HARNESS_TARBALL_URL": self.tgz.as_uri()}
        return subprocess.run(["bash", "-s", "--", "--yes", "--no-run"], input=script_text, capture_output=True,
                              text=True, env=env, timeout=120)

    def test_child_does_not_eat_piped_script(self):
        text = (TEMPLATE / "install.sh").read_text(encoding="utf-8") + "\necho AFTER_MAIN_SHOULD_NOT_RUN\n"
        r = self.pipe(text)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("child-read:[]", r.stdout, "자식 명령이 파이프(스크립트)를 읽었다")
        self.assertNotIn("child-read:[#", r.stdout)
        self.assertIn("[템플릿] 받기(압축)", r.stdout, "자식 명령 뒤 스크립트가 끝까지 돌지 않았다")
        self.assertNotIn("AFTER_MAIN_SHOULD_NOT_RUN", r.stdout)
        self.assertLess(r.stdout.index("apt-get update"), r.stdout.index("apt-get install -y git"))

    def test_truncated_download_runs_nothing(self):
        text = (TEMPLATE / "install.sh").read_text(encoding="utf-8")
        half = text[: len(text) // 2]
        r = self.pipe(half)
        self.assertNotIn("하네스 템플릿 설치", r.stdout, "끝까지 받기 전에 실행했다")
        self.assertNotIn("child-read", r.stdout)


class EofStops(unittest.TestCase):
    def test_no_input_stops_fast(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        for answers in ("", "이름\nslug\n\n대표님\norchestrator\nmain\nweb\n\n\n\nmain\n\n3\n\n\n\n"):
            t = time.time()
            r = sb.bootstrap("run", "--target", str(sb.projects / "o"), "--offline", input_text=answers)
            self.assertLess(time.time() - t, 3.0 + 2.0, "EOF 에서 반복 질문이 멈추지 않았다")  # 파이썬 시작 여유 2초
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("대화형 입력이 없다", r.stderr)
            self.assertFalse((sb.projects / "o" / "harness.json").exists())

    def test_consent_eof_means_no(self):
        self.assertFalse(boot.ask_yes("q", True, reader=lambda _: (_ for _ in ()).throw(EOFError()), on_eof=False))


class ForeignFolders(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"

    def boot(self, *extra, cfg=None, **env):
        cfg = cfg or new_config(self.sb)
        return self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--offline",
                                 "--non-interactive", "--skip-install", *extra, **env)

    def test_nonempty_target_with_env_stops_then_force_excludes_it(self):
        self.target.mkdir()
        (self.target / ".env").write_text("SECRET=1\n", encoding="utf-8")
        r = self.boot()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("비어 있지 않고", r.stderr)
        self.assertFalse((self.target / ".git").exists())
        r = self.boot("--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = self.sb.env()
        tracked = subprocess.run(["git", "-C", str(self.target), "ls-files"], capture_output=True, text=True, env=env).stdout.split()
        self.assertIn("harness.json", tracked)
        self.assertNotIn(".env", tracked, ".env 를 커밋했다")
        self.assertIn(".env", subprocess.run(["git", "-C", str(self.target), "status", "--porcelain"],
                                             capture_output=True, text=True, env=env).stdout)

    def test_other_git_repo_refused_even_with_force(self):
        env = self.sb.env()
        subprocess.run(["git", "init", "-q", str(self.target)], check=True, env=env)
        (self.target / "a.txt").write_text("x", encoding="utf-8")
        r = self.boot("--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("다른 git 저장소", r.stderr)
        self.assertFalse((self.target / "harness.json").exists())

    def test_foreign_code_repo_is_connect_only(self):
        self.sb.fake_tools(("gh",))
        env = self.sb.env()
        web = self.sb.projects / "web"
        subprocess.run(["git", "init", "-q", "-b", "develop", str(web)], check=True, env=env)
        (web / "app.js").write_text("x", encoding="utf-8")
        (web / ".env").write_text("SECRET=1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(web), "add", "app.js"], check=True, env=env)
        subprocess.run(["git", "-C", str(web), "commit", "-q", "-m", "내 작업"], check=True, env=env)
        head = subprocess.run(["git", "-C", str(web), "rev-parse", "HEAD"], capture_output=True, text=True, env=env).stdout
        cfg = new_config(self.sb, org="zero-org")
        r = self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--non-interactive",
                              "--skip-install", "--create-github", FAKE_GH_VIEW="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("기존 저장소 연결", r.stdout)
        self.assertIn("web 는 이 하네스가 만든 저장소가 아니다", r.stdout)
        creates = [c["args"][2] for c in self.sb.tool_calls() if c["args"][:2] == ["repo", "create"]]
        self.assertNotIn("zero-org/web", creates, "남의 저장소를 push 했다")
        self.assertIn("zero-org/api", creates)
        self.assertEqual(head, subprocess.run(["git", "-C", str(web), "rev-parse", "HEAD"], capture_output=True, text=True, env=env).stdout)
        self.assertFalse((web / "README.md").exists(), "남의 저장소에 뼈대를 썼다")


class UpdateMatchesFirstGeneration(unittest.TestCase):
    def snapshot(self, root: Path):
        out = {}
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_file() and ".git" not in rel.parts and "__pycache__" not in rel.parts:
                out[rel.as_posix()] = (p.read_bytes(), stat.S_IMODE(p.stat().st_mode) & 0o111 != 0)
        return out

    def test_same_files_and_modes(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        cfg = new_config(sb)
        a, b = sb.projects / "a", sb.tmp / "other" / "b"
        for t in (a, b):
            r = sb.bootstrap("run", "--config", str(cfg), "--target", str(t), "--offline", "--non-interactive", "--skip-install")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (b / "harness" / "scripts" / "generate.py").chmod(0o600)
        r = sb.bootstrap("update", "--target", str(b), "--source", str(TEMPLATE))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sa, sb_ = self.snapshot(a), self.snapshot(b)
        self.assertEqual(set(sa), set(sb_), "update 뒤 파일 집합이 첫 생성과 다르다")
        diff = [k for k in sa if sa[k][1] != sb_[k][1]]
        self.assertEqual(diff, [], "실행 권한이 다르다")
        self.assertTrue(sa["bootstrap.sh"][1], "bootstrap.sh 실행 권한")
        self.assertFalse((b / "install.sh").exists())

    def test_tar_links_are_dropped(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\n"
            ti = tarfile.TarInfo("t/run.sh"); ti.size = len(data); ti.mode = 0o755
            tf.addfile(ti, io.BytesIO(data))
            link = tarfile.TarInfo("t/evil"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"
            tf.addfile(link)
            hard = tarfile.TarInfo("t/hard"); hard.type = tarfile.LNKTYPE; hard.linkname = "t/run.sh"
            tf.addfile(hard)
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        boot.safe_extract(buf.getvalue(), sb.tmp / "x")
        self.assertTrue((sb.tmp / "x/t/run.sh").is_file())
        self.assertTrue(os.access(sb.tmp / "x/t/run.sh", os.X_OK))
        self.assertFalse(os.path.lexists(sb.tmp / "x/t/evil"))
        self.assertFalse(os.path.lexists(sb.tmp / "x/t/hard"))


if __name__ == "__main__":
    unittest.main()
