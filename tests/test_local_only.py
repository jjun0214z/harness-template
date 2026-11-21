"""원격 없이 이 컴퓨터에만 두는 저장소(repos[].local_only).

참이면: 원격을 조직 이름으로 채우지 않는다 · --create-github 여도 만들거나 push 하지 않는다 · 「원격 연결」 할 일이 없다 ·
지도 · 작업자 정의가 「로컬만」과 로컬 기준 브랜치를 쓴다 · doctor 가 원격 없음을 정상으로 본다 · push 가드 설정에 항목이 없다 ·
정리는 「로컬 기준 브랜치에 병합됐는가」로 판단한다. local_only 가 없는 저장소는 전과 같다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import SCRIPTS, Sandbox

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402
import setup_ask  # noqa: E402


def write_config(sb: Sandbox, art: dict, name: str = "lo") -> Path:
    cfg = {
        "project": {"name": "Zero 프로젝트", "slug": "zero", "github_org": "demo-org", "owner_title": "주임님"},
        "harness_repo": {"dir": "orchestrator", "base_branch": "main"},
        "repos": [
            dict({"key": "art", "base_branch": "main", "description": "그림 폴더"}, **art),
            {"key": "web", "base_branch": "main", "stack": "node"},
        ],
        "engines": ["claude"],
        "runner": "subagent",
        "platform": {"python": "python3"},
    }
    path = sb.tmp / f"{name}.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


class LocalOnlyConfig(unittest.TestCase):
    def raw(self, **art):
        return {"project": {"name": "n", "slug": "zero", "github_org": "demo-org"},
                "repos": [dict({"key": "art"}, **art), {"key": "web"}]}

    def test_remote_is_not_filled_from_org(self):
        cfg = hl.normalize(self.raw(local_only=True))
        self.assertEqual(hl.validate(cfg), [])
        art, web = cfg["repos"]
        self.assertEqual(art["remote"] if "remote" in art else "", "")
        self.assertEqual(web["remote"], "demo-org/web", "local_only 가 없는 저장소는 전과 같이 채운다")
        self.assertEqual(hl.base_ref(art), "main")
        self.assertEqual(hl.base_ref(web), "origin/main")

    def test_false_and_missing_behave_as_before(self):
        for extra in ({}, {"local_only": False}):
            cfg = hl.normalize(self.raw(**extra))
            self.assertEqual(hl.validate(cfg), [])
            self.assertEqual(cfg["repos"][0]["remote"], "demo-org/art")

    def test_conflicting_keys_are_errors(self):
        for extra, word in (({"remote": "demo-org/art"}, "remote"), ({"url": "https://example.com/a.git"}, "remote"),
                            ({"source": "clone"}, "clone"), ({"ask_on_push": ["main"]}, "ask_on_push")):
            errs = hl.validate(hl.normalize(self.raw(local_only=True, **extra)))
            self.assertTrue(any("local_only" in e and word in e for e in errs), f"{extra}: {errs}")
        errs = hl.validate(hl.normalize(self.raw(local_only="yes")))
        self.assertTrue(any("local_only 는 true 또는 false" in e for e in errs), errs)

    def test_interactive_choice_four(self):
        answers = iter(["art", "4", "3"])
        r = setup_ask.ask_repo("demo-org", 1, reader=lambda _q: next(answers), detail=False)
        self.assertEqual(r, {"key": "art", "local_only": True, "stack": "none"})
        # 자세히: 원격 · push 브랜치를 묻지 않는다
        asked = []
        answers = iter(["art", "4", "art", "main", "그림", "3", "", ""])

        def reader(q):
            asked.append(q)
            return next(answers)
        r = setup_ask.ask_repo("demo-org", 1, reader=reader, detail=True)
        self.assertTrue(r["local_only"])
        self.assertNotIn("remote", r)
        self.assertNotIn("ask_on_push", r)
        self.assertFalse([q for q in asked if "GitHub 원격" in q or "push 때" in q], asked)
        cfg = hl.normalize({"project": {"name": "n", "slug": "zero", "github_org": "demo-org"}, "repos": [r]})
        self.assertEqual(hl.validate(cfg), [])


class LocalOnlyHarness(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.sb.fake_tools(("claude", "gh"))
        self.target = self.sb.projects / "orchestrator"
        self.env = self.sb.env()

    def boot(self, cfg, *extra, **env):
        return self.sb.bootstrap("run", "--config", str(cfg), "--target", str(self.target), "--non-interactive",
                                 "--skip-install", *extra, **env)

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              env=self.env)

    def script(self, name, *args, **env):
        return subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / name), *args],
                              capture_output=True, text=True, encoding="utf-8", env=self.sb.env(**env),
                              cwd=str(self.sb.tmp))

    def gh_calls(self):
        if not self.sb.log.exists():
            return []
        rows = [json.loads(line) for line in self.sb.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r["args"] for r in rows if r["tool"] == "gh"]

    def read(self, rel):
        return (self.target / rel).read_text(encoding="utf-8")

    def test_create_github_skips_local_only_repo(self):
        r = self.boot(write_config(self.sb, {"local_only": True}), "--create-github", FAKE_GH_VIEW="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        created = [a[2] for a in self.gh_calls() if a[:2] == ["repo", "create"]]
        self.assertIn("demo-org/web", created, "local_only 가 없는 저장소는 전과 같이 만든다")
        self.assertNotIn("demo-org/art", created)
        art = self.sb.projects / "art"
        # 이슈 라벨 repo:art 는 하네스 원격의 것이라 그대로 만든다. art 저장소 자체로는 gh 를 부르지 않는다
        self.assertFalse([a for a in self.gh_calls() if "demo-org/art" in a or str(art) in a], "art 로 gh 를 불렀다")
        self.assertTrue((art / ".git").exists())
        self.assertEqual(self.git(art, "remote").stdout.strip(), "")
        self.assertNotIn("art 원격 연결", r.stdout)
        saved = json.loads(self.read("harness.json"))["repos"][0]
        self.assertTrue(saved["local_only"])
        self.assertFalse(saved.get("remote"))

    def test_no_remote_todo_without_create(self):
        r = self.boot(write_config(self.sb, {"local_only": True}), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("art 원격 연결", r.stdout)
        self.assertIn("web 원격 연결", r.stdout)

    def test_generated_files_say_local_only(self):
        r = self.boot(write_config(self.sb, {"local_only": True}), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 없는 원격 이름이 생성물 어디에도 없다
        for p in self.target.rglob("*"):
            if p.is_file() and ".git" not in p.parts and p.suffix in (".md", ".json", ".toml"):
                self.assertNotIn("demo-org/art", p.read_text(encoding="utf-8", errors="replace"), str(p))
        claude = self.read("CLAUDE.md")
        row = next(line for line in claude.splitlines() if "`art-worker`" in line and line.startswith("|"))
        self.assertIn("로컬만(원격 없음 · push 안 함)", row)
        worker = self.read("plugins/zero/agents/art-worker.md")
        self.assertNotIn("fetch origin", worker)
        self.assertNotIn("origin/main", worker)
        self.assertIn("-b <과제-슬러그> main`", worker)
        self.assertIn("기준 브랜치: `main` (로컬 브랜치", worker)
        web = self.read("plugins/zero/agents/web-worker.md")
        self.assertIn("git -C <저장소> fetch origin && git -C <저장소> worktree add", web)
        self.assertIn("기준 브랜치: `origin/main` (", web)
        self.assertIn("원격이 없는 저장소(art)", self.read("plugins/zero/agents/reviewer.md"))
        guard = json.loads(self.read("plugins/zero/config.json"))["repos"]
        self.assertIn("demo-org/web", guard)
        self.assertFalse([k for k in guard if k.split("/")[-1] == "art"], guard)
        # doctor: 원격 없음이 정상
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 저장소 art | 통과 | 로컬만(local_only · 원격 없음이 정상)", r.stdout)
        self.assertNotIn("art 원격 연결", r.stdout)

    def test_reviewer_unchanged_without_local_only(self):
        r = self.boot(write_config(self.sb, {}), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("원격이 없는 저장소", self.read("plugins/zero/agents/reviewer.md"))
        self.assertIn("기준 브랜치: `origin/main` (", self.read("plugins/zero/agents/art-worker.md"))
        self.assertIn("demo-org/art", json.loads(self.read("plugins/zero/config.json"))["repos"])

    def test_remote_with_local_only_is_config_error(self):
        r = self.boot(write_config(self.sb, {"local_only": True, "remote": "demo-org/art"}), "--offline")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("local_only", r.stdout + r.stderr)
        self.assertFalse((self.sb.projects / "art").exists())

    def worktree(self, repo, name):
        wt = self.sb.tmp / "work" / name
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = self.git(repo, "worktree", "add", "-q", "-b", name, str(wt), "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        (wt / f"{name}.txt").write_text("x\n", encoding="utf-8")
        self.git(wt, "add", f"{name}.txt")
        self.assertEqual(self.git(wt, "commit", "-q", "-m", name).returncode, 0)
        return wt

    @unittest.skipIf(os.name == "nt", "작업자 정리는 posix 에서만 시험한다")
    def test_cleanup_judges_by_local_base_branch(self):
        r = self.boot(write_config(self.sb, {"local_only": True}), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        art = self.sb.projects / "art"
        one, two = self.worktree(art, "one"), self.worktree(art, "two")
        # 병합 전: 둘 다 남긴다(잘못 지우지 않는다)
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("로컬 기준 브랜치(main)에 병합 안 된 커밋 1 개", r.stdout)
        self.assertTrue(one.is_dir() and two.is_dir())
        r = self.script("finish_worker.py", str(one))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("로컬 기준 브랜치(main)에 병합 안 된 커밋 1개", r.stderr)
        self.assertTrue(one.is_dir())
        # 로컬 기준 브랜치에 병합하면 지울 수 있다(영원히 못 지우지 않는다)
        self.assertEqual(self.git(art, "merge", "-q", "--no-ff", "-m", "merge one", "one").returncode, 0)
        r = self.script("finish_worker.py", str(one))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(one.exists())
        self.assertEqual(self.git(art, "branch", "--list", "one").stdout.strip(), "")
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertTrue(two.is_dir(), "병합 안 된 워크트리를 지웠다")
        self.assertEqual(self.git(art, "merge", "-q", "--no-ff", "-m", "merge two", "two").returncode, 0)
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("로컬 기준 브랜치(main)에 다 병합됨", r.stdout)
        self.assertFalse(two.exists())
        self.assertTrue((art / ".git").exists())
        self.assertIn("merge two", self.git(art, "log", "--oneline", "main").stdout)


if __name__ == "__main__":
    unittest.main()
