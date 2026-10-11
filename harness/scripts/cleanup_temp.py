#!/usr/bin/env python3
"""작업자가 남긴 임시 찌꺼기를 치운다. 다시 만들 수 있는 것만 지운다(macOS · Linux. Windows 는 건너뛴다).

치우는 것:
- Claude 세션 임시 폴더: <tmp>/claude-<uid>/<워크트리 경로의 영숫자 외 문자를 - 로 바꾼 이름>/
  (세션마다 scratchpad 가 있고, 검사용 사본 + 패키지 설치가 개당 1GB 넘게 쌓일 수 있다)
- 크롬 임시 복제본(macOS): /private/var/folders/*/*/X/com.google.*code_sign_clone/code_sign_clone.*
  (크롬을 띄울 때마다 앱 전체를 복제하고, 헤드리스 크롬이 죽으면 남는다)
- /tmp 검수 찌꺼기: /tmp 바로 아래 내 것 중 24시간 넘게 안 쓰였거나(안의 가장 최근 수정 기준) 끝난 작업 이름이 든 것
- (선택) --media-dir 로 준 보고서 폴더의 7일 지난 png · jpg · webp · gif · mp4 · mov · webm · json. md 등은 남긴다

절대 지우지 않는 것(경로 검사가 막는다): ~/Documents 전체, 홈 폴더 자체, / , 빈 경로, 설치 앱(/Applications 등).

지우기 전 확인(하나라도 걸리거나 모르면 남긴다):
- 경로가 계산된 절대 경로 그대로이고(심볼릭 링크 아님), 정해진 부모 폴더의 바로 아래다
- 그 안의 파일을 열었거나 그 안을 작업 폴더로 쓰는 프로세스가 없다(lsof). 명령줄에 경로가 든 프로세스도 없다(ps)
- Claude 임시 폴더: 같은 이름이 되는 작업 폴더에서 도는 살아 있는 Claude 세션이 없다
- 크롬 복제본: 도는 크롬이 뜬 무렵(앞 1분 ~ 뒤 10분)에 생긴 것이 아니고 하루가 지났다
- /tmp: 도구 자리(claude-* · tmux-* · com.apple.* · 소켓 등) · 소켓이 든 폴더 · git 저장소는 남긴다
- junk 는 미리보기가 목록 · 합계를 보이고 계획(~/.cache/harness/cleanup-junk-plan.json)에 적는다.
  --apply 는 24시간 안의 계획이 있어야 하고, 그 계획 안에서 다시 확인해 통과한 것만 지운다
- 크기만 보고: ~/.codex/sessions(대화 기록) · Claude 임시 뿌리. 지우지 않는다
- 휴지통(~/.Trash)은 크기와 작업 찌꺼기로 보이는 항목 수만 보인다. 사용자 파일이 섞여 있어 비우기는 사용자가 한다.
  이 스크립트는 휴지통으로 보내지 않고 바로 지운다. 작업자도 `trash` · Finder 휴지통 대신 정확한 경로를 `rm -rf` 한다
  (휴지통으로 보낸 찌꺼기는 용량이 그대로 남는다)

사용:
  cleanup_temp.py claude-tmp [--apply] <워크트리 경로>...   그 워크트리들의 Claude 임시 폴더
  cleanup_temp.py claude-orphans [--apply] <작업 공간 뿌리>...  워크트리가 없어진 Claude 임시 폴더
                                                            (지금 있는 워크트리 경로를 표준 입력으로 한 줄씩)
  cleanup_temp.py chrome-clones [--apply]                   크롬 임시 복제본
  cleanup_temp.py junk [--apply] [--media-dir <폴더>] [끝난작업이름,...]   /tmp 검수 찌꺼기 · (선택) 보고서 미디어
--apply 가 없으면 크기와 판정만 출력한다.
종료 코드: 0 · 1 지우기 실패 또는 확인 불가로 남김 · 2 사용법
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.realpath(os.environ.get("HOME") or str(Path.home()))
_TMP = "/private/tmp" if os.path.isdir("/private/tmp") else "/tmp"
CLAUDE_TMP_ROOT = os.environ.get("HARNESS_CLAUDE_TMP_ROOT", f"{_TMP}/claude-{getattr(os, 'getuid', lambda: 0)()}")
CLAUDE_SESSIONS = os.environ.get("CLAUDE_SESSIONS_DIR", str(Path.home() / ".claude" / "sessions"))
CHROME_CLONE_GLOB = os.environ.get(
    "HARNESS_CHROME_CLONE_GLOB",
    "/private/var/folders/*/*/X/com.google.*code_sign_clone/code_sign_clone.*",
)
# 크롬 본체(헬퍼 · 크래시 처리기 제외). 이 중 가장 먼저 뜬 것보다 뒤에 생긴 복제본은 남긴다.
CHROME_MAIN = re.compile(r"(Google Chrome|Chromium|Chrome for Testing)(\.app)?/Contents/MacOS/")
NEVER = [os.path.join(HOME, "Documents"), "/Applications", os.path.join(HOME, "Applications")]
# Claude Code 는 200자 넘는 이름을 잘라 해시를 붙인다. 그 이름은 계산으로 맞출 수 없어 건너뛴다.
MAX_NAME = 200


class Unsafe(Exception):
    """지우면 안 되거나 안전한지 모른다. 메시지가 이유다."""


def claude_tmp_name(worktree: str) -> str:
    """Claude Code 가 작업 폴더로 임시 폴더 이름을 만드는 규칙: ASCII 영숫자 외 문자는 모두 - ."""
    return re.sub(r"[^A-Za-z0-9]", "-", worktree)


def check_target(path: str, parent: str, name_ok) -> str:
    """지울 경로를 문자열로 검사한다. 통과하면 그 경로를 돌려준다."""
    if not path or not isinstance(path, str):
        raise Unsafe("빈 경로")
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise Unsafe(f"정규화된 절대 경로가 아님: {path!r}")
    if path == "/" or path == HOME or path in ("/private", "/tmp", "/private/tmp", "/var", "/private/var"):
        raise Unsafe(f"지우면 안 되는 경로: {path}")
    for root in NEVER:
        if path == root or path.startswith(root + os.sep):
            raise Unsafe(f"지우지 않는 위치 안이다: {path}")
    if not parent or parent == "/" or parent == HOME:
        raise Unsafe(f"부모 폴더가 이상함: {parent!r}")
    if os.path.dirname(path) != parent:
        raise Unsafe(f"정해진 부모({parent}) 바로 아래가 아님: {path}")
    if not name_ok(os.path.basename(path)):
        raise Unsafe(f"이름이 정해진 꼴이 아님: {path}")
    if os.path.islink(path):
        raise Unsafe(f"심볼릭 링크라 따라가지 않음: {path}")
    if os.path.realpath(path) != path:
        raise Unsafe(f"실제 경로가 다름({os.path.realpath(path)}): {path}")
    return path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", env={**os.environ, "LC_ALL": "C"})


class Usage:
    """프로세스가 쓰는 경로 목록. 한 번만 재고 여러 경로에 쓴다. 못 재면 모든 경로를 쓰는 중으로 본다."""

    def __init__(self) -> None:
        self.error = ""
        self.files: list[tuple[int, str]] = []
        self.commands: list[tuple[int, str]] = []
        try:
            lsof = run(["lsof", "-n", "-P", "-Fpn"])
            # lsof 는 읽지 못한 프로세스가 하나라도 있으면 1 을 낸다. 출력이 비었을 때만 모름으로 본다.
            if lsof.returncode != 0 and not lsof.stdout:
                self.error = f"lsof 종료 {lsof.returncode}"
            pid = 0
            for line in lsof.stdout.splitlines():
                if line[:1] == "p":
                    pid = int(line[1:])
                elif line[:1] == "n":
                    self.files.append((pid, line[1:]))
            ps = run(["ps", "-axww", "-o", "pid=,command="])
            if ps.returncode != 0 or not ps.stdout:
                self.error = self.error or f"ps 종료 {ps.returncode}"
            for line in ps.stdout.splitlines():
                head, _, command = line.strip().partition(" ")
                if head.isdigit():
                    self.commands.append((int(head), command))
        except (FileNotFoundError, ValueError) as error:
            self.error = str(error)

    def users(self, path: str) -> list[str]:
        if self.error:
            raise Unsafe(f"쓰는 프로세스를 재지 못함({self.error})")
        me = os.getpid()
        if not hasattr(self, "_sorted"):
            # /tmp 항목 수천 개를 볼 때 매번 전체 목록을 훑지 않게 정렬해 두고 앞부분 일치 구간만 본다
            self._sorted = sorted((name, pid) for pid, name in self.files)
        import bisect
        found = set()
        i = bisect.bisect_left(self._sorted, (path, -1))
        while i < len(self._sorted):
            name, pid = self._sorted[i]
            if name != path and not name.startswith(path + "/"):
                if name > path + "/\uffff":
                    break
                i += 1
                continue
            if pid != me:
                found.add(f"pid {pid}")
            i += 1
        found |= {f"pid {pid}" for pid, cmd in self.commands if pid != me and path in cmd}
        return sorted(found)


def size_text(path: str) -> str:
    du = run(["du", "-sk", path])
    try:
        kb = int(du.stdout.split()[0])
    except (IndexError, ValueError):
        return "크기 모름"
    for unit, step in (("GB", 1024 * 1024), ("MB", 1024)):
        if kb >= step:
            return f"{kb / step:.1f}{unit}"
    return f"{kb}KB"


def live_claude_names() -> set[str]:
    """살아 있는 Claude 세션의 작업 폴더를 임시 폴더 이름으로 바꾼 것. 세션 폴더 · 파일을 못 읽으면 모름."""
    names = set()
    root = Path(CLAUDE_SESSIONS)
    if not root.is_dir():
        # 세션 폴더가 없으면 「도는 세션 없음」이 아니라 모름이다. 모르면 지우지 않는다.
        raise Unsafe(f"Claude 세션 폴더가 없어 도는 세션을 모름: {root}")
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data["pid"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            stem = path.stem.split(".", 1)[0]
            if stem.isdigit() and not pid_alive(int(stem)):
                continue
            raise Unsafe(f"Claude 세션 상태를 읽지 못함: {path}")
        if not pid_alive(pid):
            continue
        cwd = data.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise Unsafe(f"살아 있는 Claude 세션(pid {pid})의 작업 폴더를 모름: {path}")
        names.add(claude_tmp_name(cwd))
    return names


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove(path: str, apply: bool, label: str) -> bool:
    size = size_text(path)
    if not apply:
        print(f"지울 것 {label} : {path} ({size})")
        return True
    try:
        shutil.rmtree(path)
    except OSError as error:
        print(f"지우기 실패 {label} : {path} ({error})", file=sys.stderr)
        return False
    print(f"지움 {label} : {path} ({size})")
    return True


def covers(name: str, names, sub: bool = False) -> bool:
    """name 이 names 중 하나와 같거나(sub 면) 그 하위 폴더 이름(<이름>-...)이면 참.
    sub 가 아니면 반대로 names 중 하나가 name 의 하위 폴더 세션인지 본다(그 폴더 아래에서 도는 세션)."""
    for other in names:
        if name == other:
            return True
        if sub and name.startswith(other + "-"):
            return True
        if not sub and other.startswith(name + "-"):
            return True
    return False


def claude_root() -> str:
    root = CLAUDE_TMP_ROOT
    if not re.fullmatch(r"claude-\d+", os.path.basename(root)) or os.path.realpath(root) != root:
        raise Unsafe(f"임시 폴더 뿌리가 이상함: {root!r}")
    return root


class ClaudeTmp:
    """Claude 임시 폴더 하나씩 판정하고 지운다. 세션 · 프로세스 목록은 한 번만 잰다."""

    def __init__(self, apply: bool) -> None:
        self.apply = apply
        self.root = claude_root()
        self.live = live_claude_names()
        self.usage: Usage | None = None
        self.failed = 0

    def name_ok(self, name: str) -> bool:
        return name.startswith("-") and name.strip("-") != "" and len(name) <= MAX_NAME

    def handle(self, name: str) -> None:
        try:
            path = check_target(os.path.join(self.root, name), self.root, self.name_ok)
            if not os.path.isdir(path):
                print(f"없음 Claude 임시 : {path}")
                return
            if covers(name, self.live):
                raise Unsafe(f"그 작업 폴더에서 도는 Claude 세션이 있음: {path}")
            self.usage = self.usage or Usage()
            users = self.usage.users(path)
            if users:
                raise Unsafe(f"쓰는 프로세스가 있음({', '.join(users)}): {path}")
            if not remove(path, self.apply, "Claude 임시"):
                self.failed += 1
        except Unsafe as reason:
            print(f"남김 Claude 임시 : {reason}")
            self.failed += 1


def claude_tmp(worktrees: list[str], apply: bool) -> int:
    """워크트리 경로로 이름을 계산해 그 Claude 임시 폴더를 지운다."""
    try:
        job = ClaudeTmp(apply)
    except Unsafe as reason:
        print(f"남김 Claude 임시 : {reason}")
        return 1
    for worktree in worktrees:
        if not worktree or not os.path.isabs(worktree):
            print(f"남김 Claude 임시 : 워크트리 경로가 절대 경로가 아님: {worktree!r}")
            job.failed += 1
            continue
        worktree = os.path.normpath(worktree)
        name = claude_tmp_name(worktree)
        if len(name) > MAX_NAME:
            print(f"남김 Claude 임시 : 이름이 {MAX_NAME}자를 넘어 Claude 가 해시를 붙였다. 계산으로 못 맞춤: {worktree}")
            job.failed += 1
            continue
        job.handle(name)
    return 1 if job.failed else 0


def claude_orphans(roots: list[str], existing: list[str], apply: bool) -> int:
    """워크트리가 이미 없어진 Claude 임시 폴더를 지운다.
    roots(작업 공간 뿌리) 아래 경로에서 나온 이름이면서, 지금 있는 워크트리 어느 것의 이름과도 같지 않은 것만 본다."""
    try:
        job = ClaudeTmp(apply)
    except Unsafe as reason:
        print(f"남김 Claude 임시 : {reason}")
        return 1
    prefixes = [claude_tmp_name(os.path.normpath(r)) + "-" for r in roots if r and os.path.isabs(r)]
    alive = {claude_tmp_name(os.path.normpath(w)) for w in existing if w}
    alive_names = [a for a in alive if a]
    for name in sorted(os.listdir(job.root)) if os.path.isdir(job.root) else []:
        # 있는 작업 공간의 하위 폴더에서 연 세션(이름이 <작업 공간>-<하위> 꼴)도 지우지 않는다.
        if not any(name.startswith(p) for p in prefixes) or covers(name, alive_names, sub=True):
            continue
        job.handle(name)
    return 1 if job.failed else 0


def claude_tmp_preview(worktree: str) -> None:
    """dry-run 용. 안전 판정 없이 지울 자리와 크기만 보여 준다(작업자가 아직 살아 있을 때 부른다)."""
    name = claude_tmp_name(os.path.normpath(worktree))
    path = os.path.join(CLAUDE_TMP_ROOT, name)
    if os.path.isdir(path) and not os.path.islink(path):
        print(f"[dry-run] 할 일: Claude 임시 폴더 지우기 {path} ({size_text(path)})")
    else:
        print(f"[dry-run] Claude 임시 폴더 없음: {path}")


def chrome_starts() -> list[float]:
    """지금 도는 크롬 본체들이 뜬 시각(epoch 초). 못 재면 Unsafe."""
    ps = run(["ps", "-axww", "-o", "lstart=,command="])
    if ps.returncode != 0:
        raise Unsafe(f"크롬 프로세스를 재지 못함(ps 종료 {ps.returncode})")
    starts = []
    for line in ps.stdout.splitlines():
        line = line.strip()
        if not CHROME_MAIN.search(line) or "--type=" in line:
            continue
        try:  # lstart 는 「Thu Oct  8 10:00:00 2026」 꼴로 다섯 낱말이다
            started = time.mktime(time.strptime(" ".join(line.split()[:5]), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            raise Unsafe(f"크롬 시작 시각을 읽지 못함: {line[:120]}")
        starts.append(started)
    return starts


# 크롬은 뜰 때 복제본을 만든다. 도는 크롬이 뜬 시각 근처(앞 1분 ~ 뒤 10분)에 생긴 복제본은 그 크롬 것일 수 있다.
CLONE_NEAR_BEFORE, CLONE_NEAR_AFTER = 60, 600


def chrome_clones(apply: bool) -> int:
    clones = sorted(glob.glob(CHROME_CLONE_GLOB))
    if not clones:
        print("없음 크롬 복제본")
        return 0
    failed = 0
    try:
        starts = chrome_starts()
        usage = Usage()
    except Unsafe as reason:
        print(f"남김 크롬 복제본 전부 : {reason}")
        return 1
    for path in clones:
        try:
            parent = os.path.dirname(path)
            if not os.path.basename(parent).endswith("code_sign_clone"):
                raise Unsafe(f"부모 이름이 code_sign_clone 이 아님: {path}")
            check_target(path, parent, lambda n: n.startswith("code_sign_clone.") and len(n) > len("code_sign_clone."))
            if not os.path.isdir(path):
                continue
            stat = os.stat(path)
            born = getattr(stat, "st_birthtime", stat.st_mtime)
            # 「가장 먼저 뜬 크롬보다 뒤에 생긴 것은 전부 남김」으로 하면 크롬을 며칠 켜 둘 때
            # 헤드리스 크롬이 남긴 복제본 수백 개를 하나도 못 지운다.
            if any(s - CLONE_NEAR_BEFORE <= born <= s + CLONE_NEAR_AFTER for s in starts):
                raise Unsafe(f"도는 크롬이 뜬 무렵에 생김(그 크롬 것일 수 있음): {path}")
            if time.time() - born < DAY:
                raise Unsafe(f"하루가 안 됨(막 뜬 크롬 것일 수 있음): {path}")
            users = usage.users(path)
            if users:
                raise Unsafe(f"쓰는 프로세스가 있음({', '.join(users)}): {path}")
            if not remove(path, apply, "크롬 복제본"):
                failed += 1
        except Unsafe as reason:
            print(f"남김 크롬 복제본 : {reason}")
    return 1 if failed else 0


# ---- 검수 찌꺼기 · 오래된 보고서 미디어 ----
# 미리보기(dry-run)가 지울 목록을 계획 파일에 적고, 실제 실행은 그 목록 안에서 다시 확인한 것만 지운다.

TMP_ROOT = os.environ.get("HARNESS_TMP_ROOT", _TMP)
REPORTS_ROOT = os.environ.get("HARNESS_REPORTS_ROOT", "")  # 보고서 미디어 정리는 폴더를 줄 때만(--media-dir)
PLAN_PATH = os.environ.get("HARNESS_CLEANUP_PLAN", str(Path.home() / ".cache" / "harness" / "cleanup-junk-plan.json"))
DAY = 24 * 3600
TMP_IDLE = DAY  # 이 시간 넘게 안 쓰인 /tmp 항목만
REPORT_AGE = 7 * DAY  # 이보다 오래된 보고서 미디어만. 오늘 · 어제 것은 늘 남는다
PLAN_MAX_AGE = DAY  # 이보다 오래된 계획은 믿지 않는다. 미리보기를 다시 돌린다
REPORT_MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".json"}
# 내 것이어도 /tmp 에서 손대지 않는 이름(도구 소켓 · 세션 · Claude 임시 뿌리)
TMP_KEEP = re.compile(r"^(\.|claude-|com\.apple\.|launchd|tmux-|ssh-|orca|powerlog|boost_interprocess)|\.(sock|socket|pid|lock)$")
WALK_LIMIT = 200_000
# 크기만 보고하는 자리(대화 기록). 지우지 않는다.
SIZE_ONLY = [
    ("Codex 대화 기록", str(Path.home() / ".codex" / "sessions")),
]


def human(n: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f}{unit}"
    return f"{n}B"


def scan(path: str) -> tuple[int, float]:
    """(디스크 크기 바이트, 안의 가장 최근 수정 시각). 링크는 따라가지 않는다.
    소켓 · 파이프가 있거나 너무 크면 Unsafe(쓰는 중일 수 있거나 판정 못 함)."""
    st = os.lstat(path)
    size, newest, seen = st.st_blocks * 512, st.st_mtime, 0
    if not os.path.isdir(path) or os.path.islink(path):
        return size, newest
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            seen += 1
            if seen > WALK_LIMIT:
                raise Unsafe(f"항목이 {WALK_LIMIT}개를 넘어 판정 못 함: {path}")
            try:
                s = os.lstat(os.path.join(top, name))
            except OSError:
                continue
            if stat_kind(s.st_mode) in ("socket", "fifo"):
                raise Unsafe(f"소켓 · 파이프가 들어 있음(쓰는 중일 수 있음): {path}")
            size += s.st_blocks * 512
            newest = max(newest, s.st_mtime)
    return size, newest


def stat_kind(mode: int) -> str:
    import stat as st
    if st.S_ISSOCK(mode):
        return "socket"
    if st.S_ISFIFO(mode):
        return "fifo"
    if st.S_ISLNK(mode):
        return "link"
    if st.S_ISDIR(mode):
        return "dir"
    return "file" if st.S_ISREG(mode) else "other"


def tmp_candidate(name: str, names: list[str], now: float, usage: "Usage") -> tuple[str, int, str]:
    """/tmp 바로 아래 항목 하나를 판정한다. 지울 것이면 (경로, 크기, 이유). 아니면 Unsafe."""
    root = os.path.realpath(TMP_ROOT)
    path = os.path.join(root, name)
    if TMP_KEEP.search(name):
        raise Unsafe(f"도구 · 세션 자리라 손대지 않음: {path}")
    check_target(path, root, lambda n: n not in ("", ".", ".."))
    st = os.lstat(path)
    if st.st_uid != getattr(os, "getuid", lambda: -1)():
        raise Unsafe(f"내 것이 아님: {path}")
    if stat_kind(st.st_mode) not in ("dir", "file"):
        raise Unsafe(f"일반 파일 · 폴더가 아님: {path}")
    if os.path.isdir(os.path.join(path, ".git")):
        raise Unsafe(f"git 저장소라 커밋 안 된 작업이 있을 수 있음: {path}")
    size, newest = scan(path)
    linked = next((n for n in names if n and len(n) >= 6 and n in name), "")
    idle = now - newest
    if not linked and idle < TMP_IDLE:
        raise Unsafe(f"최근 {TMP_IDLE // 3600}시간 안에 쓰임: {path}")
    users = usage.users(path)
    if users:
        raise Unsafe(f"쓰는 프로세스가 있음({', '.join(users)}): {path}")
    why = f"끝난 작업 '{linked}'" if linked else f"{int(idle // 3600)}시간 안 쓰임"
    return path, size, why


def report_candidates(now: float, usage: "Usage") -> list[tuple[str, int, str]]:
    """보고서 폴더의 7일 지난 미디어 · json. md 와 그 밖의 파일은 남긴다(근거 원본)."""
    if not REPORTS_ROOT:
        return []
    root = os.path.realpath(REPORTS_ROOT)
    if not os.path.isdir(root) or os.path.islink(REPORTS_ROOT):
        return []
    found = []
    for top, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(top, name)
            if os.path.splitext(name)[1].lower() not in REPORT_MEDIA:
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if stat_kind(st.st_mode) != "file" or now - st.st_mtime < REPORT_AGE:
                continue
            found.append((path, st.st_blocks * 512, f"{int((now - st.st_mtime) // DAY)}일 지남"))
    if not found:
        return []
    paths = {p for p, _s, _w in found}
    busy = set()
    for path in paths:
        if usage.users(path):
            busy.add(path)
    return [c for c in found if c[0] not in busy]


def junk_candidates(names: list[str], now: float | None = None) -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """지울 후보 [(종류, 경로, 크기, 이유)] 와 남긴 이유 목록."""
    now = time.time() if now is None else now
    usage = Usage()
    if usage.error:
        raise Unsafe(f"쓰는 프로세스를 재지 못함({usage.error})")
    out, kept = [], []
    root = os.path.realpath(TMP_ROOT)
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        try:
            path, size, why = tmp_candidate(name, names, now, usage)
            out.append(("tmp 찌꺼기", path, size, why))
        except (Unsafe, OSError) as reason:
            # 내 것 아님 · 최근 쓰임 · 도구 자리는 흔하다. 지울 뻔했는데 막은 이유만 보인다
            if any(k in str(reason) for k in ("쓰는 프로세스", "git 저장소", "소켓", "판정 못 함")):
                kept.append(f"tmp : {reason}")
    for path, size, why in report_candidates(now, usage):
        out.append(("보고서 미디어", path, size, why))
    return out, kept


def print_junk_table(cands: list[tuple[str, int, str, str]]) -> None:
    groups: dict[str, list[int]] = {}
    for kind, path, size, _why in cands:
        key = kind
        if kind == "보고서 미디어":  # 파일이 수천 개라 보고서 폴더별로 묶어 보인다
            rel = os.path.relpath(path, os.path.realpath(REPORTS_ROOT)).split(os.sep)[0]
            key = f"{kind} · {rel}"
        else:
            print(f"  {kind} : {path} ({human(size)}, {_why})")
        g = groups.setdefault(key, [0, 0])
        g[0] += 1
        g[1] += size
    for key, (n, size) in sorted(groups.items()):
        if key.startswith("보고서 미디어 · "):
            print(f"  {key} : {n}개 ({human(size)})")
    total = sum(c[2] for c in cands)
    by_kind: dict[str, list[int]] = {}
    for kind, _p, size, _w in cands:
        k = by_kind.setdefault(kind, [0, 0])
        k[0] += 1
        k[1] += size
    for kind, (n, size) in sorted(by_kind.items()):
        print(f"합계 {kind} : {n}개 · {human(size)}")
    print(f"합계 전체 : {len(cands)}개 · {human(total)}")


TRASH_DIR = os.environ.get("HARNESS_TRASH_DIR", str(Path.home() / ".Trash"))
# 휴지통에 쌓인 작업 찌꺼기로 보이는 이름(검수 사본 · 크롬 프로필 · 미리보기 · 실행 캐시 · 브리프/보고 임시 파일)
TRASH_JUNK = re.compile(r"(verify|gate|preview|chrome|profile|__pycache__|brief|report|scratch|worktree|code_sign_clone|playwright)", re.I)


def trash_report() -> None:
    """휴지통 크기와 작업 찌꺼기로 보이는 것. 지우지 않는다(사용자 파일이 같이 있다)."""
    root = TRASH_DIR
    if not os.path.isdir(root):
        return
    try:
        names = sorted(os.listdir(root))
    except OSError as error:
        print(f"크기만 휴지통 : {root} 를 읽지 못함({error.strerror or error})")
        return
    junk = [n for n in names if TRASH_JUNK.search(n)]
    size = sum(scan_size(os.path.join(root, n)) for n in junk)
    print(f"크기만 휴지통 : {root} ({size_text(root)}, {len(names)}개) · 지우지 않음. 비우기는 사용자가")
    if junk:
        shown = ", ".join(junk[:8]) + (f" 외 {len(junk) - 8}개" if len(junk) > 8 else "")
        print(f"  그중 작업 찌꺼기로 보이는 것 {len(junk)}개 · {human(size)} : {shown}")


def scan_size(path: str) -> int:
    try:
        return scan(path)[0]
    except (OSError, Unsafe):
        return 0


def size_only_report() -> None:
    if os.environ.get("HARNESS_TRASH_DIR") or not os.environ.get("HARNESS_SIZE_ONLY_OFF"):
        trash_report()
    if os.environ.get("HARNESS_SIZE_ONLY_OFF"):  # 시험에서 진짜 홈 폴더를 재지 않게
        return
    for label, path in SIZE_ONLY:
        if os.path.isdir(path):
            print(f"크기만 {label} : {path} ({size_text(path)}) · 지우지 않음")
    root = CLAUDE_TMP_ROOT
    if os.path.isdir(root):
        print(f"크기만 Claude 임시 뿌리 : {root} ({size_text(root)}) · 도는 세션 것은 남김")


def junk(apply: bool, names: list[str]) -> int:
    """미리보기는 후보와 합계를 보이고 계획 파일에 적는다. --apply 는 그 계획 안에서 다시 확인한 것만 지운다."""
    plan = None
    if apply:
        try:
            with open(PLAN_PATH, encoding="utf-8") as f:
                plan = json.load(f)
            if time.time() - float(plan["created"]) > PLAN_MAX_AGE:
                raise ValueError("계획이 24시간 넘게 지남")
            planned = set(plan["paths"])
            names = sorted(set(names) | set(plan.get("names") or []))  # 미리보기 때의 끝난 작업 이름으로 같은 판정
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"남김 찌꺼기 전부 : 미리보기 계획이 없거나 낡음({error}). 먼저 미리보기를 돌린다")
            return 1
    try:
        cands, kept = junk_candidates(names)
    except Unsafe as reason:
        print(f"남김 찌꺼기 전부 : {reason}")
        return 1
    if not apply:
        for line in kept:
            print(f"남김 {line}")
        print_junk_table(cands)
        size_only_report()
        os.makedirs(os.path.dirname(PLAN_PATH), exist_ok=True)
        tmp = PLAN_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"created": time.time(), "names": names, "paths": [c[1] for c in cands]}, f)
        os.replace(tmp, PLAN_PATH)
        print(f"계획 저장 : {PLAN_PATH} ({len(cands)}개). --apply 는 이 목록 안의 것만 지운다")
        return 0
    now_cands = [c for c in cands if c[1] in planned]
    failed = 0
    removed = 0
    for kind, path, size, _why in now_cands:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += size
        except OSError as error:
            print(f"지우기 실패 {kind} : {path} ({error})", file=sys.stderr)
            failed += 1
    skipped = len(planned) - len(now_cands)
    print(f"지움 찌꺼기 : {len(now_cands) - failed}개 · {human(removed)}"
          + (f" · 계획에 있었지만 다시 확인에서 빠져 남김 {skipped}개" if skipped else ""))
    try:
        os.remove(PLAN_PATH)
    except OSError:
        pass
    return 1 if failed else 0


def sweep(apply: bool, names: list[str], roots: list[str], existing: list[str] | None) -> int:
    """작업 정리 끝에 부른다: 작업 공간이 없어진 Claude 임시 폴더 + /tmp 검수 찌꺼기.
    existing 은 지금 있는 워크트리 전부. 못 읽었으면 None 이고 고아 판정을 하지 않는다."""
    if os.name == "nt":
        return 0
    failed = 0
    if existing is None:
        print("건너뜀 고아 Claude 임시 : 저장소 워크트리 목록을 못 읽어 「없어진 작업 공간」을 판정할 수 없음")
    else:
        failed |= claude_orphans(roots, existing, apply)
    failed |= junk(apply, names)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    global REPORTS_ROOT
    if os.name == "nt":
        print("건너뜀 임시 찌꺼기 : Windows 는 지원하지 않는다")
        return 0
    apply = "--apply" in argv
    args = [a for a in argv if a != "--apply"]
    if "--media-dir" in args:
        k = args.index("--media-dir")
        if k + 1 >= len(args):
            print("--media-dir 뒤에 폴더가 없다", file=sys.stderr)
            return 2
        REPORTS_ROOT = os.path.abspath(os.path.expanduser(args[k + 1]))
        del args[k:k + 2]
    if args[:1] == ["junk"]:
        return junk(apply, [n for a in args[1:] for n in a.split(",") if n])
    if args[:1] == ["claude-tmp"] and len(args) >= 2:
        return claude_tmp(args[1:], apply)
    if args[:1] == ["claude-orphans"] and len(args) >= 2:
        existing = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
        return claude_orphans(args[1:], existing, apply)
    if args == ["chrome-clones"]:
        return chrome_clones(apply)
    print(__doc__.split("사용:", 1)[1].split("종료 코드", 1)[0].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
