"""install.sh: 도구가 하나도 없는 PATH · 일부만 있는 PATH 에서 고르는 순서, 템플릿 받기(clone · 갱신 · git 없이 압축).

실제 설치는 하지 않는다: --dry-run 으로 할 일만 보거나, 필요한 도구를 미리 가짜로 두어 할 일이 없게 한다.
PATH 는 셸 기본 도구만 링크한 폴더라 이 기기의 git · brew · node 를 보지 않는다(HARNESS_PATH_ONLY=1).
install.ps1 은 이 맥에 PowerShell 이 없어 실행 시험을 못 한다(test_platform 에서 pwsh 가 있으면 문법만 본다).
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import unittest

from helpers import TEMPLATE, Sandbox

BASIC = ("bash", "sh", "uname", "sed", "grep", "head", "cat", "mkdir", "rm", "mv", "dirname", "ls", "sleep",
         "mktemp", "tar", "id", "tr", "env", "gzip")


class Install(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.path = self.sb.tmp / "path"
        self.path.mkdir()
        for tool in BASIC:
            found = shutil.which(tool)
            if found:
                os.symlink(found, self.path / tool)

    def link(self, *tools):
        for tool in tools:
            os.symlink(shutil.which(tool) if tool != "python3" else sys.executable, self.path / tool)

    def fake(self, name, body):
        p = self.path / name
        p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        p.chmod(0o755)

    def install(self, *args, os_name="mac", **env):
        e = {"HOME": str(self.sb.home), "PATH": str(self.path), "HARNESS_OS": os_name, "HARNESS_PATH_ONLY": "1",
             "HARNESS_DIR": str(self.sb.tmp / "harness-template"), "LANG": "C.UTF-8", "HARNESS_NO_TTY": "1",
             "GIT_CONFIG_NOSYSTEM": "1"}
        e.update(env)
        return subprocess.run(["bash", str(TEMPLATE / "install.sh"), *args], capture_output=True, text=True,
                              env=e, stdin=subprocess.DEVNULL, timeout=120)

    def plan(self, out):
        return [line[5:] for line in out.splitlines() if line.startswith("PLAN ")]

    def test_empty_mac_order(self):
        self.fake("xcode-select", "exit 2")
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        steps = [p.split(":")[0] for p in self.plan(r.stdout)]
        self.assertEqual(steps, ["개발자 도구(CLT)", "brew", "python", "node", "템플릿", "bootstrap"])
        joined = "\n".join(self.plan(r.stdout))
        self.assertIn("xcode-select --install", joined)
        self.assertIn("Homebrew/install", joined)
        self.assertIn("brew install python@3.12", joined)
        self.assertIn("brew install node@22", joined)
        self.assertIn("| git | 없음 | 함께 |", r.stdout)

    def test_empty_linux_apt_order(self):
        self.fake("apt-get", "exit 0")
        self.fake("sudo", 'exec "$@"')
        r = self.install("--dry-run", os_name="linux")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        plan = self.plan(r.stdout)
        # apt 로 설치할 것이 있을 때만 목록 갱신이 맨 앞에 들어간다(동의 표에도 보인다)
        self.assertEqual([p.split(":")[0] for p in plan], ["apt 목록", "git", "python", "curl", "node", "템플릿", "bootstrap"])
        self.assertIn("apt-get update -y", plan[0])
        self.assertIn("| apt 목록 | - | 갱신 |", r.stdout)
        self.assertIn("apt-get install -y git", plan[1])
        self.assertIn("apt-get install -y python3", plan[2])
        self.assertIn("nvm", plan[4])

    def test_linux_without_package_manager_is_guidance(self):
        r = self.install("--dry-run", os_name="linux")
        self.assertNotIn("apt 목록", r.stdout, "apt 설치가 없는데 목록 갱신을 넣었다")
        self.assertIn("| 패키지 관리자 | 없음 | 안내 |", r.stdout)
        self.assertIn("| git | 없음 | 안내 | git 을 직접 설치한다 |", r.stdout)

    def test_partial_machine_skips_and_upgrades(self):
        self.fake("xcode-select", "exit 0")
        self.fake("brew", "exit 0")
        self.link("git", "python3")
        self.fake("node", 'echo v18.12.1')
        self.fake("fnm", "exit 0")
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 개발자 도구(CLT) | 있음 | 건너뜀 |", r.stdout)
        self.assertIn("| brew | 있음 | 건너뜀 |", r.stdout)
        self.assertRegex(r.stdout, r"\| git \| [0-9.]+[^|]* \| 건너뜀 \|")
        self.assertRegex(r.stdout, r"\| python \| 3\.\d+\.\d+ \| 건너뜀 \|")
        self.assertIn("| node | v18.12.1 | 올림 | fnm install 22", r.stdout)
        self.assertEqual([p.split(":")[0] for p in self.plan(r.stdout)], ["node", "템플릿", "bootstrap"])

    def test_no_consent_without_tty_or_yes(self):
        self.fake("xcode-select", "exit 2")
        r = self.install()
        self.assertNotIn("[실행]", r.stdout, "동의 없이 설치를 실행했다")
        self.assertIn("무인 실행은 --yes", r.stdout)

    # ---------------------------------------------------------------- 받기

    def template_repo(self):
        """템플릿을 복사해 커밋한 로컬 저장소. 공개 저장소 clone 을 네트워크 없이 흉내 낸다."""
        src = self.sb.tmp / "upstream"
        shutil.copytree(TEMPLATE, src, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        env = self.sb.env()
        subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "t"], check=True, env=env)
        return src

    def ready_linux(self):
        """할 일이 없는 linux: git · python · node 22 · curl 이 있다."""
        self.link("git", "python3", "curl")
        self.fake("node", "echo v22.1.0")

    def test_clone_then_update_then_bootstrap(self):
        self.ready_linux()
        src = self.template_repo()
        env = dict(HARNESS_TEMPLATE_URL=str(src), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        cfg = TEMPLATE / "examples" / "harness.example.json"
        r = self.install("--", "check", "--config", str(cfg), os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("받기(git clone)", r.stdout)
        self.assertIn("설정 통과", r.stdout)
        dest = self.sb.tmp / "harness-template"
        self.assertTrue((dest / ".harness-template").is_file())
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[템플릿] 갱신", r.stdout)
        self.assertIn("bootstrap 은 부르지 않음", r.stdout)

    def git_env(self, src):
        return dict(HARNESS_TEMPLATE_URL=str(src), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

    def rewrite_upstream(self, src, text):
        """공개본 이력을 다시 쓴다(맨 위 커밋을 바꿔 끼움): 받아 둔 사본은 fast-forward 가 안 된다."""
        env = self.sb.env()
        (src / "NEW.txt").write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(src), "add", "NEW.txt"], check=True, env=env)
        subprocess.run(["git", "-C", str(src), "commit", "-q", "--amend", "-m", "rewritten"], check=True, env=env)

    def test_rewritten_upstream_aligns_clean_copy(self):
        """공개 이력이 바뀌어 fast-forward 가 안 돼도 옛 판으로 계속하지 않는다: 고친 파일이 없으면 공개본으로 맞춘다."""
        self.ready_linux()
        src = self.template_repo()
        env = self.git_env(src)
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.rewrite_upstream(src, "새 판")
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("공개본으로 맞춘다", r.stdout)
        self.assertNotIn("있는 것으로 계속한다", r.stdout)
        dest = self.sb.tmp / "harness-template"
        self.assertEqual((dest / "NEW.txt").read_text(encoding="utf-8"), "새 판")

    def test_edited_copy_stops_instead_of_old_template(self):
        self.ready_linux()
        src = self.template_repo()
        env = self.git_env(src)
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dest = self.sb.tmp / "harness-template"
        (dest / "README.md").write_text("내가 고침", encoding="utf-8")
        self.rewrite_upstream(src, "새 판")
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("고친 파일이 있다", r.stdout)
        self.assertIn("README.md", r.stdout)
        self.assertEqual((dest / "README.md").read_text(encoding="utf-8"), "내가 고침", "사람이 고친 파일을 덮었다")

    def test_fetch_failure_stops(self):
        self.ready_linux()
        src = self.template_repo()
        env = self.git_env(src)
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        shutil.rmtree(src)   # 원격이 사라짐(네트워크 끊김 흉내)
        r = self.install("--no-run", os_name="linux", **env)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("옛 판으로 셋업하지 않는다", r.stdout)

    def test_install_ps1_update_never_continues_on_old(self):
        """pwsh 가 없어 실행은 못 한다: install.ps1 도 같은 갈래(받기 실패 · 고친 파일 → 멈춤, 아니면 공개본으로)를 갖는지 본다."""
        ps = (TEMPLATE / "install.ps1").read_text(encoding="utf-8")
        self.assertNotIn("pull --ff-only", ps)
        for needle in ("fetch --quiet origin", "merge --ff-only --quiet '@{u}'", "--untracked-files=no", "reset --quiet --hard '@{u}'"):
            self.assertIn(needle, ps)

    def test_title_option_reaches_bootstrap(self):
        """`curl … | bash -s -- --title 팀장님`: run 없이 온 옵션이 bootstrap.py run 까지 가서 3번(호칭) 질문을 건너뛴다."""
        self.ready_linux()
        src = self.template_repo()
        here = self.sb.tmp / "here"
        here.mkdir()
        env = dict(HARNESS_TEMPLATE_URL=str(src), HARNESS_CALLER_CWD=str(here), GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        r = self.install("--title", "팀장님", "--offline", os_name="linux", **env)
        self.assertNotIn("invalid choice", r.stderr)
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertIn("하네스 셋업 (질문 2개", r.stdout, r.stdout + r.stderr)   # --title 이 닿았다: 호칭은 묻지 않는다
        self.assertEqual(r.returncode, 2, "터미널이 없으니 1번 질문에서 멈춘다")

    def test_without_git_uses_tarball(self):
        self.link("python3", "curl")
        self.fake("node", "echo v22.1.0")
        tgz = self.sb.tmp / "t.tgz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(TEMPLATE, arcname="harness-template-main",
                   filter=lambda ti: None if "__pycache__" in ti.name else ti)
        r = self.install("--no-run", os_name="linux", HARNESS_TARBALL_URL=tgz.as_uri())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("받기(압축)", r.stdout)
        self.assertTrue((self.sb.tmp / "harness-template" / "harness" / "scripts" / "bootstrap.py").is_file())
        # 다시 부르면 같은 자리를 갱신한다(압축본은 표시 파일로 알아본다)
        r = self.install("--no-run", os_name="linux", HARNESS_TARBALL_URL=tgz.as_uri())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 템플릿 |", r.stdout)
        self.assertIn("갱신", r.stdout)

    def test_refuses_foreign_folder(self):
        self.ready_linux()
        other = self.sb.tmp / "harness-template"
        other.mkdir()
        (other / "내것.txt").write_text("x", encoding="utf-8")
        r = self.install("--no-run", os_name="linux", HARNESS_TEMPLATE_URL=str(self.sb.tmp / "없음"))
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertTrue((other / "내것.txt").exists())


if __name__ == "__main__":
    unittest.main()
