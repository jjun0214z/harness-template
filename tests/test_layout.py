"""배치(layout): 작업 레포는 하네스 안 `.repos/`, 워크트리는 하네스 안 `.works/`. layout 칸이 없는 옛 설정은 옆 배치 그대로.

- 안쪽 배치 끝까지: 설치 → add-repo → 지도 · 작업자 정의 → work.py new → doctor → git status → 정리 뒤에도 `.repos/<dir>` 가 남는다
- 옛 설정(layout 없음): 옆 배치 · `.work` 가 그대로다
- 저장소 0개 가드: 세션 시작 줄 · CLAUDE.md · core.md
- 지침 겹침: 역할 범위 문장 · 규칙 보호 훅이 `.repos/` · `.works/` 안을 막지 않는다
- Orca: 저장소를 이름이 아니라 경로로 고른다
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import SCRIPTS, TEMPLATE, Sandbox

sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402

INSIDE = {"repos_dir": ".repos", "works_dir": ".works"}


def base_config(**extra) -> dict:
    cfg = {"project": {"name": "Layout 프로젝트", "slug": "lay", "owner_title": "주임님"},
           "repos": [], "engines": ["claude", "codex"], "runner": "subagent", "packs": ["workflow"],
           "platform": {"python": "python3"}}
    cfg.update(extra)
    return cfg


class Case(unittest.TestCase):
    layout = INSIDE

    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"
        cfg = base_config(**({"layout": self.layout} if self.layout else {}))
        self.cfg = self.sb.tmp / "layout.json"
        self.cfg.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = self.sb.bootstrap("run", "--config", str(self.cfg), "--target", str(self.target), "--offline",
                              "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.run_out = r.stdout

    def add_web(self):
        p = self.sb.tmp / "repo-web.json"
        p.write_text(json.dumps({"key": "web", "base_branch": "develop"}), encoding="utf-8")
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p), "--offline", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def script(self, name, *args, **env):
        return subprocess.run([sys.executable, str(self.target / "harness" / "scripts" / name), *args],
                              capture_output=True, text=True, encoding="utf-8", env=self.sb.env(**env))

    def hook(self, name, payload, **env):
        return subprocess.run([sys.executable, str(self.target / "harness" / "hooks" / name)], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8",
                              env=self.sb.env(CLAUDE_PROJECT_DIR=str(self.target), **env))

    def git(self, repo, *args):
        return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args], capture_output=True,
                              text=True, encoding="utf-8", env=self.sb.env()).stdout.strip()

    def read(self, rel):
        return (self.target / rel).read_text(encoding="utf-8")


class Inside(Case):
    def test_end_to_end(self):
        self.assertIn("배치: 저장소 안쪽 `.repos/` · 워크트리 안쪽 `.works/`", self.run_out)
        self.add_web()
        web = self.target / ".repos" / "web"
        self.assertTrue((web / ".git").exists())
        self.assertFalse((self.sb.projects / "web").exists(), "옆에 만들면 안 된다")
        # 지도 · 작업자 정의 · Codex 작업자 · 무시 목록이 그 경로를 가리킨다
        self.assertIn("| `.repos/web` |", self.read("CLAUDE.md"))
        worker = self.read("plugins/lay/agents/web-worker.md")
        self.assertIn("`.repos/web`", worker)
        self.assertIn("<하네스 폴더>/.works/<작업이름>/web", worker)
        self.assertNotIn("../web", self.read("CLAUDE.md") + worker)
        ignore = self.read(".gitignore")
        self.assertIn(".repos/\n", ignore)
        self.assertIn(".works/\n", ignore)
        self.assertEqual(json.loads(self.read(".claude/settings.json"))["permissions"]["additionalDirectories"], [".repos/web"])
        self.assertEqual(json.loads(self.read("harness.json"))["layout"], INSIDE)
        # 저장소 CLAUDE.md: 하네스는 두 단계 위 · 여기서 연 세션은 작업자
        repo_md = (web / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("`../..`", repo_md)
        self.assertIn("이 저장소 작업자", repo_md)
        # 작업 흐름: 워크트리는 .works 아래
        r = self.script("work.py", "new", "시험", "web")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        wt = self.target / ".works" / "T-0001-시험" / "web"
        self.assertTrue(wt.is_dir(), r.stdout)
        self.assertEqual(self.git(wt, "symbolic-ref", "--short", "HEAD"), "feature/T-0001-시험")
        self.assertFalse((self.sb.projects / ".work").exists())
        r = self.script("work.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("| T-0001 | 시험 | 열림 |", r.stdout)
        # doctor: 실패 0 · 배치 한 줄
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 배치 | 안내 | 저장소 안쪽 `.repos/` · 워크트리 안쪽 `.works/` |", r.stdout)
        self.assertNotIn("| 실패 |", r.stdout)
        self.assertNotIn("배치 어긋남", r.stdout)
        self.assertIn(f"| 저장소 web | 통과 | 로컬만(원격 없음) · {web}", r.stdout.replace(str(web.resolve()), str(web)))
        # 하네스 git 은 두 폴더를 보지 않는다
        status = self.git(self.target, "status", "--porcelain", "--untracked-files=all")
        self.assertNotIn(".repos", status)
        self.assertNotIn(".works", status)
        # 정리 스크립트를 돌려도 저장소 원본은 남는다(최근 손댐 조건도 끈다)
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((web / ".git").exists())
        self.assertTrue((web / "README.md").is_file())
        self.assertNotIn(f"지움 [web] {web}", r.stdout)
        # 작업자 정리: 저장소 원본은 본 체크아웃이라 거절한다
        r = self.script("finish_worker.py", str(web))
        self.assertEqual(r.returncode, 1)
        self.assertIn("본 체크아웃", r.stderr)
        self.assertTrue((web / ".git").exists())
        # 닫기: 워크트리만 치우고 원본은 남는다
        r = self.script("work.py", "done", "T-0001", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(wt.exists())
        self.assertTrue((web / ".git").exists())

    def test_cleanup_removes_only_worktree_when_pushed(self):
        """원격에 다 들어간 워크트리는 지우고, 그 저장소 원본(.repos/<dir>)은 그대로 둔다."""
        bare = self.sb.bare_repo("web", "develop")
        p = self.sb.tmp / "repo-web.json"
        p.write_text(json.dumps({"key": "web", "base_branch": "develop", "url": bare}), encoding="utf-8")
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p), "--offline", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        web = self.target / ".repos" / "web"
        self.assertTrue((web / ".git").exists(), r.stdout)
        self.assertEqual(self.script("work.py", "new", "치울 일", "web").returncode, 0)
        wt = self.target / ".works" / "T-0001-치울-일" / "web"
        self.assertTrue(wt.is_dir())
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("지움 [web]", r.stdout)
        self.assertFalse(wt.exists())
        self.assertTrue((web / ".git").exists())
        self.assertTrue((web / "README.md").is_file())

    def test_orca_worker_in_works_can_be_finished(self):
        """Orca 작업자도 .works 아래에서 일한다: --dispatch 정리가 「Orca 작업 폴더 밖」으로 멈추지 않는다."""
        self.sb.fake_tools(("orca",))
        bare = self.sb.bare_repo("web", "develop")
        p = self.sb.tmp / "repo-web.json"
        p.write_text(json.dumps({"key": "web", "base_branch": "develop", "url": bare}), encoding="utf-8")
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p), "--offline", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.script("work.py", "new", "오르카", "web").returncode, 0)
        wt = self.target / ".works" / "T-0001-오르카" / "web"
        r = self.script("finish_worker.py", str(wt), "--dispatch", "ctx_1", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("안전 조건 통과", r.stdout)
        # .works 밖(그리고 Orca 작업 폴더 밖)은 전처럼 멈춘다
        other = self.sb.tmp / "elsewhere" / "web"
        other.parent.mkdir()
        subprocess.run(["git", "-C", str(self.target / ".repos" / "web"), "worktree", "add", "-q", "-b", "x", str(other), "origin/develop"],
                       check=True, env=self.sb.env())
        r = self.script("finish_worker.py", str(other), "--dispatch", "ctx_1", "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Orca 작업 폴더", r.stderr)

    def test_role_scope_and_guard_hook(self):
        self.add_web()
        for rel in ("CLAUDE.md", "AGENTS.md", "plugins/lay/core.md"):
            text = self.read(rel)
            self.assertIn("하네스 루트에서 연 세션", text, rel)
            self.assertIn("`.repos/` · `.works/` 아래에서 연 세션은 그 저장소의 **작업자**다", text, rel)
        web = self.target / ".repos" / "web"
        self.assertEqual(self.script("work.py", "new", "훅", "web").returncode, 0)
        wt = self.target / ".works" / "T-0001-훅" / "web"
        # 하네스 세션이 저장소 · 워크트리 안의 CLAUDE.md · .claude/ 를 고쳐도 묻지 않는다(하네스 규칙 파일이 아니다)
        for path in (web / "CLAUDE.md", web / ".claude" / "settings.json", wt / "CLAUDE.md", wt / "harness" / "x.py"):
            r = self.hook("guard-rules.py", {"tool_name": "Edit", "cwd": str(self.target), "tool_input": {"file_path": str(path)}})
            self.assertEqual(r.stdout.strip(), "", str(path))
        r = self.hook("guard-rules.py", {"tool_name": "Bash", "cwd": str(self.target),
                                         "tool_input": {"command": f'rm "{web}/CLAUDE.md"'}})
        self.assertEqual(r.stdout.strip(), "")
        # git 저장소가 아닌 폴더여도(받다 만 것 등) .repos 안은 하네스 규칙 파일로 보지 않는다
        stray = self.target / ".repos" / "stray" / "CLAUDE.md"
        r = self.hook("guard-rules.py", {"tool_name": "Write", "cwd": str(self.target), "tool_input": {"file_path": str(stray)}})
        self.assertEqual(r.stdout.strip(), "")
        # 하네스 자신의 규칙 파일은 여전히 묻는다
        r = self.hook("guard-rules.py", {"tool_name": "Edit", "cwd": str(self.target),
                                         "tool_input": {"file_path": str(self.target / "CLAUDE.md")}})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")
        # 고친 뒤 할 일 알림: 저장소 안의 harness.json · plugins/ 는 하네스 것이 아니다
        r = self.hook("remind-check.py", {"tool_name": "Edit", "cwd": str(self.target), "session_id": "s",
                                          "tool_input": {"file_path": str(web / "harness.json")}})
        self.assertEqual(r.stdout.strip(), "")
        # 턴 끝 훅: .gitignore 블록이 낡아도 .repos · .works 때문에 막지 않는다
        self.git(self.target, "add", "-A")
        self.git(self.target, "commit", "-q", "-m", "셋업")
        (self.target / ".gitignore").write_text("", encoding="utf-8")
        self.git(self.target, "update-index", "--assume-unchanged", ".gitignore")
        r = self.hook("harness-sync.py", {"hook_event_name": "Stop", "cwd": str(self.target), "session_id": "s"})
        self.assertEqual(r.stdout.strip(), "", r.stdout)

    def test_doctor_reports_mismatch_and_old_work(self):
        # 설정은 안쪽인데 저장소가 옆에 있다 · 옛 .work 에 워크트리가 남아 있다
        beside = self.sb.projects / "web"
        beside.mkdir()
        subprocess.run(["git", "init", "-q", str(beside)], check=True, env=self.sb.env())
        (self.sb.projects / ".work" / "T-0009-옛-일").mkdir(parents=True)
        raw = json.loads(self.read("harness.json"))
        raw["repos"] = [{"key": "web"}]
        (self.target / "harness.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertIn("| 배치 어긋남 web | 안내 |", r.stdout)
        self.assertIn("| 옛 워크트리 폴더 | 안내 |", r.stdout)
        self.assertIn("T-0009-옛-일", r.stdout)
        self.assertIn("worktree repair", r.stdout)
        self.assertTrue(beside.is_dir(), "자동으로 옮기지 않는다")

    def test_orca_scripts_pick_repo_by_path(self):
        raw = json.loads(self.read("harness.json"))
        raw["runner"] = "orca"
        (self.target / "harness.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(self.sb.bootstrap("generate", "--target", str(self.target)).returncode, 0)
        text = self.read("scripts/orca-worker.sh")
        self.assertNotIn("name:$dir", text)
        self.assertIn('--repo "path:$repo_dir"', text)
        self.assertIn('--worktree "path:$made_wt"', text)
        r = subprocess.run(["bash", "-c", f'. "{self.target}/scripts/orca-common.sh"; place_info'],
                           capture_output=True, text=True, env=self.sb.env(HARNESS_PYTHON=sys.executable))
        self.assertEqual(r.stdout.split("\n")[:3],
                         [str((self.target / ".repos").resolve()), str((self.target / ".works").resolve()), "inside"], r.stderr)


class Beside(Case):
    """layout 칸이 없는 옛 설정: 폴더를 옮기지 않고 예전 동작 그대로."""
    layout = None

    def test_old_config_keeps_sibling_layout(self):
        self.assertIn("배치: 저장소 옆 `../<폴더>` · 워크트리 옆 `../.work/`", self.run_out)
        self.add_web()
        web = self.sb.projects / "web"
        self.assertTrue((web / ".git").exists())
        self.assertFalse((self.target / ".repos").exists())
        self.assertIn("| `../web` |", self.read("CLAUDE.md"))
        worker = self.read("plugins/lay/agents/web-worker.md")
        self.assertIn("`../web`", worker)
        self.assertIn("<저장소 부모>/.work/web-<과제-슬러그>", worker)
        ignore = self.read(".gitignore")
        self.assertIn(".work/\n", ignore)
        self.assertNotIn(".repos/", ignore)
        self.assertNotIn(".works/", ignore)
        self.assertNotIn("하네스 루트에서 연 세션", self.read("CLAUDE.md"))
        self.assertIn("`../orchestrator`", (web / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.read("harness.json"))["layout"], hl.LAYOUT_BESIDE)
        self.assertNotIn("works_dir", json.loads(self.read("plugins/lay/config.json")))
        r = self.script("work.py", "new", "시험", "web")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.sb.projects / ".work" / "T-0001-시험" / "web").is_dir())
        self.assertFalse((self.target / ".works").exists())
        self.assertIn("| T-0001 | 시험 | 열림 |", self.script("work.py").stdout)
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("| 배치 | 안내 | 저장소 옆 `../<폴더>` · 워크트리 옆 `../.work/` |", r.stdout)
        self.assertNotIn("배치 어긋남", r.stdout)
        r = self.script("cleanup_worktrees.py", "--apply", RECENT_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((web / ".git").exists())


class ZeroRepos(Case):
    def start_line(self):
        return self.hook("harness-sync.py", {"hook_event_name": "SessionStart", "cwd": str(self.target)}).stdout

    def test_guard_everywhere_until_a_repo_is_added(self):
        line = self.start_line()
        self.assertIn("[저장소] 등록된 저장소 0개", line)
        self.assertIn("저장소 추가", line)
        self.assertIn("harness/scripts/bootstrap.py add-repo", line)
        self.assertIn("work.py new", line)
        self.assertIn("검토원(`reviewer`)", line)
        for rel in ("CLAUDE.md", "plugins/lay/core.md"):
            text = self.read(rel)
            self.assertIn("등록된 저장소가 0개면 오케스트레이터는 만드는 일을 직접 하지 않는다", text, rel)
            self.assertIn("**지금 0개다.**", text, rel)
            self.assertIn("harness/scripts/bootstrap.py add-repo", text, rel)
        start = self.read("plugins/lay/skills/start/SKILL.md")
        self.assertIn("오케스트레이터가 직접 만들지 않는다", start)
        self.assertIn("등록된 저장소가 0개인데 만드는 일", start)
        self.add_web()
        self.assertNotIn("[저장소]", self.start_line())
        for rel in ("CLAUDE.md", "plugins/lay/core.md"):
            text = self.read(rel)
            self.assertIn("등록된 저장소가 0개면 오케스트레이터는 만드는 일을 직접 하지 않는다", text, rel)
            self.assertNotIn("지금 0개다", text, rel)


class Config(unittest.TestCase):
    def cfg(self, **layout):
        raw = base_config()
        if layout:
            raw["layout"] = layout
        return hl.normalize(raw)

    def test_defaults(self):
        self.assertEqual(hl.default_config()["layout"], INSIDE)          # 새로 묻는 셋업
        self.assertEqual(self.cfg()["layout"], hl.LAYOUT_BESIDE)          # 칸이 없는 옛 설정
        self.assertEqual(hl.validate(self.cfg()), [])
        self.assertEqual(hl.validate(self.cfg(**INSIDE)), [])
        example = hl.load_config(TEMPLATE / "examples" / "harness.example.json")
        self.assertEqual(example["layout"], INSIDE)

    def test_paths(self):
        root = Path("/p/orchestrator")
        inside, beside = self.cfg(**INSIDE), self.cfg()
        self.assertEqual(hl.repos_root(inside, root), root / ".repos")
        self.assertEqual(hl.works_root(inside, root), root / ".works")
        self.assertEqual(hl.repos_root(beside, root), Path("/p"))
        self.assertEqual(hl.works_root(beside, root), Path("/p/.work"))
        self.assertEqual(hl.work_roots(inside, root), [str(root / ".works"), "/p/.work"])
        self.assertEqual(hl.work_roots(beside, root), ["/p/.work"])
        self.assertNotIn(str(root / ".repos"), hl.work_roots(inside, root))  # 정리 뿌리에 저장소 폴더는 없다
        local = {"dir": "x", "source": "local", "path": "/elsewhere/x"}
        self.assertEqual(hl.repo_path(local, hl.repos_root(inside, root)), Path("/elsewhere/x"))
        self.assertEqual(hl.repo_ref(local, inside), "/elsewhere/x")
        self.assertEqual(hl.repo_ref({"dir": "x"}, inside), ".repos/x")
        self.assertEqual(hl.repo_ref({"dir": "x"}, beside), "../x")
        self.assertEqual(hl.repo_ref({"dir": "x"}), "../x")

    def test_bad_values(self):
        for layout in ({"repos_dir": "/abs", "works_dir": ".works"}, {"repos_dir": "repos", "works_dir": ".works"},
                       {"repos_dir": ".repos", "works_dir": "../elsewhere"}, {"repos_dir": ".git", "works_dir": ".works"},
                       {"repos_dir": ".same", "works_dir": ".same"}, {"repos_dir": ".repos/x", "works_dir": ".works"}):
            self.assertTrue(any("layout" in e for e in hl.validate(self.cfg(**layout))), layout)


class CodexBinding(unittest.TestCase):
    """안쪽 배치: 하네스 프로젝트 폴더 안의 저장소 · 워크트리 세션이 하네스 프로젝트로 묶이지 않는다."""

    def test_nested_repo_and_worktree(self):
        path = TEMPLATE / "harness" / "skeleton" / "plugin" / "scripts" / "codex_project_binding.py"
        spec = importlib.util.spec_from_file_location("codex_project_binding_layout", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        h = "/p/orchestrator"
        projects = {"H": {"name": "orchestrator", "roots": [h]}, "W": {"name": "web", "roots": [h + "/.repos/web"]}}
        home = Path("/home/me")
        none = lambda _c: None  # noqa: E731
        web_git = lambda _c: h + "/.repos/web/.git"  # noqa: E731
        self.assertEqual(mod.resolve_project(h, projects, home, none), "H")
        self.assertEqual(mod.resolve_project(h + "/docs", projects, home, lambda _c: h + "/.git"), "H")
        self.assertEqual(mod.resolve_project(h + "/.repos/web", projects, home, none), "W")
        self.assertEqual(mod.resolve_project(h + "/.repos/web/src", projects, home, web_git), "W")
        self.assertEqual(mod.resolve_project(h + "/.works/T-0001-x/web", projects, home, web_git), "W")


if __name__ == "__main__":
    unittest.main()
