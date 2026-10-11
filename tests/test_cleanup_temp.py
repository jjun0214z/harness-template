#!/usr/bin/env python3
"""cleanup_temp.py 회귀 테스트: Claude 임시 폴더 · 크롬 복제본 · /tmp 검수 찌꺼기 · 보고서 미디어의 미리보기 · 적용 · 거부."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "harness" / "scripts"
CLEAN = SCRIPTS / "cleanup_temp.py"
sys.path.insert(0, str(SCRIPTS))
import cleanup_temp  # noqa: E402

# lstart 를 물으면 FAKE_CHROME_START 시각에 뜬 크롬 하나를 답하고, 나머지는 진짜 ps 로 넘기는 가짜 ps.
FAKE_PS = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import os, sys, time
    args = sys.argv[1:]
    if any("lstart=" in a for a in args):
        start = os.environ.get("FAKE_CHROME_START")
        if start:
            when = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(float(start)))
            print(when + " /Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        sys.exit(0)
    os.execv("/bin/ps", ["ps", *args])
    """
)


class CleanupTempTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.tmp_root = self.root / "claude-501"
        self.tmp_root.mkdir()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.workspaces = self.root / "orca" / "workspaces"
        self.worktree = str(self.workspaces / "front" / "job")
        self.tmp = self.tmp_root / cleanup_temp.claude_tmp_name(self.worktree)
        self.fill(self.tmp / "sess" / "scratchpad" / "copy" / "node_modules")
        self.clones = self.root / "X" / "com.google.Chrome.code_sign_clone"
        self.fakebin = self.root / "fakebin"
        self.fakebin.mkdir()
        (self.fakebin / "ps").write_text(FAKE_PS, encoding="utf-8")
        (self.fakebin / "ps").chmod(0o755)

    def fill(self, folder: Path) -> None:
        folder.mkdir(parents=True)
        (folder / "big.bin").write_bytes(b"x" * 300_000)

    def run_clean(self, *args: str, stdin: str = "", chrome_start: float | None = None):
        env = os.environ.copy()
        env.update(
            HARNESS_CLAUDE_TMP_ROOT=str(self.tmp_root),
            CLAUDE_SESSIONS_DIR=str(self.sessions),
            HARNESS_CHROME_CLONE_GLOB=str(self.clones / "code_sign_clone.*"),
            PATH=f"{self.fakebin}:{env['PATH']}",
        )
        env.pop("FAKE_CHROME_START", None)
        if chrome_start is not None:
            env["FAKE_CHROME_START"] = str(chrome_start)
        return subprocess.run(
            [sys.executable, str(CLEAN), *args], input=stdin, env=env, capture_output=True, text=True
        )

    def sleeper(self, cwd: Path) -> subprocess.Popen:
        child = subprocess.Popen(["sleep", "30"], cwd=cwd)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        return child

    # Claude 임시 폴더
    def test_name_rule_matches_claude_code(self) -> None:
        self.assertEqual(
            cleanup_temp.claude_tmp_name("/home/me/orca/workspaces/한글/job-a"),
            "-home-me-orca-workspaces----job-a",
        )
        self.assertEqual(
            cleanup_temp.claude_tmp_name("/home/me/orca/workspaces/harness/cleanup-temp"),
            "-home-me-orca-workspaces-harness-cleanup-temp",
        )

    def test_dry_run_shows_size_and_keeps(self) -> None:
        result = self.run_clean("claude-tmp", self.worktree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"지울 것 Claude 임시 : {self.tmp} (", result.stdout)
        self.assertRegex(result.stdout, r"\((\d+KB|[\d.]+MB)\)")
        self.assertTrue(self.tmp.is_dir())

    def test_apply_removes_only_that_folder(self) -> None:
        other = self.tmp_root / cleanup_temp.claude_tmp_name(str(self.workspaces / "front" / "other"))
        self.fill(other)
        result = self.run_clean("claude-tmp", "--apply", self.worktree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("지움 Claude 임시", result.stdout)
        self.assertFalse(self.tmp.exists())
        self.assertTrue(other.is_dir())
        self.assertTrue(self.tmp_root.is_dir())

    def test_live_claude_session_keeps(self) -> None:
        (self.sessions / f"{os.getpid()}.json").write_text(
            json.dumps({"pid": os.getpid(), "cwd": self.worktree, "status": "idle"}), encoding="utf-8"
        )
        result = self.run_clean("claude-tmp", "--apply", self.worktree)
        self.assertEqual(result.returncode, 1)
        self.assertIn("도는 Claude 세션이 있음", result.stdout)
        self.assertTrue(self.tmp.is_dir())

    def test_missing_sessions_dir_is_unknown_and_keeps(self) -> None:
        self.sessions.rmdir()
        result = self.run_clean("claude-tmp", "--apply", self.worktree)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("세션 폴더가 없어 도는 세션을 모름", result.stdout)
        self.assertTrue(self.tmp.is_dir())
        orphan = self.run_clean("claude-orphans", "--apply", str(self.workspaces))
        self.assertEqual(orphan.returncode, 1, orphan.stdout)
        self.assertTrue(self.tmp.is_dir())

    def test_live_session_in_subfolder_keeps(self) -> None:
        (self.sessions / f"{os.getpid()}.json").write_text(
            json.dumps({"pid": os.getpid(), "cwd": self.worktree + "/apps/web", "status": "idle"}), encoding="utf-8"
        )
        result = self.run_clean("claude-tmp", "--apply", self.worktree)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(self.tmp.is_dir())

    def test_process_inside_keeps(self) -> None:
        child = self.sleeper(self.tmp / "sess" / "scratchpad")
        result = self.run_clean("claude-tmp", "--apply", self.worktree)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"pid {child.pid}", result.stdout)
        self.assertTrue(self.tmp.is_dir())

    def test_empty_and_relative_worktree_refused(self) -> None:
        for bad in ("", "relative/path"):
            result = self.run_clean("claude-tmp", "--apply", bad)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("절대 경로가 아님", result.stdout)
        self.assertTrue(self.tmp.is_dir())

    def test_bad_tmp_root_refused(self) -> None:
        for root in ("", "/", str(Path.home())):
            env = os.environ.copy()
            env.update(HARNESS_CLAUDE_TMP_ROOT=root, CLAUDE_SESSIONS_DIR=str(self.sessions))
            result = subprocess.run(
                [sys.executable, str(CLEAN), "claude-tmp", "--apply", self.worktree],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, root)
            self.assertIn("뿌리가 이상함", result.stdout)

    def test_check_target_refuses_dangerous_paths(self) -> None:
        home = cleanup_temp.HOME
        ok = lambda name: True  # noqa: E731
        for path, parent in (
            ("", "/x"),
            ("/", "/"),
            (home, os.path.dirname(home)),
            (os.path.join(home, "Documents"), home),
            (os.path.join(home, "Documents", "사진"), os.path.join(home, "Documents")),
            ("/Applications/Game.app", "/Applications"),
            ("/private/tmp/claude-501/../..", "/private/tmp/claude-501"),
            ("relative", "/x"),
        ):
            with self.assertRaises(cleanup_temp.Unsafe, msg=path):
                cleanup_temp.check_target(path, parent, ok)

    def test_symlink_refused(self) -> None:
        target = self.root / "precious"
        target.mkdir()
        link = self.tmp_root / "-link"
        link.symlink_to(target)
        with self.assertRaises(cleanup_temp.Unsafe):
            cleanup_temp.check_target(str(link), str(self.tmp_root), lambda n: True)
        self.assertTrue(target.is_dir())

    # 워크트리가 없어진 임시 폴더
    def test_orphans_remove_gone_and_keep_existing(self) -> None:
        alive = str(self.workspaces / "front" / "alive")
        alive_tmp = self.tmp_root / cleanup_temp.claude_tmp_name(alive)
        # 있는 작업 공간의 하위 폴더에서 연 세션의 임시 폴더
        sub_tmp = self.tmp_root / cleanup_temp.claude_tmp_name(alive + "/apps/web")
        outside_tmp = self.tmp_root / "-Users-someone-else"
        for folder in (alive_tmp, sub_tmp, outside_tmp):
            self.fill(folder)
        preview = self.run_clean("claude-orphans", str(self.workspaces), stdin=alive + "\n")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertIn(f"지울 것 Claude 임시 : {self.tmp}", preview.stdout)
        self.assertTrue(self.tmp.is_dir())
        result = self.run_clean("claude-orphans", "--apply", str(self.workspaces), stdin=alive + "\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.tmp.exists())
        for folder in (alive_tmp, sub_tmp, outside_tmp):
            self.assertTrue(folder.is_dir(), folder)

    # 크롬 복제본
    def make_clone(self, name: str, old: bool) -> Path:
        clone = self.clones / name
        self.fill(clone / "Google Chrome.app" / "Contents")
        if old:
            past = 946684800  # 2000-01-01. mtime 을 생성 시각보다 앞으로 당기면 APFS 는 생성 시각도 당긴다
            os.utime(clone, (past, past))
        return clone

    def test_chrome_clone_older_than_running_chrome_is_removed(self) -> None:
        old = self.make_clone("code_sign_clone.AAA", old=True)
        new = self.make_clone("code_sign_clone.BBB", old=False)
        start = os.stat(new).st_mtime - 3600
        preview = self.run_clean("chrome-clones", chrome_start=start)
        self.assertIn(f"지울 것 크롬 복제본 : {old}", preview.stdout)
        self.assertIn("하루가 안 됨", preview.stdout)
        self.assertTrue(old.is_dir())
        result = self.run_clean("chrome-clones", "--apply", chrome_start=start)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(new.is_dir())

    def test_chrome_clone_in_use_is_kept(self) -> None:
        clone = self.make_clone("code_sign_clone.CCC", old=True)
        child = self.sleeper(clone)
        result = self.run_clean("chrome-clones", "--apply")
        self.assertIn(f"pid {child.pid}", result.stdout)
        self.assertTrue(clone.is_dir())

    def test_long_running_chrome_does_not_block_old_headless_clones(self) -> None:
        """크롬을 며칠 켜 둬도 그 뒤 헤드리스 크롬이 남긴 하루 지난 복제본은 지운다(가장 먼저 뜬 크롬만 기준으로 삼으면 하나도 못 지운다)."""
        old = self.make_clone("code_sign_clone.EEE", old=True)
        born = os.stat(old).st_birthtime
        result = self.run_clean("chrome-clones", "--apply", chrome_start=born - 7 * 86400)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(old.exists())

    def test_clone_born_when_running_chrome_started_is_kept(self) -> None:
        clone = self.make_clone("code_sign_clone.FFF", old=True)
        born = os.stat(clone).st_birthtime
        result = self.run_clean("chrome-clones", "--apply", chrome_start=born - 30)
        self.assertIn("도는 크롬이 뜬 무렵에 생김", result.stdout)
        self.assertTrue(clone.is_dir())

    def test_no_chrome_running_removes_all_unused(self) -> None:
        clone = self.make_clone("code_sign_clone.DDD", old=True)
        result = self.run_clean("chrome-clones", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(clone.exists())



class JunkTest(unittest.TestCase):
    """/tmp 검수 찌꺼기 · 7일 지난 보고서 미디어: 미리보기 계획 안에서만 지운다."""

    OLD = 946684800  # 2000-01-01

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.tmp = self.root / "tmp"
        self.reports = self.root / "reports"
        self.plan = self.root / "plan.json"
        self.tmp.mkdir()
        self.reports.mkdir()

    def old_tree(self, path: Path, files: dict[str, bytes]) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (path / name).write_bytes(data)
            os.utime(path / name, (self.OLD, self.OLD))
        os.utime(path, (self.OLD, self.OLD))
        return path

    def run_junk(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            HARNESS_TMP_ROOT=str(self.tmp),
            HARNESS_REPORTS_ROOT=str(self.reports),
            HARNESS_CLEANUP_PLAN=str(self.plan),
            HARNESS_CLAUDE_TMP_ROOT=str(self.root / "claude-501"),
            HARNESS_SIZE_ONLY_OFF="1",
        )
        return subprocess.run([sys.executable, "-B", str(CLEAN), "junk", *args], env=env, capture_output=True, text=True)

    def test_dry_run_lists_total_and_deletes_nothing(self) -> None:
        audit = self.old_tree(self.tmp / "old-audit-run", {"a.png": b"x" * 5000})
        result = self.run_junk()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"tmp 찌꺼기 : {audit}", result.stdout)
        self.assertIn("합계 전체 : 1개", result.stdout)
        self.assertTrue(audit.is_dir())
        self.assertTrue(self.plan.is_file())

    def test_apply_needs_dry_run_plan(self) -> None:
        audit = self.old_tree(self.tmp / "old-audit", {"a.png": b"x"})
        result = self.run_junk("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertIn("먼저 미리보기", result.stdout)
        self.assertTrue(audit.is_dir())

    def test_apply_deletes_only_planned(self) -> None:
        planned = self.old_tree(self.tmp / "old-audit", {"a.png": b"x"})
        self.run_junk()
        later = self.old_tree(self.tmp / "appeared-later", {"b.png": b"x"})
        result = self.run_junk("--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(planned.exists())
        self.assertTrue(later.is_dir())

    def test_recent_tmp_is_kept_unless_linked_to_finished_work(self) -> None:
        recent = self.tmp / "fresh-check"
        recent.mkdir()
        (recent / "a.png").write_bytes(b"x")
        linked = self.tmp / "check-feature-b"
        linked.mkdir()
        self.run_junk("feature-b")
        self.run_junk("--apply")
        self.assertTrue(recent.is_dir())
        self.assertFalse(linked.exists())

    def test_old_folder_with_one_recent_file_is_kept(self) -> None:
        folder = self.old_tree(self.tmp / "mixed", {"old.png": b"x"})
        (folder / "new.png").write_bytes(b"x")
        os.utime(folder, (self.OLD, self.OLD))
        self.run_junk()
        self.run_junk("--apply")
        self.assertTrue(folder.is_dir())

    def test_in_use_tmp_is_kept(self) -> None:
        folder = self.old_tree(self.tmp / "busy-audit", {"a.png": b"x"})
        child = subprocess.Popen(["sleep", "30"], cwd=folder)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        result = self.run_junk()
        self.assertIn(f"pid {child.pid}", result.stdout)
        self.run_junk("--apply")
        self.assertTrue(folder.is_dir())

    def test_git_repo_and_tool_dirs_are_kept(self) -> None:
        repo = self.old_tree(self.tmp / "scratch-clone", {"a.txt": b"x"})
        (repo / ".git").mkdir()
        tool = self.old_tree(self.tmp / "tmux-501", {"x": b"x"})
        self.run_junk()
        self.run_junk("--apply")
        self.assertTrue(repo.is_dir())
        self.assertTrue(tool.is_dir())

    def test_reports_keep_md_and_recent_media(self) -> None:
        folder = self.reports / "run-a"
        self.old_tree(folder, {"shot.png": b"x" * 100, "clip.mp4": b"x", "data.json": b"{}", "보고서.md": b"# r"})
        recent = folder / "today.png"
        recent.write_bytes(b"x")
        result = self.run_junk()
        self.assertIn("보고서 미디어 · run-a : 3개", result.stdout)
        self.run_junk("--apply")
        self.assertTrue((folder / "보고서.md").is_file())
        self.assertTrue(recent.is_file())
        for name in ("shot.png", "clip.mp4", "data.json"):
            self.assertFalse((folder / name).exists(), name)

    def test_reports_untouched_without_media_dir_and_given_by_flag(self) -> None:
        """보고서 폴더는 설정에 없으므로 --media-dir 로 줄 때만 본다."""
        folder = self.old_tree(self.reports / "old-run", {"shot.png": b"x"})
        env = os.environ.copy()
        env.update(HARNESS_TMP_ROOT=str(self.tmp), HARNESS_CLEANUP_PLAN=str(self.plan),
                   HARNESS_CLAUDE_TMP_ROOT=str(self.root / "claude-501"), HARNESS_SIZE_ONLY_OFF="1")
        env.pop("HARNESS_REPORTS_ROOT", None)
        run = lambda *a: subprocess.run([sys.executable, "-B", str(CLEAN), *a], env=env, capture_output=True, text=True)  # noqa: E731
        self.assertNotIn("보고서 미디어", run("junk").stdout)
        result = run("junk", "--media-dir", str(self.reports))
        self.assertIn("보고서 미디어 · old-run : 1개", result.stdout)
        run("junk", "--apply", "--media-dir", str(self.reports))
        self.assertFalse((folder / "shot.png").exists())

    def test_trash_is_reported_never_emptied(self) -> None:
        """휴지통은 크기와 찌꺼기로 보이는 항목 수만 보이고 사용자 파일과 함께 그대로 둔다."""
        trash = self.root / "Trash"
        self.old_tree(trash / "web-verify.AbC123", {"x.bin": b"x" * 5000})
        self.old_tree(trash / "__pycache__ 1", {"a.pyc": b"x"})
        self.old_tree(trash / "family-photos", {"a.jpg": b"x" * 5000})
        os.environ["HARNESS_TRASH_DIR"] = str(trash)
        self.addCleanup(os.environ.pop, "HARNESS_TRASH_DIR", None)
        result = self.run_junk()
        self.assertIn(f"크기만 휴지통 : {trash} (", result.stdout)
        self.assertIn("작업 찌꺼기로 보이는 것 2개", result.stdout)
        self.assertNotIn("family-photos", result.stdout.split("작업 찌꺼기로 보이는 것", 1)[1])
        self.run_junk("--apply")
        for name in ("web-verify.AbC123", "__pycache__ 1", "family-photos"):
            self.assertTrue((trash / name).is_dir(), name)

    def test_removal_never_goes_to_trash(self) -> None:
        """지우기는 바로 지운다: 휴지통으로 보내는 명령을 쓰지 않는다."""
        source = CLEAN.read_text(encoding="utf-8")
        for bad in ("/usr/bin/trash", "trashItem", "move_to_trash", '"trash"', "send2trash"):
            self.assertNotIn(bad, source)


if __name__ == "__main__":
    unittest.main()
