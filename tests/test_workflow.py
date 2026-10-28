"""workflow 묶음: 열기(new) → 상태 계산(열림 · 작업중 · 병합됨) → 닫기(done) · 번호 잠금(push) · 세션 시작 한 줄."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import SCRIPTS, Sandbox

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402


class Flow(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        path = self.sb.config(["claude"], "subagent")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["packs"] = ["workflow"]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.target = self.sb.projects / "orchestrator"
        r = self.sb.bootstrap("run", "--config", str(path), "--target", str(self.target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.web = self.sb.projects / "web"

    def git(self, repo, *args, check=True):
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=self.sb.env())
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def work(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / "work.py"), *args],
                           capture_output=True, text=True, encoding="utf-8", env=self.sb.env())
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def status(self):
        return json.loads(self.work("--json").stdout)

    def test_generated_pieces(self):
        self.assertTrue((self.target / "docs" / "작업" / "README.md").is_file())
        claude_md = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("harness/scripts/work.py new <제목>", claude_md)
        self.assertIn("work.py", (self.target / "plugins" / "demo" / "core.md").read_text(encoding="utf-8"))

    def test_open_work_merge_close(self):
        out = self.work("new", "로그인 개선", "web", "api").stdout
        self.assertIn("열었다: T-0001 로그인 개선", out)
        doc = self.target / "docs" / "작업" / "T-0001-로그인-개선.md"
        text = doc.read_text(encoding="utf-8")
        self.assertIn("- 대상 저장소: web, api", text)
        self.assertIn("- 브랜치: `feature/T-0001-로그인-개선`", text)
        self.assertRegex(text, r"- 시작점: web@[0-9a-f]+ · api@[0-9a-f]+")
        space = self.sb.projects / ".work" / "T-0001-로그인-개선"
        wt_web, wt_api = space / "web", space / "api 서버"
        self.assertTrue(wt_web.is_dir() and wt_api.is_dir())
        # rev-parse --abbrev-ref 는 git 2.39 에서 한글 끝 바이트를 잘라 보인다(브랜치는 온전). 전체 이름으로 본다
        self.assertEqual(self.git(wt_web, "symbolic-ref", "HEAD"), "refs/heads/feature/T-0001-로그인-개선")
        self.assertIn("T-0001 착수", self.git(self.target, "log", "--oneline", "-3"))
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "", "문서는 커밋까지 한다")

        [row] = self.status()
        self.assertEqual((row["state"], row["turn"]), ("열림", "시작 전"))

        # 작업자가 커밋했다 → 작업중 · 올릴 차례
        (wt_web / "login.txt").write_text("x\n", encoding="utf-8")
        self.git(wt_web, "add", "login.txt")
        self.git(wt_web, "commit", "-qm", "로그인")
        [row] = self.status()
        self.assertEqual(row["state"], "작업중")
        self.assertTrue(row["turn"].startswith("올릴 차례: web 1커밋"), row["turn"])
        r = self.work("done", "T-0001", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("병합 뒤 닫는다", r.stderr)

        # 올리고 develop 에 병합 → 병합됨 · 닫을 차례 (커밋 없는 api 는 열림이어도 된다)
        self.git(wt_web, "push", "-q", "-u", "origin", "feature/T-0001-로그인-개선")
        self.assertEqual(self.status()[0]["turn"], "작업 중")
        self.git(self.web, "merge", "-q", "--no-ff", "-m", "병합", "feature/T-0001-로그인-개선")
        self.git(self.web, "push", "-q", "origin", "develop")
        [row] = self.status()
        self.assertEqual(row["state"], "병합됨")
        self.assertIn("닫을 차례", row["turn"])
        self.assertIn("work.py done T-0001", row["turn"])

        # 닫기: 워크트리 · 브랜치 정리, 문서는 완료/ 로, 커밋
        out = self.work("done", "1").stdout
        self.assertIn("닫았다: T-0001", out)
        self.assertFalse(space.exists())
        self.assertEqual(self.git(self.web, "branch", "--list", "feature/T-0001-로그인-개선"), "")
        self.assertTrue((self.target / "docs" / "작업" / "완료" / "T-0001-로그인-개선.md").is_file())
        self.assertFalse(doc.exists())
        self.assertIn("T-0001 닫음", self.git(self.target, "log", "--oneline", "-1"))
        self.assertEqual(self.status(), [])
        # 다음 번호는 닫힌 것도 센다
        self.assertIn("T-0002", self.work("new", "다음 일").stdout)

    def test_done_keeps_unsaved_work(self):
        self.work("new", "실험", "web")
        wt = self.sb.projects / ".work" / "T-0001-실험" / "web"
        (wt / "a.txt").write_text("x", encoding="utf-8")
        r = self.work("done", "T-0001", "--force", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("커밋 안 된 변경", r.stderr)
        self.assertTrue(wt.exists(), "지우면 잃는 것이 있는데 지웠다")

    def test_force_closes_unmerged_but_keeps_branch(self):
        self.work("new", "취소할 일", "web")
        self.work("done", "T-0001", "--force")
        self.assertTrue((self.target / "docs" / "작업" / "완료" / "T-0001-취소할-일.md").is_file())

    def test_add_repo_later(self):
        self.work("new", "나중에 늘림")
        self.assertEqual(self.status()[0]["turn"], "대상 저장소 없음")
        self.work("add", "T-0001", "api")
        text = (self.target / "docs" / "작업" / "T-0001-나중에-늘림.md").read_text(encoding="utf-8")
        self.assertIn("- 대상 저장소: api", text)
        self.assertTrue((self.sb.projects / ".work" / "T-0001-나중에-늘림" / "api 서버").is_dir())

    def test_unknown_repo_stops_before_numbering(self):
        r = self.work("new", "오타", "wbe", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("wbe", r.stderr)
        self.assertFalse(list((self.target / "docs" / "작업").glob("T-*.md")), "번호를 받기 전에 멈춘다")

    def test_session_start_line(self):
        self.work("new", "보이는 일", "web")
        r = subprocess.run([sys.executable, str(self.target / "harness" / "hooks" / "harness-sync.py")],
                           input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(self.target)}),
                           capture_output=True, text=True, env=self.sb.env(CLAUDE_PROJECT_DIR=str(self.target)))
        self.assertIn("[작업] 열린 작업 1개: T-0001 보이는 일(열림 · 시작 전)", r.stdout)

    def test_number_lock_retries_when_someone_pushed_first(self):
        """번호를 받고 push 하는 사이 남이 같은 번호를 올렸다: 거절 → 되돌림 → 받음 → 다음 번호로."""
        bare = self.sb.remotes / "orchestrator.git"
        self.git(self.sb.remotes, "init", "-q", "--bare", str(bare))
        self.git(self.target, "remote", "add", "origin", str(bare))
        self.git(self.target, "push", "-q", "-u", "origin", "main")
        other = self.sb.tmp / "other"
        self.git(self.sb.tmp, "clone", "-q", "-b", "main", str(bare), str(other))
        # 첫 push 직전에 남이 T-0001 을 올린다(pre-push 훅으로 흉내, 한 번만)
        marker = self.sb.tmp / "raced"
        hook = self.target / ".git" / "hooks" / "pre-push"
        hook.write_text(f"""#!/bin/sh
