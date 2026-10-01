"""설치 직후 온보딩: 마지막 출력 · 루트 README · start 스킬 · 상황판 체크리스트 ·
원격 없을 때 가짜 저장소 이름 금지 · add-repo 의 세션 다시 열기 안내 · 규칙 스킬 표 드리프트 · Orca 기준 ref.

모두 빈 임시 폴더 · 임시 HOME 에서 돈다(helpers.Sandbox).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

from helpers import Sandbox, TEMPLATE

SCRIPTS = TEMPLATE / "harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bootstrap as boot  # noqa: E402
import harnesslib as hl  # noqa: E402

# 하네스가 만드는 문서만 본다(harness/ 아래는 다음 설치를 위해 실려 가는 템플릿 원본이라 {{자리}}가 남아 있다)
def harness_docs(root: Path):
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("harness/") or ".git/" in rel:
            continue
        yield rel, p.read_text(encoding="utf-8")


class Onboarding(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"

    def write_config(self, name="zero.json", **over):
        cfg = {"project": {"name": "Zero 프로젝트", "slug": "zero", "owner_title": "주임님"},
               "harness_repo": {"dir": "orchestrator", "remote": "", "base_branch": "main"},
               "repos": [], "engines": ["claude"], "orca": {"enabled": False},
               "platform": {"python": "python3"}}
        cfg.update(over)
        path = self.sb.tmp / name
        path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return path

    def setup_harness(self, cfg=None):
        r = self.sb.bootstrap("run", "--config", str(cfg or self.write_config()), "--target", str(self.target),
                              "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def add(self, repo, *extra):
        p = self.sb.tmp / f"repo-{repo['key']}.json"
        p.write_text(json.dumps(repo, ensure_ascii=False), encoding="utf-8")
        return self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p),
                                 "--offline", "--skip-install", *extra)

    # ---------------------------------------------------------------- A
    def test_last_output_says_start_and_not_fill_blanks(self):
        r = self.setup_harness()
        self.assertIn("하네스가 준비됐습니다", r.stdout)
        tail = r.stdout[r.stdout.index("하네스가 준비됐습니다"):]
        self.assertIn("시작해", tail)
        self.assertIn(str(self.target), tail)
        self.assertIn("점검해", tail)
        self.assertNotIn("채울 자리", tail, "첫 할 일이 다시 「채울 자리」가 됐다(저장소 0개에서는 할 수 없는 일이다)")

    def test_last_output_names_the_engines_that_were_chosen(self):
        cfg = hl.normalize(json.loads(self.write_config(engines=["codex"]).read_text(encoding="utf-8")))
        text = boot.next_steps(cfg, Path("/somewhere/orchestrator"))
        self.assertIn("`codex`", text)
        self.assertNotIn("`claude`", text, "codex 만 고른 하네스에 claude 를 열라고 했다")
        both = hl.normalize(json.loads(self.write_config(name="b.json", engines=["claude", "codex"]).read_text(encoding="utf-8")))
        self.assertIn("`claude` 또는 `codex`", boot.next_steps(both, Path("/x")))

    # ---------------------------------------------------------------- B
    def test_root_readme_is_seeded_and_never_overwritten(self):
        self.setup_harness()
        readme = self.target / "README.md"
        self.assertTrue(readme.is_file(), "설치본 루트에 README.md 가 없다")
        text = readme.read_text(encoding="utf-8")
        for word in ("시작해", "저장소 추가해", "점검해", "셋업해"):
            self.assertIn(f"**{word}**", text, f"README 안내 표에 {word} 가 없다")
        readme.write_text("내가 고친 안내\n", encoding="utf-8")
        r = self.sb.bootstrap("generate", "--target", str(self.target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("바뀐 파일 0", r.stdout)
        self.assertEqual(readme.read_text(encoding="utf-8"), "내가 고친 안내\n", "다시 생성이 사람이 고친 README 를 덮었다")

    # ---------------------------------------------------------------- C
    def test_start_skill_is_generated_and_listed(self):
        self.setup_harness()
        skill = self.target / "plugins/zero/skills/start/SKILL.md"
        self.assertTrue(skill.is_file(), "start 스킬이 생성되지 않았다")
        desc = [ln for ln in skill.read_text(encoding="utf-8").splitlines() if ln.startswith("description:")]
        self.assertEqual(len(desc), 1, skill)
        for phrase in ("시작해", "뭐부터 해", "처음이야", "다음 뭐야"):
            self.assertIn(phrase, desc[0], f"start 스킬 description 에 사용자 문구 {phrase} 가 없다")
        core = (self.target / "plugins/zero/core.md").read_text(encoding="utf-8")
        self.assertIn("| `start` |", core, "「어떤 스킬을 언제 읽나」 표에 start 가 없다")
        files = json.loads((self.target / ".harness-manifest.json").read_text(encoding="utf-8"))["files"]
        self.assertIn("plugins/zero/skills/start/SKILL.md", files, "관리 파일 목록에 start 스킬이 없다")
        cfg = json.loads((self.target / "harness.json").read_text(encoding="utf-8"))
        self.assertNotIn("start", cfg["skills"]["fill"], "start 는 채울 자리가 아니다")

    # ---------------------------------------------------------------- D
    def test_board_seed_is_a_checklist(self):
        self.setup_harness()
        board = (self.target / "상황판.md").read_text(encoding="utf-8")
        boxes = [ln for ln in board.splitlines() if ln.startswith("- [ ]")]
        self.assertGreaterEqual(len(boxes), 3, f"상황판 시드에 체크리스트가 없다:\n{board}")
        self.assertIn("add-repo", board)
        self.assertIn("원격", board)
        self.assertIn("시작해", board)

    # ---------------------------------------------------------------- E1
    def test_no_invented_repo_name_when_there_is_no_remote(self):
        self.setup_harness()
        fake = re.compile(r"[\w./-]+ 저장소(?=[`\"'\s)]|$)")
        bad = []
        for rel, text in harness_docs(self.target):
            self.assertNotIn("{{", text, f"{rel}: 채우지 못한 자리가 남았다")
            if "gh issue list -R" in text:
                bad.append(f"{rel}: 원격이 없는데 gh issue list -R 을 지시한다")
            for m in fake.finditer(text):
                # 「<하네스 폴더> 저장소」 꼴은 원격이 없을 때의 폴백이었다(유효한 OWNER/REPO 가 아니다)
                if m.group(0).startswith(("orchestrator ", "zero ")):
                    bad.append(f"{rel}: {m.group(0)}")
        self.assertEqual(bad, [])

    def test_remote_harness_still_points_at_issues(self):
        cfg = self.write_config(name="withremote.json", harness_repo={"dir": "orchestrator", "remote": "acme/orchestrator",
                                                                     "base_branch": "main"})
        self.setup_harness(cfg)
        claude_md = (self.target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("gh issue list -R acme/orchestrator", claude_md)

    # ---------------------------------------------------------------- E2
    def test_researcher_and_reviewer_do_not_assert_rules_dir(self):
        self.setup_harness()
        for name in ("researcher.md", "reviewer.md"):
            text = (self.target / "plugins/zero/agents" / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                if ".claude/rules/" in line:
                    self.assertIn("있으면", line, f"{name}: 없는 .claude/rules/ 를 단정형으로 가리킨다")

    # ---------------------------------------------------------------- E4
    def test_label_guidance_shows_even_without_remote(self):
        r = self.setup_harness()
        self.assertIn("나중에 할 일", r.stdout)
        self.assertIn("이슈 추적", r.stdout, "원격이 없을 때 라벨 · 이슈 안내가 한 줄도 없다")
        self.assertIn("--create-github", r.stdout)

    # ---------------------------------------------------------------- E5
    def test_seed_skill_tables_follow_the_config(self):
        self.setup_harness()
        rules = self.target / "plugins/zero/skills/git-rules/SKILL.md"
        self.assertIn("코드 저장소가 아직 없다", rules.read_text(encoding="utf-8"))
        with open(rules, "a", encoding="utf-8") as fh:
            fh.write("\n<!-- 우리 팀 메모 -->\n")
        r = self.add({"key": "web", "base_branch": "develop", "stack": "node"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = rules.read_text(encoding="utf-8")
        self.assertNotIn("코드 저장소가 아직 없다", after, "add-repo 뒤에도 git-rules 의 저장소 표가 낡은 채로 남았다")
        self.assertIn("`web-worker`", after)
        self.assertIn("<!-- 우리 팀 메모 -->", after, "관리 블록 밖의 사람 글을 덮었다")
        deploy = (self.target / "plugins/zero/skills/deploy/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("develop", deploy, "deploy 스킬의 배포 표가 낡았다")
        checks = (self.target / "plugins/zero/skills/code-convention/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("pnpm test", checks, "code-convention 의 검사 명령 표가 낡았다")

    # ---------------------------------------------------------------- F
    def test_add_repo_tells_to_reopen_the_session(self):
        self.setup_harness()
        r = self.add({"key": "web", "base_branch": "main"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("`web-worker`", r.stdout, "새 작업자 이름을 적어 주지 않았다")
        self.assertRegex(r.stdout, r"세션.*다시 열", "세션을 다시 열라는 안내가 없다")
        todos = r.stdout.split("## 나중에 할 일", 1)
        self.assertEqual(len(todos), 2, "add-repo 가 「나중에 할 일」을 내지 않았다")
        self.assertIn("web-worker", todos[1])


class OrcaBaseRef(unittest.TestCase):
    """원격이 없는 저장소에서 orca-worker.sh 가 있지도 않은 origin/<기준> 을 기준 ref 로 넘기지 않는다."""

    FAKE_ORCA = '''#!{python}
import json, os, sys
with open(os.environ["FAKE_ORCA_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
a = sys.argv[1:]
out = {{}}
if a[:2] == ["terminal", "list"]:
    out = {{"terminals": [{{"handle": "w1", "title": "worker", "worktreePath": "/tmp/wt"}}]}}
elif a[:2] == ["terminal", "create"]:
    out = {{"terminal": {{"handle": "c1"}}}}
elif a[:2] == ["orchestration", "run-create"]:
    out = {{"runId": "r1"}}
elif a[:2] == ["orchestration", "worker-start"]:
    out = {{"taskId": "t1", "dispatchId": "d1"}}
elif a[:2] == ["orchestration", "dispatch-show"]:
    out = {{"dispatch": {{"assignee_handle": "w1"}}}}
print(json.dumps({{"result": out}}))
'''

    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash 가 없다")
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"
        self.orca_log = self.sb.tmp / "orca.log"
        self.orca = self.sb.bin / "fake-orca"
        self.orca.write_text(self.FAKE_ORCA.format(python=sys.executable), encoding="utf-8")
        self.orca.chmod(0o755)
        cfg = {"project": {"name": "Orca 프로젝트", "slug": "oc", "owner_title": "주임님"},
               "harness_repo": {"dir": "orchestrator", "remote": "", "base_branch": "main"},
               "repos": [{"key": "app", "base_branch": "main"}], "engines": ["claude"],
               "orca": {"enabled": True, "workspaces_dir": str(self.sb.tmp / "ws")},
               "platform": {"python": "python3"}}
        path = self.sb.tmp / "orca.json"
        path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = self.sb.bootstrap("run", "--config", str(path), "--target", str(self.target), "--offline",
                             "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.app = self.sb.projects / "app"

    def worker(self):
        env = self.sb.env(ORCA_BIN=str(self.orca), FAKE_ORCA_LOG=str(self.orca_log),
                          HARNESS_PYTHON=sys.executable, ORCA_PROJECTS_DIR=str(self.sb.projects))
        return subprocess.run(["bash", str(self.target / "scripts" / "orca-worker.sh"), "app", "일감", "과제 글"],
                              capture_output=True, text=True, env=env, timeout=120)

    def base_branch_arg(self):
        if not self.orca_log.exists():
            return None
        for line in self.orca_log.read_text(encoding="utf-8").splitlines():
            args = json.loads(line)
            if args[:2] == ["orchestration", "worker-start"]:
                return args[args.index("--base-branch") + 1]
        return None

    def test_no_remote_uses_local_base_branch(self):
        env = self.sb.env()
        self.assertNotEqual(subprocess.run(["git", "-C", str(self.app), "rev-parse", "--verify", "--quiet",
                                            "origin/main"], capture_output=True, env=env).returncode, 0,
                            "원격이 없는데 origin/main 이 있다(전제가 깨졌다)")
        r = self.worker()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.base_branch_arg(), "main",
                         "원격이 없는 저장소에 있지도 않은 origin/main 을 기준 ref 로 넘겼다")
        self.assertIn("기준 main", r.stdout)

    def test_remote_still_wins(self):
        env = self.sb.env()
        bare = self.sb.remotes / "app.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=env)
        subprocess.run(["git", "-C", str(self.app), "remote", "add", "origin", str(bare)], check=True, env=env)
        subprocess.run(["git", "-C", str(self.app), "push", "-q", "-u", "origin", "main"], check=True, env=env)
        r = self.worker()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.base_branch_arg(), "origin/main")

    def test_missing_base_branch_stops(self):
        env = self.sb.env()
        subprocess.run(["git", "-C", str(self.app), "checkout", "-q", "-b", "other"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.app), "branch", "-q", "-D", "main"], check=True, env=env)
        r = self.worker()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("기준 브랜치 없음", r.stdout + r.stderr)
        self.assertIsNone(self.base_branch_arg(), "기준 ref 가 없는데 작업자를 띄웠다")


if __name__ == "__main__":
    unittest.main()
