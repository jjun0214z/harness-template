"""검토 권고: 남의 저장소 판별은 manifest 기록(폴더 + 첫 커밋 SHA) · 빈 .git 폴더 target 허용 · 설치 「아니오」 안내."""
from __future__ import annotations

import json
import subprocess
import unittest

from helpers import Sandbox
from test_new_repos import new_config


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env).stdout.strip()


class Ownership(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"
        self.env = self.sb.env()

    def run_boot(self, *extra, **env):
        return self.sb.bootstrap("run", "--config", str(new_config(self.sb, org="zero-org")), "--target", str(self.target),
                                 "--non-interactive", "--skip-install", *extra, **env)

    def test_manifest_records_created_repos_with_first_sha(self):
        r = self.run_boot("--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        manifest = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        for d in ("web", "api"):
            self.assertEqual(manifest["repos"][d], git(self.sb.projects / d, "rev-list", "--max-parents=0", "HEAD", env=self.env))
        self.assertNotIn(self.target.name, manifest["repos"], "하네스 자신은 코드 저장소 기록에 넣지 않는다")

    def test_empty_git_folder_is_foreign(self):
        self.sb.fake_tools(("gh",))
        web = self.sb.projects / "web"
        subprocess.run(["git", "init", "-q", str(web)], check=True, env=self.env)  # 커밋 · 뼈대 없음
        r = self.run_boot("--create-github", FAKE_GH_VIEW="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("기존 저장소 연결", r.stdout)
        creates = [c["args"][2] for c in self.sb.tool_calls() if c["args"][:2] == ["repo", "create"]]
        self.assertNotIn("zero-org/web", creates, "커밋 없는 남의 git 폴더에 원격을 만들었다")
        self.assertIn("zero-org/api", creates)
        manifest = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("web", manifest["repos"])

    def test_same_message_but_different_history_is_foreign(self):
        r = self.run_boot("--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        web = self.sb.projects / "web"
        # 같은 폴더 이름 · 같은 첫 커밋 메시지로 다시 만든 남의 저장소: 메시지로는 못 가리지만 SHA 로는 가린다
        subprocess.run(["rm", "-rf", str(web)], check=True)
        subprocess.run(["git", "init", "-q", str(web)], check=True, env=self.env)
        (web / "a").write_text("a", encoding="utf-8")
        subprocess.run(["git", "-C", str(web), "add", "a"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(web), "commit", "-q", "-m", "chore: 저장소 뼈대 (하네스 생성)"], check=True, env=self.env)
        r = self.run_boot("--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("저장소 web | 안내 | 기존 저장소 연결", r.stdout)


class EmptyShaIsFilled(unittest.TestCase):
    def test_sha_recorded_after_later_commit(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        noid = {"GIT_AUTHOR_NAME": "", "GIT_AUTHOR_EMAIL": "", "GIT_COMMITTER_NAME": "", "GIT_COMMITTER_EMAIL": ""}
        args = ("run", "--config", str(new_config(sb)), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        r = sb.bootstrap(*args, **noid)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["repos"]["web"], "", "커밋 전 저장소는 빈 SHA 로 기록")
        env = sb.env()
        web = sb.projects / "web"
        subprocess.run(["git", "-C", str(web), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(web), "commit", "-q", "-m", "first"], check=True, env=env)
        r = sb.bootstrap(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["repos"]["web"], git(web, "rev-list", "--max-parents=0", "HEAD", env=env))


class EmptyGitTarget(unittest.TestCase):
    def test_user_git_init_first_is_allowed(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        env = sb.env()
        subprocess.run(["git", "init", "-q", "-b", "master", str(target)], check=True, env=env)
        r = sb.bootstrap("run", "--config", str(new_config(sb)), "--target", str(target), "--offline",
                         "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(target, "rev-parse", "--abbrev-ref", "HEAD", env=env), "main", "기준 브랜치 이름을 맞추지 않았다")
        self.assertIn("chore: 하네스 초기 생성", git(target, "log", "--format=%s", env=env))
        self.assertEqual(git(target, "status", "--porcelain", env=env), "")


class ConsentNo(unittest.TestCase):
    def test_no_prints_continue_line(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        sb.fake_tools(("npm",))  # claude CLI 가 없고 npm 이 있으면 「설치」 줄이 생긴다
        r = sb.bootstrap("run", "--config", str(new_config(sb)), "--target", str(sb.projects / "o"), "--offline",
                         "--skip-install", input_text="n\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("설치는 건너뛰고 템플릿 받기와 셋업은 계속한다", r.stdout)
        self.assertFalse([c for c in sb.tool_calls() if c["tool"] == "npm" and c["args"][:1] == ["install"]])


if __name__ == "__main__":
    unittest.main()
