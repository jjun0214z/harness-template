"""빈 컴퓨터 전제: 저장소 새로 만들기 · 원격 없음/있음/만들기 · gh 없음/로그인 안 됨 · 이름 없음 · 일부 설치 · update."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import TEMPLATE, Sandbox, tree_snapshot


def new_config(sb: Sandbox, org: str = "", engines=("claude",), name="n"):
    cfg = {
        "project": {"name": "Zero 프로젝트", "slug": "zero", "github_org": org, "owner_title": "대표님"},
        "harness_repo": {"dir": "orchestrator", "base_branch": "main"},
        "repos": [
            {"key": "web", "base_branch": "develop", "stack": "node", "deploy": {"main": "상용"}, "ask_on_push": ["main"]},
            {"key": "api", "base_branch": "main", "stack": "python"},
        ],
        "engines": list(engines),
        "orca": {"enabled": False},
        "platform": {"python": "python3"},
    }
    path = sb.tmp / f"{name}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def git(repo: Path, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env).stdout.strip()


class NewRepos(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"

    def run_boot(self, cfg, *extra, **env):
        return self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--non-interactive", *extra, **env)

    def test_local_only_without_gh(self):
        self.sb.fake_tools(("claude",))
        cfg = new_config(self.sb)
        r = self.run_boot(cfg, "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        env = self.sb.env()
        for d, base in (("web", "develop"), ("api", "main")):
            repo = self.sb.projects / d
            self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD", env=env), base)
            self.assertEqual(git(repo, "log", "--oneline", env=env).count("\n"), 0, "첫 커밋 하나")
            for f in ("README.md", ".gitignore", "CLAUDE.md", "AGENTS.md", ".claude/settings.json"):
                self.assertTrue((repo / f).is_file(), f"{d}/{f}")
            settings = json.loads((repo / ".claude/settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["enabledPlugins"], {"zero@zero": True})
            self.assertEqual(git(repo, "remote", env=env), "", "원격을 멋대로 붙였다")
        self.assertEqual(git(self.target, "rev-parse", "--abbrev-ref", "HEAD", env=env), "main")
        self.assertTrue(git(self.target, "log", "--oneline", env=env))
        self.assertEqual(git(self.target, "status", "--porcelain", env=env), "", "하네스 첫 커밋에 빠진 파일")
        self.assertIn("## 나중에 할 일", r.stdout)
        self.assertIn("원격 연결(원하면)", r.stdout)
        cfgj = json.loads((self.target / "harness.json").read_text(encoding="utf-8"))
        self.assertEqual(cfgj["repos"][0]["checks"], ["pnpm lint", "pnpm test"])
        self.assertEqual(cfgj["repos"][1]["checks"], ["python3 -m pytest -q"])

        heads = {d: git(self.sb.projects / d, "rev-parse", "HEAD", env=env) for d in ("web", "api", "orchestrator")}
        snap = tree_snapshot(self.target)
        r = self.run_boot(cfg, "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("있음, 덮지 않음", r.stdout)
        self.assertEqual(heads, {d: git(self.sb.projects / d, "rev-parse", "HEAD", env=env) for d in heads}, "두 번째 실행이 커밋을 더했다")
        self.assertEqual(snap, tree_snapshot(self.target))

    def test_no_git_identity_leaves_todo(self):
        cfg = new_config(self.sb)
        env = {"GIT_AUTHOR_NAME": "", "GIT_AUTHOR_EMAIL": "", "GIT_COMMITTER_NAME": "", "GIT_COMMITTER_EMAIL": ""}
        r = self.run_boot(cfg, "--offline", "--skip-install", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(subprocess.run(["git", "-C", str(self.sb.projects / "web"), "rev-parse", "HEAD"],
                                        capture_output=True).returncode != 0, True, "이름 · 메일 없이 커밋했다")
        self.assertIn('git config --global user.name "<이름>"', r.stdout)

    def test_gh_logged_in_creates_private_remote(self):
        self.sb.fake_tools(("gh",))
        cfg = new_config(self.sb, org="zero-org")
        r = self.run_boot(cfg, "--create-github", "--skip-install", FAKE_GH_VIEW="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        creates = [c["args"] for c in self.sb.tool_calls() if c["args"][:2] == ["repo", "create"]]
        self.assertEqual(sorted(a[2] for a in creates), ["zero-org/api", "zero-org/orchestrator", "zero-org/web"])
        for a in creates:
            self.assertIn("--private", a)
            self.assertIn("--push", a)
        labels = [c["args"][2] for c in self.sb.tool_calls() if c["args"][:2] == ["label", "create"]]
        self.assertIn("repo:web", labels)

    def test_remote_already_exists_connects_only(self):
        self.sb.fake_tools(("gh",))
        cfg = new_config(self.sb, org="zero-org")
        r = self.run_boot(cfg, "--create-github", "--skip-install", FAKE_GH_VIEW="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse([c for c in self.sb.tool_calls() if c["args"][:2] == ["repo", "create"]])
        self.assertEqual(git(self.sb.projects / "web", "remote", "get-url", "origin", env=self.sb.env()),
                         "https://github.com/zero-org/web.git")
        self.assertIn("이미 있어 연결만", r.stdout)

    def test_gh_not_logged_in_is_todo(self):
        self.sb.fake_tools(("gh",))
        cfg = new_config(self.sb, org="zero-org")
        r = self.run_boot(cfg, "--create-github", "--skip-install", FAKE_GH_AUTH="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| GitHub 로그인 | 없음 | 로그인 안 됨(선택) |", r.stdout)
        self.assertIn("gh repo create zero-org/web --private", r.stdout)
        self.assertFalse([c for c in self.sb.tool_calls() if c["args"][:2] == ["repo", "create"]])

    def test_gh_missing_is_todo(self):
        cfg = new_config(self.sb, org="zero-org")
        r = self.run_boot(cfg, "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gh 가 없다(선택)", r.stdout)
        self.assertIn("원격 연결: gh repo create zero-org/orchestrator", r.stdout)

    def test_existing_folder_is_not_touched(self):
        web = self.sb.projects / "web"
        web.mkdir()
        (web / "내 파일.txt").write_text("x", encoding="utf-8")
        r = self.run_boot(new_config(self.sb), "--offline", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((web / ".git").exists())
        self.assertIn("git 이 아닌 폴더가 이미 있어 건드리지 않음", r.stdout)

    def test_refuses_template_itself(self):
        r = self.sb.bootstrap("run", "--config", str(new_config(self.sb)), "--target", str(TEMPLATE), "--non-interactive")
        self.assertEqual(r.returncode, 2)
        self.assertIn("템플릿 저장소 자신", r.stderr)


class PartialMachine(unittest.TestCase):
    """git · node 18 만 있고 fnm 이 있는 컴퓨터, claude 는 로그인돼 있음."""

    def test_plan_upgrades_node_with_fnm_and_skips_logged_in(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        sb.fake_tools(("node", "fnm", "claude", "codex"))
        (sb.home / ".claude.json").write_text('{"oauthAccount": {}}', encoding="utf-8")
        cfg = new_config(sb, engines=("claude", "codex"))
        r = sb.bootstrap("tools", "--config", str(cfg), FAKE_NODE_VERSION="v18.12.1", FAKE_CODEX_LOGIN="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| node | v18.12.1 | 올림 | fnm install 22 |", r.stdout)
        self.assertIn("| git | ", r.stdout)
        self.assertRegex(r.stdout, r"\| git \| git version [^|]+ \| 건너뜀 \|")
        self.assertIn("| claude 로그인 | 통과 | 로그인돼 있어 건너뜀 |", r.stdout)
        self.assertIn("codex 로그인: ! codex login", r.stdout)
        # 동의(--yes) 없이 설치 명령을 부르지 않는다
        self.assertFalse([c for c in sb.tool_calls() if c["tool"] == "fnm" and c["args"][:1] == ["install"]])

    def test_yes_runs_upgrade(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        sb.fake_tools(("node", "fnm"))
        r = sb.bootstrap("tools", "--config", str(new_config(sb)), "--yes", FAKE_NODE_VERSION="v18.12.1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue([c for c in sb.tool_calls() if c["tool"] == "fnm" and c["args"] == ["install", "22"]])


class Update(unittest.TestCase):
    def test_update_refreshes_engine_keeps_human(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        target = sb.projects / "orchestrator"
        r = sb.bootstrap("run", "--config", str(new_config(sb)), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        engine_file = target / "harness" / "scripts" / "generate.py"
        engine_file.write_text("# 옛 엔진\n", encoding="utf-8")
        (target / "harness" / "scripts" / "사라진파일.py").write_text("x", encoding="utf-8")
        skill = target / "plugins/zero/skills/design/SKILL.md"
        skill.write_text("사람이 채움\n", encoding="utf-8")
        r = sb.bootstrap("update", "--target", str(target), "--source", str(TEMPLATE))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(engine_file.read_bytes(), (TEMPLATE / "harness/scripts/generate.py").read_bytes())
        self.assertFalse((target / "harness/scripts/사라진파일.py").exists())
        self.assertEqual(skill.read_text(encoding="utf-8"), "사람이 채움\n")
        self.assertFalse((target / ".harness-template").exists(), "템플릿 표시가 하네스에 복사됐다")


NOID = {"GIT_AUTHOR_NAME": "", "GIT_AUTHOR_EMAIL": "", "GIT_COMMITTER_NAME": "", "GIT_COMMITTER_EMAIL": ""}


def todo_section(out: str) -> str:
    """「나중에 할 일」 목록만(뒤의 안내 문단 전까지)."""
    part = out.split("## 나중에 할 일", 1)[1] if "## 나중에 할 일" in out else ""
    return part.split("\n\n하네스가 준비됐습니다", 1)[0]


class NoIdentityFirstCommit(unittest.TestCase):
    """이름 · 메일이 없는 새 기기: 첫 커밋 안내는 신원 설정 하나(파일 목록 · add 명령 없음), 다시 돌리면 스스로 커밋한다."""

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"

    def test_one_identity_todo_then_rerun_commits(self):
        r = self.sb.bootstrap("run", "--config", str(new_config(self.sb)), "--target", str(self.target),
                              "--offline", "--non-interactive", "--skip-install", **NOID)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        todos = todo_section(r.stdout)
        items = [ln for ln in todos.splitlines() if ln[:1].isdigit() and "첫 커밋" in ln]
        self.assertEqual(len(items), 1, "같은 원인의 첫 커밋 안내가 여러 번 나왔다:\n" + todos)
        self.assertIn("대상 저장소: web, api, orchestrator", todos)
        self.assertIn('git config --global user.name "<이름>"', todos)
        self.assertIn('git config --global user.email "<메일>"', todos)
        self.assertIn("bootstrap.py run", todos)
        for bad in ("add -A", "add --", "add .", "harness/scripts/bootstrap.py\"", "상황판.md\"", "commit -m"):
            self.assertNotIn(bad, todos, f"안내에 {bad!r} 가 들어갔다")
        for d in ("orchestrator", "web", "api"):
            self.assertNotEqual(subprocess.run(["git", "-C", str(self.sb.projects / d), "rev-parse", "HEAD"],
                                               capture_output=True).returncode, 0, f"{d}: 이름 · 메일 없이 커밋했다")
        # 사람 파일: 커밋에 들어가면 안 된다
        (self.target / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.sb.projects / "web" / "notes.txt").write_text("mine\n", encoding="utf-8")
        env = self.sb.env()  # 이름 · 메일이 생긴 상태
        r = subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / "bootstrap.py"), "run",
                            "--offline", "--non-interactive", "--skip-install"],
                           capture_output=True, text=True, env=env, cwd=str(self.target), timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("첫 커밋", todo_section(r.stdout), "신원이 생겼는데 첫 커밋 안내가 남았다")
        self.assertEqual(git(self.target, "log", "--format=%s", env=env), "chore: 하네스 초기 생성")
        self.assertEqual(git(self.target, "status", "--porcelain", env=env), "?? .env", "하네스 첫 커밋에 빠진 파일이 있다")
        self.assertEqual(git(self.sb.projects / "web", "log", "--format=%s", env=env), "chore: 저장소 뼈대 (하네스 생성)")
        self.assertEqual(git(self.sb.projects / "web", "status", "--porcelain", env=env), "?? notes.txt")
        self.assertEqual(git(self.sb.projects / "api", "status", "--porcelain", env=env), "")
        m = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["repos"]["web"], git(self.sb.projects / "web", "rev-list", "--max-parents=0", "HEAD", env=env))
        heads = {d: git(self.sb.projects / d, "rev-parse", "HEAD", env=env) for d in ("orchestrator", "web", "api")}
        r = subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / "bootstrap.py"), "run",
                            "--offline", "--non-interactive", "--skip-install"],
                           capture_output=True, text=True, env=env, cwd=str(self.target), timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(heads, {d: git(self.sb.projects / d, "rev-parse", "HEAD", env=env) for d in heads}, "세 번째 실행이 커밋을 더했다")


if __name__ == "__main__":
    unittest.main()
