#!/usr/bin/env python3
"""끝난 작업 공간(git worktree)과 그 브랜치를 치운다. harness.json 의 하네스 저장소와 저장소 목록을 훑는다.

치우는 조건(모두 맞아야 한다):
  1. 원래 체크아웃이 아니고 잠겨 있지 않다
  2. 최근 RECENT_MIN 분(기본 120) 안에 손댄 흔적이 없다(막 만든 작업 공간을 지우지 않으려는 장치)
  3. 커밋 안 된 변경이 없다
  4. 그 작업 공간의 커밋이 원격(origin)의 어느 브랜치에든 전부 들어가 있다
하나라도 어긋나면 지우지 않고 이유를 적는다. Orca 작업 폴더 아래 것은 Orca 에서 지우라고만 안내한다.

사용: python3 harness/scripts/cleanup_worktrees.py            (미리보기)
      python3 harness/scripts/cleanup_worktrees.py --apply    (실제로 지움)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harnesslib as hl  # noqa: E402


def git(repo, *args, check=False):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.returncode, r.stdout.strip()


def worktrees(repo):
    _, out = git(repo, "worktree", "list", "--porcelain")
    item: dict = {}
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            item = {"path": line[9:], "branch": "", "locked": False}
        elif line.startswith("branch "):
            item["branch"] = line[len("branch refs/heads/"):]
        elif line.startswith("locked"):
            item["locked"] = True
        elif line == "" and item:
            yield item
            item = {}


def recently_touched(path, minutes):
    limit = time.time() - minutes * 60
    _, gd = git(path, "rev-parse", "--absolute-git-dir")
    for p in (Path(path), Path(gd) / "HEAD", Path(gd) / "index"):
        try:
            if p.stat().st_mtime > limit:
                return True
        except OSError:
            pass
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="끝난 작업 공간 정리")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="하네스 저장소 루트")
    args = ap.parse_args(argv)
    cfg = hl.load_config(args.root / hl.CONFIG_NAME)
    parent = hl.git_common_parent(args.root) or args.root.parent
    recent = int(os.environ.get("RECENT_MIN", "120"))
    orca_ws = os.path.realpath(os.path.expanduser(cfg["orca"]["workspaces_dir"])) + os.sep
    removed = kept = scanned = 0
    for d, repo in [(cfg["harness_repo"]["dir"], args.root)] + [(r["dir"], hl.repo_path(r, parent)) for r in cfg["repos"]]:
        if not (repo / ".git").exists():
            print(f"건너뜀 [{d}] 저장소가 없다: {repo}")
            continue
        scanned += 1
        git(repo, "fetch", "--quiet", "--prune", "origin")
        _, main_path = git(repo, "rev-parse", "--show-toplevel")
        for wt in worktrees(repo):
            path, branch = wt["path"], wt["branch"]
            if os.path.realpath(path) == os.path.realpath(main_path):
                continue
            label = f"[{d}] {path} ({branch or 'detached'})"
            if wt["locked"]:
                print(f"남김 {label} : 잠김"); kept += 1; continue
            if not os.path.isdir(path):
                print(f"정리 {label} : 폴더가 이미 없음 → worktree prune")
                if args.apply:
                    git(repo, "worktree", "prune")
                removed += 1; continue
            if recently_touched(path, recent):
                print(f"남김 {label} : 최근 {recent}분 안에 손댐"); kept += 1; continue
            _, dirty = git(path, "status", "--porcelain")
            if dirty:
                print(f"남김 {label} : 커밋 안 된 변경 있음"); kept += 1; continue
            _, head = git(path, "rev-parse", "HEAD")
            _, unpushed = git(repo, "rev-list", head, "--not", "--remotes=origin")
            n = len(unpushed.splitlines())
            if n:
                print(f"남김 {label} : 원격에 없는 커밋 {n} 개"); kept += 1; continue
            if (os.path.realpath(path) + os.sep).startswith(orca_ws):
                print(f"Orca {label} : 원격에 다 있음 → Orca 에서 지운다"); kept += 1; continue
            print(f"지움 {label} : 원격에 다 있음")
            if args.apply:
                code, _ = git(repo, "worktree", "remove", path)
                if code == 0 and branch:
                    git(repo, "branch", "-D", branch)
            removed += 1
    mode = "적용" if args.apply else "미리보기 (지우려면 --apply)"
    print(f"완료 [{mode}]: 저장소 {scanned} · 정리 {removed} · 남김 {kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
