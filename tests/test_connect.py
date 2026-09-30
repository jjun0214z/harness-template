"""이미 이 컴퓨터에 있는 저장소 · 하네스 폴더 연결(source: local).

연결은 그 저장소의 코드 · 이력 · 원격 · 브랜치를 건드리지 않는다. 하네스 쪽 지도 · 작업자 · 신뢰 · 등록에만 넣고,
동의(connect_files)했을 때만 없는 최소 파일을 더한다. 사람이 가진 CLAUDE.md 는 그대로 둔다.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from helpers import Sandbox, tree_snapshot


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env).stdout.strip()


class ConnectLocalRepo(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.env = self.sb.env()
        # 프로젝트 루트 밖(다른 경로)에 이미 있는 저장소: 원격 · 기본 브랜치 trunk · 사람이 쓴 CLAUDE.md
        bare = self.sb.bare_repo("legacy", "trunk")
        self.legacy = self.sb.tmp / "elsewhere" / "legacy-admin"
        subprocess.run(["git", "clone", "-q", bare, str(self.legacy)], check=True, env=self.env)
        (self.legacy / "CLAUDE.md").write_text("# 사람이 쓴 지침\n지우면 안 된다\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.legacy), "add", "CLAUDE.md"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.legacy), "commit", "-q", "-m", "내 지침"], check=True, env=self.env)
        self.target = self.sb.projects / "orchestrator"

    def config(self, connect_files, engines=("claude", "codex")):
        cfg = {
            "project": {"name": "Link 프로젝트", "slug": "link", "github_org": "", "owner_title": "대표님"},
            "repos": [
                {"key": "web", "stack": "node"},
                {"key": "admin", "source": "local", "path": str(self.legacy), "connect_files": connect_files},
            ],
            "engines": list(engines),
            "orca": {"enabled": False},
            "platform": {"python": "python3"},
        }
        p = self.sb.tmp / f"link-{connect_files}.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return p

    def state(self):
        return (git(self.legacy, "rev-parse", "HEAD", env=self.env), git(self.legacy, "rev-parse", "--abbrev-ref", "HEAD", env=self.env),
                git(self.legacy, "remote", "get-url", "origin", env=self.env), git(self.legacy, "log", "--oneline", env=self.env))

    def run_boot(self, cfg, *extra):
        return self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--offline",
                                 "--non-interactive", "--skip-install", *extra)

    def test_connect_keeps_code_history_remote_branch(self):
        before = self.state()
        r = self.run_boot(self.config(connect_files=True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(before, self.state(), "연결이 커밋 · 브랜치 · 원격을 바꿨다")
        self.assertEqual((self.legacy / "CLAUDE.md").read_text(encoding="utf-8"), "# 사람이 쓴 지침\n지우면 안 된다\n")
        self.assertTrue((self.legacy / "AGENTS.md").is_file(), "없던 AGENTS.md 는 더한다(동의했으므로)")
        settings = json.loads((self.legacy / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["enabledPlugins"], {"link@link": True})
        self.assertIn("CLAUDE.md 이 이미 있어 덮지 않았다", r.stdout)
        self.assertIn("연결(코드 · 이력 · 원격 · 브랜치 그대로)", r.stdout)
        cfg = json.loads((self.target / "harness.json").read_text(encoding="utf-8"))
        admin = cfg["repos"][1]
        self.assertEqual((admin["source"], admin["base_branch"], admin["dir"]), ("local", "trunk", "legacy-admin"),
                         "기준 브랜치는 그 저장소의 기본 브랜치를 감지한다")
        # 하네스 쪽에만 넣는다: 저장소 지도 · 작업자 · Codex 신뢰
        claude_md = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(f"`{self.legacy.resolve()}`", claude_md)
        self.assertTrue((self.target / "plugins/link/agents/admin-worker.md").is_file())
        codex = (self.sb.home / ".codex" / "config.toml")
        self.assertFalse(codex.exists(), "--skip-install 이라 Codex 설정은 안 건드림")
        manifest = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("legacy-admin", manifest["repos"], "연결한 저장소를 우리가 만든 것으로 기록했다")
        # 원격 연결 · push 대상이 아니다
        self.assertNotIn("legacy-admin 원격 연결", r.stdout)

        legacy_files = tree_snapshot(self.legacy)
        harness_files = tree_snapshot(self.target)
        r = self.run_boot(self.config(connect_files=True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(before, self.state())
        self.assertEqual(legacy_files, tree_snapshot(self.legacy), "두 번째 실행이 연결 저장소를 바꿨다")
        self.assertEqual(harness_files, tree_snapshot(self.target), "두 번째 실행이 하네스를 바꿨다")

    def test_connect_without_consent_adds_nothing(self):
        before = tree_snapshot(self.legacy)
        r = self.run_boot(self.config(connect_files=False))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(before, tree_snapshot(self.legacy))
        self.assertIn("connect_files: true", r.stdout)

    def test_plugin_install_only_with_consent(self):
        self.sb.fake_tools(("claude",))
        for consent in (False, True):
            r = self.sb.bootstrap("run", "--config", str(self.config(connect_files=consent, engines=("claude",))),
                                  "--target", str(self.target), "--offline", "--non-interactive")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            installs = [c for c in self.sb.tool_calls() if c["tool"] == "claude" and c["args"][:2] == ["plugin", "install"]
                        and Path(c["cwd"]).resolve() == self.legacy.resolve()]
            if consent:
                self.assertTrue(installs, "동의했는데 연결 저장소에 플러그인을 켜지 않았다")
            else:
                self.assertFalse(installs, "동의 없이 연결 저장소에 claude plugin install 을 불렀다")
                self.assertIn("연결한 저장소라 플러그인을 켜지 않았다", r.stdout)

    def test_missing_path_fails(self):
        cfg = json.loads(self.config(True).read_text(encoding="utf-8"))
        cfg["repos"][1]["path"] = str(self.sb.tmp / "없는폴더")
        p = self.sb.tmp / "bad.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = self.run_boot(p)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("연결할 git 저장소가 없다", r.stdout)

    def test_new_gitignore_hides_secrets_but_connected_untouched(self):
        (self.legacy / ".gitignore").write_text("dist/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.legacy), "add", ".gitignore"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(self.legacy), "commit", "-q", "-m", "ignore"], check=True, env=self.env)
        r = self.run_boot(self.config(connect_files=True))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.legacy / ".gitignore").read_text(encoding="utf-8"), "dist/\n", "연결 저장소 .gitignore 를 바꿨다")
        for repo in (self.target, self.sb.projects / "web"):  # 새로 만든 하네스 · 코드 저장소
            for name in (".env", ".env.local", ".env.production", ".env.example"):
                (repo / name).write_text("X=1\n", encoding="utf-8")
            self.assertEqual(git(repo, "status", "--porcelain", env=self.env), "?? .env.example",
                             f"{repo.name}: 비밀 파일이 보이거나 예시 파일이 가려졌다")


class ConnectHarness(unittest.TestCase):
    def test_existing_folder_as_harness_keeps_human_files(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        env = sb.env()
        h = sb.tmp / "my-ops"
        subprocess.run(["git", "init", "-q", "-b", "main", str(h)], check=True, env=env)
        (h / "CLAUDE.md").write_text("# 우리 운영 메모\n", encoding="utf-8")
        (h / ".gitignore").write_text("secret/\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(h), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(h), "commit", "-q", "-m", "init"], check=True, env=env)
        head = git(h, "rev-parse", "HEAD", env=env)
        cfg = {"project": {"name": "Ops", "slug": "ops", "owner_title": "대표님"},
               "harness_repo": {"source": "local", "path": str(h)},
               "repos": [{"key": "web"}], "engines": ["claude"], "platform": {"python": "python3"}}
        p = sb.tmp / "ops.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = sb.bootstrap("run", "--config", str(p), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(h, "rev-parse", "HEAD", env=env), head, "연결한 하네스에 멋대로 커밋했다")
        self.assertEqual((h / ".gitignore").read_text(encoding="utf-8"), "secret/\n", "원래 있던 .gitignore 를 덮었다")
        self.assertIn(".gitignore 은 원래 있던 파일이라 덮지 않았다", r.stdout)
        text = (h / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("<!-- harness:begin claude-md", text)
        self.assertIn("# 우리 운영 메모", text)
        self.assertTrue((h / "harness.json").is_file())
        self.assertTrue((sb.tmp / "web" / ".git").exists(), "새 저장소는 연결한 하네스 옆에 만든다")
        r = sb.bootstrap("run", "--config", str(p), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((h / ".gitignore").read_text(encoding="utf-8"), "secret/\n", "두 번째 실행에서 보호가 풀렸다")


if __name__ == "__main__":
    unittest.main()
