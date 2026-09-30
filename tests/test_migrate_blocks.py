"""이미 설치한 하네스 고치기: 마커가 없는 옛 파일에 관리 블록을 한 번 심는다.

관리 블록이 생기기 전 생성기는 규칙 스킬 · 상황판 · 기록 README 를 처음 한 번만 만들고(seed) 다시 손대지 않았다.
그래서 이미 설치한 하네스는 설정에서 만든 표가 영구히 낡은 채 남는다(`replace_blocks` 는 마커가 없으면 아무것도 못 한다).

여기서 재는 것:
① 마커가 없는 옛 파일에 블록이 심기고 표가 실제로 갱신된다
② 사람이 손댄 자리는 한 글자도 안 바뀌고 알림만 간다
③ `doctor` 가 낡은 파일을 이름 · 절 이름과 함께 보여 준다
④ 두 번 돌려도 결과가 같다(멱등, 백업도 늘지 않는다)

옛 파일은 「지금 생성물의 마커를 떼고, 옛 생성기가 넣었던 글로 되돌리는」 방법으로 만든다.
되돌릴 글은 이 파일에 글자 그대로 적는다(생성기 표를 그대로 다시 쓰면 검사가 아니라 거울이 된다).

한계: `OLD_SHAPE` 에서 되돌릴 글이 `None` 인 4개(`git-rules-repos` · `dispatch-table` ·
`deploy-table` · `deploy-cleanup`)는 마커만 떼므로 옛 글이 **지금 생성기 출력 그 자체**다.
이 4개는 생성기를 거울로 쓰기 때문에 여기서 퇴행을 잡지 못한다(생성기 표가 틀어지면
옛 글 후보도 같이 틀어져 검사는 계속 통과하고, 옛 설치본에서는 이관이 조용히 멈춘다).
그래서 `generate.py` 의 `LEGACY_*` · `legacy_contexts` 를 건드릴 때는
**진짜 옛 템플릿(커밋 `f51adf7`) 설치본과 대조해 다시 재야 한다.**
2026-09-30 검토: 실제 `f51adf7` 설치본에 update 를 돌려 8개 블록이 모두 심기는 것을 확인했다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

from helpers import Sandbox, TEMPLATE

SCRIPTS = TEMPLATE / "harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import harnesslib as hl  # noqa: E402

BLOCK = re.compile(r"<!-- harness:begin (\S+)[^>]*-->\n(.*?)<!-- harness:end \1 -->\n", re.S)

# 관리 블록이 생기기 전 생성기가 그 자리에 넣던 글(원격 없음 · 저장소 0개 · 기본 라벨).
OLD_CHECKS = "| 저장소 | 검사 명령 |\n| --- | --- |\n"
OLD_RECORDS_TABLE = (
    "| 무엇 | 어디 |\n"
    "| --- | --- |\n"
    "| 할 일 · 진행 · 결정 대기 · 끝난 일 | GitHub Issues `orchestrator 저장소` "
    "(라벨 `결정대기` · `진행중` · `하네스` · `규칙` · `repo:<키>`) |\n"
    "| 결정의 근거(명령과 출력, 파일과 줄) | 하네스 `docs/기록/YYYY-MM-DD-제목.md`, 이슈에서 링크 |\n"
    "| 지금 집중하는 것 한 장 | 하네스 `상황판.md` (가리키기만 한다) |\n")
OLD_R6 = "- R6. 버그 · 추가요건은 기준 문서에 적지 않는다. 이슈(orchestrator 저장소)에 등록한다.\n"
OLD_BOARD = "> 지금 집중하는 것 한 장. 목록은 GitHub Issues `orchestrator 저장소` 가 갖고, 여기는 가리키기만 한다.\n"
OLD_LINK = "- 이슈(`orchestrator 저장소`)에서 링크한다. 고쳐 쓰지 않고 쌓는다(틀린 것은 새 기록에서 정정한다).\n"
# 파일 → [(블록 이름, 되돌릴 글 또는 None=마커만 뗀다)]
OLD_SHAPE = {
    "plugins/zero/skills/git-rules/SKILL.md": [("git-rules-repos", None)],
    "plugins/zero/skills/task-brief/SKILL.md": [("dispatch-table", None)],
    "plugins/zero/skills/deploy/SKILL.md": [("deploy-table", None), ("deploy-cleanup", None)],
    "plugins/zero/skills/code-convention/SKILL.md": [("checks-table", OLD_CHECKS)],
    "plugins/zero/skills/work-method/SKILL.md": [("work-method-records", OLD_RECORDS_TABLE)],
    "plugins/zero/skills/absolute-rules/SKILL.md": [("absolute-rules-tracker", OLD_R6)],
    "상황판.md": [("board-intro", OLD_BOARD)],
    "docs/기록/README.md": [("records-link", OLD_LINK)],
}
FAKE_REPO = "orchestrator 저장소"  # 원격이 없을 때의 옛 폴백. 유효한 OWNER/REPO 가 아니다


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class Migration(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.target = self.sb.projects / "orchestrator"
        cfg = {"project": {"name": "Zero 프로젝트", "slug": "zero", "owner_title": "대표님"},
               "harness_repo": {"dir": "orchestrator", "remote": "", "base_branch": "main"},
               "repos": [], "engines": ["claude"], "orca": {"enabled": False},
               "platform": {"python": "python3"}}
        path = self.sb.tmp / "zero.json"
        path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        r = self.sb.bootstrap("run", "--config", str(path), "--target", str(self.target),
                              "--offline", "--non-interactive", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.make_old()

    # ---------------------------------------------------------------- 옛 설치본 만들기
    def make_old(self) -> None:
        """생성물을 관리 블록이 없던 시절 모양으로 되돌린다(이미 설치한 하네스 재현)."""
        for rel, blocks in OLD_SHAPE.items():
            path = self.target / rel
            text = path.read_text(encoding="utf-8")
            for name, old in blocks:
                text = self.unblock(text, name, old)
            path.write_text(text, encoding="utf-8")
        self.assertNotIn("harness:begin", (self.target / "상황판.md").read_text(encoding="utf-8"))

    def unblock(self, text: str, name: str, old=None) -> str:
        def sub(m):
            return m.group(0) if m.group(1) != name else (m.group(2) if old is None else old)
        new = BLOCK.sub(sub, text)
        self.assertNotEqual(new, text, f"{name} 블록을 찾지 못했다(생성물 모양이 바뀌었다)")
        return new

    # ---------------------------------------------------------------- 도구
    def read(self, rel: str) -> str:
        return (self.target / rel).read_text(encoding="utf-8")

    def generate(self):
        r = self.sb.bootstrap("generate", "--target", str(self.target))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def add_web(self):
        p = self.sb.tmp / "repo-web.json"
        p.write_text(json.dumps({"key": "web", "base_branch": "main", "stack": "node"}), encoding="utf-8")
        r = self.sb.bootstrap("add-repo", "--target", str(self.target), "--repo", str(p),
                              "--offline", "--skip-install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def backups(self):
        return sorted(p.name for p in self.target.rglob(f"*.{hl.BACKUP_SUFFIX}-*"))

    # ---------------------------------------------------------------- ①
    def test_old_files_get_blocks_and_tables_follow_the_config(self):
        self.add_web()   # 저장소를 더하면 설치 때 굳은 표가 전부 낡는다
        for rel, blocks in OLD_SHAPE.items():
            text = self.read(rel)
            for name, _ in blocks:
                self.assertIn(f"harness:begin {name} ", text, f"{rel}: {name} 블록이 심기지 않았다")
        repos = self.read("plugins/zero/skills/git-rules/SKILL.md")
        self.assertIn("`web-worker`", repos, "git-rules 의 저장소 표가 낡은 채로 남았다")
        self.assertNotIn("코드 저장소가 아직 없다", repos)
        self.assertIn("pnpm test", self.read("plugins/zero/skills/code-convention/SKILL.md"),
                      "code-convention 의 검사 명령 표가 낡았다")
        # 원격이 없는 기존 설치자에게 남던 가짜 저장소 이름 4건
        for rel in ("plugins/zero/skills/work-method/SKILL.md", "plugins/zero/skills/absolute-rules/SKILL.md",
                    "상황판.md", "docs/기록/README.md"):
            self.assertNotIn(FAKE_REPO, self.read(rel), f"{rel}: 없는 저장소 이름이 남았다")

    def test_backup_is_left_before_planting(self):
        self.assertEqual(self.backups(), [])
        self.generate()
        names = self.backups()
        self.assertEqual(len(names), len(OLD_SHAPE), f"블록을 심은 파일마다 백업이 남지 않았다: {names}")
        for rel in OLD_SHAPE:
            base = Path(rel).name
            self.assertTrue(any(n.startswith(f"{base}.{hl.BACKUP_SUFFIX}-") for n in names),
                            f"{rel} 의 백업이 없다: {names}")
            src = self.target / rel
            bak = next(p for p in src.parent.glob(f"{base}.{hl.BACKUP_SUFFIX}-*"))
            self.assertIn("harness:begin", src.read_text(encoding="utf-8"))
            self.assertNotIn("harness:begin", bak.read_text(encoding="utf-8").replace("harness:begin claude-md", ""),
                             f"{rel} 백업이 이사 전 원본이 아니다")
        self.assertIn(f"*.{hl.BACKUP_SUFFIX}-*", self.read(".gitignore"), "백업이 커밋에 섞인다")

    # ---------------------------------------------------------------- ②
    def test_human_edited_table_is_never_overwritten(self):
        rel = "plugins/zero/skills/git-rules/SKILL.md"
        path = self.target / rel
        text = path.read_text(encoding="utf-8")
        # 사람이 그 표에 줄을 더하고 자기 절을 붙였다
        text = text.replace("| `.` (orchestrator) | - | `main` | 배포 없음. 하네스 규칙과 실행 장치 | 오케스트레이터 |\n",
                            "| `.` (orchestrator) | - | `main` | 배포 없음. 하네스 규칙과 실행 장치 | 오케스트레이터 |\n"
                            "| `../사내툴` | 로컬만 | `main` | 내가 손으로 적었다 | 사람 |\n")
        text += "\n## 7. 우리 팀 규칙 (손글씨)\n- release/* 는 금요일에만 만든다.\n"
        path.write_text(text, encoding="utf-8")
        before = md5(path)

        r = self.add_web()
        self.assertEqual(md5(path), before, "사람이 고친 표를 생성기가 덮었다(데이터 손실)")
        self.assertIn("내가 손으로 적었다", path.read_text(encoding="utf-8"))
        self.assertIn("## 7. 우리 팀 규칙 (손글씨)", path.read_text(encoding="utf-8"))
        # 덮지 않는 대신 알린다
        self.assertIn("직접 고쳐야 한다", r.stdout, f"사람이 고친 자리를 알리지 않았다:\n{r.stdout}")
        self.assertIn(rel, r.stdout)
        # 사람이 안 건드린 다른 파일은 그대로 갱신된다(한 자리가 막혀도 나머지는 고친다)
        self.assertIn("pnpm test", self.read("plugins/zero/skills/code-convention/SKILL.md"))

    def test_block_is_not_planted_when_the_old_text_is_gone(self):
        rel = "plugins/zero/skills/absolute-rules/SKILL.md"
        path = self.target / rel
        path.write_text(path.read_text(encoding="utf-8").replace(
            OLD_R6.strip(), "- R6. 버그는 우리 노션 「버그」 보드에 적는다(우리가 정했다)."), encoding="utf-8")
        before = md5(path)
        r = self.generate()
        self.assertEqual(md5(path), before, "사람이 고친 R6 줄을 생성기가 덮었다")
        self.assertIn("직접 고쳐야 한다", r.stdout)
        self.assertIn(rel, r.stdout)

    # ---------------------------------------------------------------- ③
    def test_doctor_names_the_stale_files_and_the_command(self):
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("설정 표", r.stdout, f"doctor 표에 관리 블록 항목이 없다:\n{r.stdout}")
        for rel in OLD_SHAPE:
            self.assertIn(rel, r.stdout, f"doctor 가 낡은 파일 {rel} 을 이름으로 보여 주지 않았다")
        self.assertIn("harness/scripts/generate.py", r.stdout, "고치는 명령을 알려 주지 않았다")
        self.assertIn("나중에 할 일", r.stdout)
        # doctor 는 재기만 한다. 파일을 고치지 않는다
        self.assertEqual(self.backups(), [], "doctor 가 파일을 고쳤다")
        self.assertNotIn("harness:begin board-intro", self.read("상황판.md"))

    def test_doctor_separates_what_a_person_must_fix(self):
        path = self.target / "plugins/zero/skills/work-method/SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "| 지금 집중하는 것 한 장 | 하네스 `상황판.md` (가리키기만 한다) |",
            "| 지금 집중하는 것 한 장 | 하네스 `상황판.md` 와 우리 주간 회의록 |"), encoding="utf-8")
        r = self.sb.bootstrap("doctor", "--target", str(self.target), "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [ln for ln in r.stdout.splitlines() if "사람이 고침" in ln]
        self.assertEqual(len(rows), 1, f"「직접 고쳐야 한다」를 따로 보여 주지 않았다:\n{r.stdout}")
        self.assertIn("plugins/zero/skills/work-method/SKILL.md", rows[0])
        self.assertIn("기록의 자리 표", rows[0], "어느 절인지 적어 주지 않았다")
        self.assertIn("직접 고쳐야 한다", rows[0])

    # ---------------------------------------------------------------- ④
    def test_running_twice_changes_nothing(self):
        self.generate()
        after_first = {p.relative_to(self.target).as_posix(): p.read_bytes()
                       for p in sorted(self.target.rglob("*")) if p.is_file() and ".git/" not in
                       p.relative_to(self.target).as_posix() and hl.BACKUP_SUFFIX not in p.name}
        first_backups = self.backups()
        r = self.generate()
        self.assertIn("바뀐 파일 0", r.stdout, f"두 번째 생성이 파일을 또 바꿨다:\n{r.stdout}")
        self.assertEqual(self.backups(), first_backups, "두 번째 생성이 백업을 또 남겼다")
        after_second = {p.relative_to(self.target).as_posix(): p.read_bytes()
                        for p in sorted(self.target.rglob("*")) if p.is_file() and ".git/" not in
                        p.relative_to(self.target).as_posix() and hl.BACKUP_SUFFIX not in p.name}
        self.assertEqual(after_first, after_second)


class InsertBlocks(unittest.TestCase):
    """판정 규칙 자체: 옛 글이 딱 한 번 · 한 덩이로 있을 때만 심는다."""

    FRESH = "머리\n" + hl.block("t", "| a |\n| - |\n| 새 |") + "꼬리\n"
    OLD = "| a |\n| - |\n| 옛 |\n"

    def run_insert(self, existing, legacy=None):
        return hl.insert_blocks(existing, self.FRESH, {"t": legacy or [self.OLD]})

    def test_plants_when_the_old_text_is_there_as_is(self):
        out, done, left = self.run_insert("머리\n" + self.OLD + "꼬리\n")
        self.assertEqual((done, left), (["t"], []))
        self.assertIn("harness:begin t ", out)
        self.assertIn("| 새 |", out)
        self.assertNotIn("| 옛 |", out)
        self.assertTrue(out.startswith("머리\n") and out.endswith("꼬리\n"), out)

    def test_leaves_it_alone_when_a_person_added_a_row(self):
        existing = "머리\n" + self.OLD + "| 내가 적었다 |\n꼬리\n"
        out, done, left = self.run_insert(existing)
        self.assertEqual((done, left), ([], ["t"]), "사람이 표에 더한 줄을 블록 밖으로 떨어뜨렸다")
        self.assertEqual(out, existing)

    def test_leaves_it_alone_when_the_text_differs(self):
        existing = "머리\n| a |\n| - |\n| 내가 고쳤다 |\n꼬리\n"
        out, done, left = self.run_insert(existing)
        self.assertEqual((done, left), ([], ["t"]))
        self.assertEqual(out, existing)

    def test_leaves_it_alone_when_the_text_is_there_twice(self):
        existing = "머리\n" + self.OLD + "사이\n" + self.OLD + "꼬리\n"
        out, done, left = self.run_insert(existing)
        self.assertEqual((done, left), ([], ["t"]), "어느 자리인지 모르는데 골라 바꿨다")
        self.assertEqual(out, existing)

    def test_does_not_match_in_the_middle_of_a_line(self):
        existing = "머리 " + self.OLD + "꼬리\n"
        self.assertEqual(self.run_insert(existing)[1:], ([], ["t"]))

    def test_keeps_a_block_that_is_already_there(self):
        existing = "머리\n" + hl.block("t", "| a |\n| - |\n| 이미 |") + "꼬리\n"
        out, done, left = self.run_insert(existing)
        self.assertEqual((done, left), ([], []), "이미 있는 블록을 또 심으려 했다")
        self.assertEqual(out, existing)

    def test_no_legacy_text_means_no_touch(self):
        existing = "머리\n" + self.OLD + "꼬리\n"
        out, done, left = hl.insert_blocks(existing, self.FRESH, {})
        self.assertEqual((out, done, left), (existing, [], ["t"]))


if __name__ == "__main__":
    unittest.main()