[ -f "{marker}" ] && exit 0
touch "{marker}"
cd "{other}" && mkdir -p docs/작업 && echo '# T-0001 남의 일' > docs/작업/T-0001-남의-일.md \\
  && git add docs && git commit -qm 남 && git push -q origin main
exit 0
""", encoding="utf-8")
        hook.chmod(0o755)
        out = self.work("new", "내 일").stdout
        self.assertIn("열었다: T-0002 내 일", out)
        self.assertTrue(marker.exists(), "경쟁 흉내가 돌지 않았다")
        self.git(self.target, "fetch", "-q", "origin")
        names = self.git(self.target, "ls-tree", "-r", "--name-only", "origin/main", "--", "docs/작업")
        self.assertIn("docs/작업/T-0001-남의-일.md", names)
        self.assertIn("docs/작업/T-0002-내-일.md", names)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")


class WorkflowConfig(unittest.TestCase):
    def test_off_means_stop(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        path = sb.config(["claude"], "subagent")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["packs"] = []
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        target = sb.projects / "orchestrator"
        r = sb.bootstrap("run", "--config", str(path), "--target", str(target), "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((target / "docs" / "작업").exists(), "묶음을 안 골랐는데 작업 폴더가 생겼다")
        self.assertNotIn("work.py", (target / "CLAUDE.md").read_text(encoding="utf-8"))
        r = subprocess.run([sys.executable, str(target / "harness" / "scripts" / "work.py")], capture_output=True, text=True, env=sb.env())
        self.assertEqual(r.returncode, 2)
        self.assertIn("workflow 묶음이 꺼져 있다", r.stderr)

    def test_validate(self):
        base = {"project": {"name": "P", "slug": "p"}, "packs": ["workflow"]}
        for wf, word in (({"prefix": "t1"}, "prefix"), ({"dir": "../x"}, "dir"), ({"branch": "feature/x"}, "branch")):
            errs = hl.validate(hl.normalize(dict(base, workflow=wf)))
            self.assertTrue(any(word in e for e in errs), (wf, errs))
        self.assertEqual(hl.validate(hl.normalize(base)), [])


if __name__ == "__main__":
    unittest.main()
