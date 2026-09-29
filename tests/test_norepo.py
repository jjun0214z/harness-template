"""저장소 0개로 셋업하고 나중에 add-repo 로 더하기(새로 만들기 · 이 컴퓨터 폴더 연결), 두 번 실행 멱등."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from helpers import Sandbox, tree_snapshot


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env).stdout.strip()


class NoRepos(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"
        cfg = {"project": {"name": "Empty 프로젝트", "slug": "empty", "owner_title": "대표님"},
               "repos": [], "engines": ["claude", "codex"], "orca": {"enabled": False}, "platform": {"python": "python3"}}
        self.cfg = self.sb.tmp / "empty.json"
        self.cfg.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def setup_empty(self):
        r = self.sb.bootstrap("run", "--config", str(self.cfg), "--target", str(self.target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def add(self, repo, *extra):
        p = self.sb.tmp / f"repo-{repo['key']}.json"
        p.write_text(json.dumps(repo, ensure_ascii=False), encoding="utf-8")
        return self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p), "--offline", "--skip-install", *extra)

    def test_zero_repos_setup_and_doctor(self):
        self.setup_empty()
        claude_md = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("코드 저장소가 아직 없다", claude_md)
        self.assertIn("add-repo", claude_md)
        self.assertEqual(sorted(p.name for p in (self.target / "plugins/empty/agents").iterdir()), ["researcher.md", "reviewer.md"])
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 훅 push 가드 | 통과 |", r.stdout)
        r = self.sb.bootstrap("generate", "--target", str(self.target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("바뀐 파일 0", r.stdout)

    def test_add_new_repo_then_idempotent(self):
        self.setup_empty()
        r = self.add({"key": "web", "base_branch": "develop", "stack": "node"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = self.sb.env()
        web = self.sb.projects / "web"
        self.assertEqual(git(web, "rev-parse", "--abbrev-ref", "HEAD", env=env), "develop")
        self.assertTrue(git(web, "log", "--oneline", env=env))
        cfg = json.loads((self.target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual([r["key"] for r in cfg["repos"]], ["web"])
        self.assertTrue((self.target / "plugins/empty/agents/web-worker.md").is_file())
        self.assertTrue((self.target / ".codex/agents/web-worker.toml").is_file())
        self.assertNotIn("코드 저장소가 아직 없다", (self.target / "CLAUDE.md").read_text(encoding="utf-8"))
        manifest = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertIn("web", manifest["repos"])
        snap, head = tree_snapshot(self.target), git(web, "rev-parse", "HEAD", env=env)
        r = self.add({"key": "web", "base_branch": "develop", "stack": "node"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("이미 있다", r.stdout)
        self.assertEqual(snap, tree_snapshot(self.target), "두 번째 add-repo 가 하네스를 바꿨다")
        self.assertEqual(head, git(web, "rev-parse", "HEAD", env=env))

    def test_add_local_repo_connect(self):
        self.setup_empty()
        env = self.sb.env()
        legacy = self.sb.tmp / "elsewhere" / "legacy"
        subprocess.run(["git", "init", "-q", "-b", "trunk", str(legacy)], check=True, env=env)
        (legacy / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run(["git", "-C", str(legacy), "add", "a.txt"], check=True, env=env)
        subprocess.run(["git", "-C", str(legacy), "commit", "-q", "-m", "mine"], check=True, env=env)
        head = git(legacy, "rev-parse", "HEAD", env=env)
        r = self.add({"key": "legacy", "source": "local", "path": str(legacy)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(head, git(legacy, "rev-parse", "HEAD", env=env))
        cfg = json.loads((self.target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["repos"][0]["source"], cfg["repos"][0]["base_branch"]), ("local", "trunk"))
        self.assertIn(str(legacy.resolve()), (self.target / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertFalse((legacy / "AGENTS.md").exists(), "동의 없이 파일을 더했다")

    def test_add_repo_installs_only_that_repo(self):
        self.setup_empty()
        self.sb.fake_tools(("claude",))
        p = self.sb.tmp / "r.json"
        p.write_text(json.dumps({"key": "api"}), encoding="utf-8")
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        local_installs = [c for c in self.sb.tool_calls() if c["tool"] == "claude" and c["args"][-2:] == ["--scope", "local"]]
        self.assertEqual([Path(c["cwd"]).name for c in local_installs], ["api"])
        text = (self.sb.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn(str((self.sb.projects / "api").resolve()), text)

    def test_interview_blank_first_key(self):
        answers = "\n".join(["Later", "later", "", "대표님", "1", "orchestrator", "main",
                             "",                                   # 첫 저장소 키 빈칸 = 나중에
                             "y", "y", "y", "y", "1", "n"]) + "\n"
        r = self.sb.bootstrap("run", "--detail", "--target", str(self.target), "--offline", "--skip-install", input_text=answers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("저장소는 나중에 추가한다", r.stdout)
        self.assertEqual(json.loads((self.target / "harness.json").read_text(encoding="utf-8"))["repos"], [])
        # 대화형 add-repo
        answers = "\n".join(["app", "1", "3"]) + "\n"   # 기본 add-repo: 키 · 방식 · 스택만
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--offline", "--skip-install", input_text=answers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.sb.projects / "app" / ".git").exists())


if __name__ == "__main__":
    unittest.main()
