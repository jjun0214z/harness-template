#!/usr/bin/env python3
"""작업 흐름(workflow 묶음): 작업 하나 = 번호 하나 = 문서 한 장 + 저장소별 워크트리.

사용:
  work.py                         열린 작업과 상태 한 화면 (--json · --brief)
  work.py new <제목> [저장소 키…]   번호를 받고 문서 · 저장소별 워크트리를 만든다
  work.py add <번호> <저장소 키…>   열린 작업에 저장소를 더한다
  work.py done <번호> [--force]     워크트리를 치우고 문서를 완료 자리로 옮긴다 (--dry-run 은 미리 보기)

사람이 치는 명령은 여는 것(new)과 닫는 것(done) 두 개다. 단계를 체크하지 않는다.
상태는 파일에 적지 않고 git 에서 매번 계산한다.

  열림    워크트리 · 브랜치는 있는데 아직 커밋이 없다
  작업중  어느 저장소 브랜치에 기준 브랜치에 없는 커밋이 있다
  병합됨  커밋한 저장소의 브랜치가 모두 기준 브랜치에 들어갔다
  닫힘    done 으로 문서가 `<dir>/완료/` 로 갔다

병합 판정은 「브랜치 끝 커밋이 기준 브랜치에 들어 있는가」다. squash · rebase 병합은 끝 커밋이 바뀌어
잡지 못한다. 그때는 확인한 뒤 `done --force` 로 닫는다.

번호는 `max(로컬 ∪ 원격 기준 브랜치) + 1` 이다. 하네스에 원격이 있으면 문서를 커밋하고 **바로 push 해서**
번호를 잡는다(push 가 잠금이다. 남이 먼저 올렸으면 거절되므로 번호를 다시 받는다). 원격이 없으면 로컬 커밋만 한다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402

DONE_DIR = "완료"


class Stop(Exception):
    pass


def git(cwd, *args, timeout: int = 60) -> Tuple[int, str]:
    try:
        # 한글 경로 · 브랜치: 따옴표로 감싸지 않게(core.quotepath) · 바이트는 utf-8 로 읽는다
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(cwd), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()


def ok(cwd, *args) -> bool:
    return git(cwd, *args)[0] == 0


# ---------------------------------------------------------------- 자리

class Place:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cfg = hl.load_config(self.root / hl.CONFIG_NAME)
        if not hl.has_pack(self.cfg, "workflow"):
            raise Stop("workflow 묶음이 꺼져 있다. harness.json packs 에 \"workflow\" 를 넣고 생성기를 돌린다")
        self.wf = self.cfg["workflow"]
        self.py = self.cfg["platform"]["python"]
        self.docs = self.root / self.wf["dir"]
        self.parent = hl.git_common_parent(self.root) or self.root.parent
        self.work = self.parent / ".work"
        self.base = self.cfg["harness_repo"]["base_branch"]
        self.repos = {r["key"]: r for r in self.cfg["repos"]}

    def repo_path(self, key: str) -> Path:
        return hl.repo_path(self.repos[key], self.parent)

    def id_re(self):
        return re.compile(rf"^({re.escape(self.wf['prefix'])}-(\d{{4}}))(?:-|\.md$)")

    def branch(self, tid: str, slug: str) -> str:
        return self.wf["branch"].replace("{id}", tid).replace("{slug}", slug)

    def space(self, tid: str, slug: str) -> Path:
        return self.work / f"{tid}-{slug}"


def slugify(title: str) -> str:
    s = re.sub(r"\s+", "-", title.strip())
    s = re.sub(r"[\\/:*?\"<>|~^\[\]{}`'$&;!#]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return s[:40] or "작업"


# ---------------------------------------------------------------- 문서

class Doc:
    def __init__(self, path: Path, place: Place):
        self.path = path
        text = path.read_text(encoding="utf-8")
        m = place.id_re().match(path.name)
        self.tid = m.group(1) if m else path.stem
        self.slug = path.stem[len(self.tid) + 1:] if path.stem.startswith(self.tid + "-") else ""
        head = re.search(r"^# (.+)$", text, re.M)
        self.title = head.group(1).split(" ", 1)[-1].strip() if head else self.slug
        self.repos = field_list(text, "대상 저장소")
        self.branch = (field(text, "브랜치") or place.branch(self.tid, self.slug)).strip("`")
        self.starts = dict(s.split("@", 1) for s in field_list(text, "시작점") if "@" in s)
        self.done = path.parent.name == DONE_DIR


def field(text: str, name: str) -> str:
    m = re.search(rf"^- {re.escape(name)}: *(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def field_list(text: str, name: str) -> List[str]:
    v = field(text, name)
    return [] if v in ("", "없음", "-") else [x.strip().strip("`") for x in re.split(r"[,·]", v) if x.strip()]


def set_field(text: str, name: str, value: str) -> str:
    return re.sub(rf"^- {re.escape(name)}: *.*$", f"- {name}: {value}", text, count=1, flags=re.M)


def doc_text(tid: str, title: str, repos: List[str], branch: str, starts: Dict[str, str]) -> str:
    return f"""# {tid} {title}

- 번호: {tid}
- 대상 저장소: {', '.join(repos) or '없음'}
- 브랜치: `{branch}`
- 시작점: {' · '.join(f'{k}@{v}' for k, v in starts.items()) or '-'}
- 만든 날: {_dt.date.today().isoformat()}

## 목표

## 완료 조건

## 메모
"""


def all_docs(place: Place, include_done: bool = False) -> List[Doc]:
    out = []
    dirs = [place.docs] + ([place.docs / DONE_DIR] if include_done else [])
    for d in dirs:
        if d.is_dir():
            out += [Doc(p, place) for p in sorted(d.glob("*.md")) if place.id_re().match(p.name)]
    return out


def find_doc(place: Place, tid: str) -> Doc:
    tid = tid.upper() if re.match(r"^[a-z]+-\d+$", tid) else tid
    if re.fullmatch(r"\d{1,4}", tid):
        tid = f"{place.wf['prefix']}-{int(tid):04d}"
    for d in all_docs(place, include_done=True):
        if d.tid == tid:
            return d
    raise Stop(f"{tid} 문서를 찾지 못했다({place.docs.relative_to(place.root)})")


# ---------------------------------------------------------------- 번호

def has_remote(path: Path) -> bool:
    return bool(git(path, "remote")[1].strip())


def numbers(place: Place) -> List[int]:
    rx = place.id_re()
    nums = [int(rx.match(d.path.name).group(2)) for d in all_docs(place, include_done=True)]
    rel = place.docs.relative_to(place.root).as_posix()
    code, out = git(place.root, "ls-tree", "-r", "--name-only", f"origin/{place.base}", "--", rel)
    if code == 0:
        for line in out.splitlines():
            m = rx.match(os.path.basename(line))
            if m:
                nums.append(int(m.group(2)))
    return nums


def next_id(place: Place) -> str:
    return f"{place.wf['prefix']}-{max(numbers(place), default=0) + 1:04d}"


# ---------------------------------------------------------------- 워크트리

def base_ref(repo: Path, base: str) -> str:
    return f"origin/{base}" if ok(repo, "rev-parse", "--verify", "--quiet", f"origin/{base}") else base


def make_worktree(place: Place, key: str, tid: str, slug: str, branch: str) -> Tuple[Path, str]:
    if key not in place.repos:
        raise Stop(f"저장소 키 `{key}` 가 harness.json 에 없다 (있는 키: {', '.join(place.repos) or '없음'})")
    repo = place.repo_path(key)
    if not (repo / ".git").exists():
        raise Stop(f"저장소가 없다: {repo}")
    if has_remote(repo):
        git(repo, "fetch", "--quiet", "origin")
    ref = base_ref(repo, place.repos[key]["base_branch"])
    _, start = git(repo, "rev-parse", "--short", ref)
    path = place.space(tid, slug) / place.repos[key]["dir"]
    if path.exists():
        return path, start
    path.parent.mkdir(parents=True, exist_ok=True)
    if ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        code, out = git(repo, "worktree", "add", str(path), branch)
    else:
        code, out = git(repo, "worktree", "add", "-b", branch, str(path), ref)
    if code != 0:
        raise Stop(f"{key} 워크트리를 만들지 못했다: {out[-300:]}")
    return path, start


# ---------------------------------------------------------------- 상태

def repo_state(place: Place, doc: Doc, key: str) -> dict:
    if key not in place.repos:
        return {"repo": key, "state": "설정에 없음"}
    repo = place.repo_path(key)
    if not (repo / ".git").exists():
        return {"repo": key, "state": "저장소 없음"}
    ref = base_ref(repo, place.repos[key]["base_branch"])
    tip = None
    for cand in (f"refs/heads/{doc.branch}", f"refs/remotes/origin/{doc.branch}"):
        code, out = git(repo, "rev-parse", "--verify", "--quiet", cand)
        if code == 0:
            tip = out
            break
    if not tip:
        return {"repo": key, "state": "브랜치 없음"}
    _, n = git(repo, "rev-list", "--count", f"{ref}..{tip}")
    ahead = int(n or 0)
    unpushed = 0
    if has_remote(repo):
        theirs = f"refs/remotes/origin/{doc.branch}"
        rng = f"{theirs}..{tip}" if ok(repo, "rev-parse", "--verify", "--quiet", theirs) else f"{ref}..{tip}"
        _, n = git(repo, "rev-list", "--count", rng)
        unpushed = int(n or 0)
    if ahead:
        state = "작업중"
    else:
        # 끝 커밋이 기준에 들어 있다: 시작점 그대로면 아직 손대지 않은 것, 아니면 병합된 것
        start = doc.starts.get(key)
        start_full = git(repo, "rev-parse", "--verify", "--quiet", f"{start}^{{commit}}")[1] if start else ""
        state = "열림" if (tip == start_full or (not start and tip == git(repo, "rev-parse", ref)[1])) else "병합됨"
    return {"repo": key, "state": state, "ahead": ahead, "unpushed": unpushed}


def task_state(place: Place, doc: Doc) -> dict:
    if doc.done:
        return {"id": doc.tid, "title": doc.title, "state": "닫힘", "turn": "", "repos": []}
    repos = [repo_state(place, doc, k) for k in doc.repos]
    states = {r["state"] for r in repos}
    if "작업중" in states:
        state = "작업중"
    elif "병합됨" in states and states <= {"병합됨", "열림"}:
        state = "병합됨"
    else:
        state = "열림"
    if state == "병합됨":
        turn = f"닫을 차례: {place.py} harness/scripts/work.py done {doc.tid}"
    elif any(r.get("unpushed") for r in repos):
        turn = "올릴 차례: " + " · ".join(f"{r['repo']} {r['unpushed']}커밋" for r in repos if r.get("unpushed"))
    elif state == "작업중":
        turn = "작업 중"
    else:
        turn = "시작 전" if repos else "대상 저장소 없음"
    odd = [f"{r['repo']} {r['state']}" for r in repos if r["state"] in ("설정에 없음", "저장소 없음", "브랜치 없음")]
    return {"id": doc.tid, "title": doc.title, "state": state, "turn": turn, "repos": repos, "odd": odd}


def cmd_status(place: Place, as_json: bool, brief: bool) -> int:
    rows = [task_state(place, d) for d in all_docs(place)]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if brief:
        if rows:
            print(f"[작업] 열린 작업 {len(rows)}개: " + " · ".join(f"{r['id']} {r['title']}({r['state']} · {r['turn']})" for r in rows))
        return 0
    if not rows:
        print(f"열린 작업이 없다. 새로 열려면: {place.py} harness/scripts/work.py new <제목> [저장소 키…]")
        return 0
    print("| 번호 | 제목 | 상태 | 누구 차례 | 저장소 |")
    print("| --- | --- | --- | --- | --- |")
    for r in rows:
        repos = " · ".join(f"{x['repo']} {x['state']}" for x in r["repos"]) or "-"
        print(f"| {r['id']} | {r['title']} | {r['state']} | {r['turn']} | {repos} |")
    return 0


# ---------------------------------------------------------------- new · add

def commit_doc(place: Place, path: Path, message: str) -> None:
    rel = path.relative_to(place.root).as_posix()
    if not ok(place.root, "add", "--", rel):
        raise Stop(f"git add 실패: {rel}")
    code, out = git(place.root, "commit", "-q", "-m", message, "--", rel)
    if code != 0:
        raise Stop(f"커밋 실패: {out[-300:]}")


def cmd_new(place: Place, title: str, keys: List[str]) -> int:
    for k in keys:
        if k not in place.repos:
            raise Stop(f"저장소 키 `{k}` 가 harness.json 에 없다 (있는 키: {', '.join(place.repos) or '없음'})")
    slug = slugify(title)
    remote = has_remote(place.root)
    on_base = git(place.root, "symbolic-ref", "-q", "HEAD")[1] == f"refs/heads/{place.base}"  # abbrev-ref 는 한글을 잘라 보인다
    if remote:
        git(place.root, "fetch", "--quiet", "origin")
        if on_base and not ok(place.root, "merge", "--ff-only", "--quiet", f"origin/{place.base}"):
            raise Stop(f"하네스가 origin/{place.base} 와 갈라져 있다. 먼저 맞춘 뒤 다시 연다(git status · git log)")
    place.docs.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        tid = next_id(place)
        branch = place.branch(tid, slug)
        path = place.docs / f"{tid}-{slug}.md"
        path.write_text(doc_text(tid, title, keys, branch, {}), encoding="utf-8")
        commit_doc(place, path, f"{tid} 착수: {title}")
        if not (remote and on_base):
            break
        if ok(place.root, "push", "--quiet", "origin", f"HEAD:{place.base}"):
            break
        # 남이 먼저 올렸다: 내 커밋(문서 하나)만 되돌리고 받은 뒤 번호를 다시 받는다
        git(place.root, "reset", "--quiet", "--soft", "HEAD~1")
        git(place.root, "restore", "--quiet", "--staged", "--", path.relative_to(place.root).as_posix())
        path.unlink()
        git(place.root, "fetch", "--quiet", "origin")
        if not ok(place.root, "merge", "--ff-only", "--quiet", f"origin/{place.base}"):
            raise Stop("번호를 잡으려고 push 했는데 거절됐고, 원격을 받지도 못했다. 네트워크 · git status 를 확인한다")
    else:
        raise Stop("번호를 세 번 잡지 못했다(동시에 여는 사람이 많다). 잠시 뒤 다시 연다")
    starts = {}
    made = []
    for k in keys:
        wt, start = make_worktree(place, k, tid, slug, branch)
        starts[k] = start
        made.append(wt)
    if starts:
        text = set_field(path.read_text(encoding="utf-8"), "시작점", " · ".join(f"{k}@{v}" for k, v in starts.items()))
        path.write_text(text, encoding="utf-8")
        commit_doc(place, path, f"{tid} 시작점 기록")
    rel = path.relative_to(place.root).as_posix()
    print(f"열었다: {tid} {title}")
    print(f"  문서   {rel}  (목표 · 완료 조건을 채운다)")
    for wt in made:
        print(f"  워크트리 {wt}  (브랜치 {branch})")
    if remote and on_base:
        print(f"  번호를 원격에 올려 잡았다(origin/{place.base}).")
    elif remote:
        print(f"  하네스가 {place.base} 가 아니라 번호를 원격에 올리지 않았다. 겹치지 않는지 {place.base} 에 올릴 때 본다.")
    print(f"다음: 작업자에게 저장소마다 브리프를 넘긴다(task-brief). 끝나면 {place.py} harness/scripts/work.py done {tid}")
    return 0


def cmd_add(place: Place, tid: str, keys: List[str]) -> int:
    doc = find_doc(place, tid)
    if doc.done:
        raise Stop(f"{doc.tid} 는 이미 닫혔다")
    new = [k for k in keys if k not in doc.repos]
    starts = dict(doc.starts)
    for k in new:
        wt, start = make_worktree(place, k, doc.tid, doc.slug, doc.branch)
        starts[k] = start
        print(f"  워크트리 {wt}  (브랜치 {doc.branch})")
    if not new:
        print("더할 저장소가 없다(이미 대상이다)")
        return 0
    text = doc.path.read_text(encoding="utf-8")
    text = set_field(text, "대상 저장소", ", ".join(doc.repos + new))
    text = set_field(text, "시작점", " · ".join(f"{k}@{v}" for k, v in starts.items()))
    doc.path.write_text(text, encoding="utf-8")
    commit_doc(place, doc.path, f"{doc.tid} 대상 저장소 추가: {', '.join(new)}")
    print(f"{doc.tid} 에 더했다: {', '.join(new)}")
    return 0


# ---------------------------------------------------------------- done

def worktree_problem(wt: Path, ref: str, branch: str) -> Optional[str]:
    code, dirty = git(wt, "status", "--porcelain")
    if code != 0:
        return "git 상태를 읽지 못했다"
    if dirty:
        return "커밋 안 된 변경이 있다"
    theirs = f"refs/remotes/origin/{branch}"
    keep = [ref] + ([theirs] if ok(wt, "rev-parse", "--verify", "--quiet", theirs) else [])
    _, n = git(wt, "rev-list", "--count", "HEAD", *[f"^{k}" for k in keep])
    if int(n or 0):
        return f"기준 브랜치에도 원격에도 없는 커밋이 {n}개 있다(지우면 잃는다)"
    return None


def cmd_done(place: Place, tid: str, force: bool, dry: bool) -> int:
    doc = find_doc(place, tid)
    if doc.done:
        print(f"{doc.tid} 는 이미 닫혔다")
        return 0
    st = task_state(place, doc)
    if st["state"] != "병합됨" and not force:
        raise Stop(f"{doc.tid} 는 {st['state']} 이다({st['turn']}). 병합 뒤 닫는다. "
                   "squash 병합 · 취소처럼 확인했으면 --force")
    space = place.space(doc.tid, doc.slug)
    plan = []
    for k in doc.repos:
        if k not in place.repos:
            continue
        wt = space / place.repos[k]["dir"]
        if not wt.exists():
            continue
        repo = place.repo_path(k)
        ref = base_ref(repo, place.repos[k]["base_branch"])
        why = worktree_problem(wt, ref, doc.branch)
        if why:
            raise Stop(f"{k} 워크트리를 치우지 않는다: {why} ({wt})")
        plan.append((k, repo, wt, ref))
    dest = place.docs / DONE_DIR / doc.path.name
    for k, _, wt, _ in plan:
        print(f"{'(미리 보기) ' if dry else ''}워크트리 지움: {k} {wt}")
    print(f"{'(미리 보기) ' if dry else ''}문서 옮김: {doc.path.relative_to(place.root).as_posix()} → {dest.relative_to(place.root).as_posix()}")
    if dry:
        return 0
    for k, repo, wt, ref in plan:
        code, out = git(repo, "worktree", "remove", str(wt))
        if code != 0:
            raise Stop(f"{k} 워크트리를 지우지 못했다(쓰는 프로세스가 있으면 끄고 다시): {out[-200:]}")
        merged = ok(repo, "merge-base", "--is-ancestor", f"refs/heads/{doc.branch}", ref)
        if merged:
            git(repo, "branch", "-D", doc.branch)
        else:
            print(f"  {k} 브랜치 {doc.branch} 는 기준에 들어가지 않아 남겼다(--force 로 닫음)")
    if space.is_dir() and not any(space.iterdir()):
        space.rmdir()
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_rel = doc.path.relative_to(place.root).as_posix()
    dst_rel = dest.relative_to(place.root).as_posix()
    if not ok(place.root, "mv", src_rel, dst_rel):
        doc.path.rename(dest)
        git(place.root, "add", "--", dst_rel)
        git(place.root, "rm", "-q", "--cached", "--ignore-unmatch", "--", src_rel)
    code, out = git(place.root, "commit", "-q", "-m", f"{doc.tid} 닫음: {doc.title}", "--", src_rel, dst_rel)
    if code != 0:
        raise Stop(f"문서 이동 커밋 실패: {out[-300:]}")
    print(f"닫았다: {doc.tid} {doc.title}" + (". 하네스 커밋을 올린다(push)" if has_remote(place.root) else ""))
    return 0


# ---------------------------------------------------------------- 입구

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="작업 흐름: 열기(new) · 상태 · 닫기(done)")
    ap.add_argument("--root", type=Path, default=HERE.parents[1], help="하네스 저장소 루트")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", action="store_true", help="한 줄(세션 시작 알림용)")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new")
    n.add_argument("title")
    n.add_argument("repos", nargs="*")
    a = sub.add_parser("add")
    a.add_argument("id")
    a.add_argument("repos", nargs="+")
    d = sub.add_parser("done")
    d.add_argument("id")
    d.add_argument("--force", action="store_true")
    d.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        place = Place(args.root)
        if args.cmd == "new":
            return cmd_new(place, args.title, args.repos)
        if args.cmd == "add":
            return cmd_add(place, args.id, args.repos)
        if args.cmd == "done":
            return cmd_done(place, args.id, args.force, args.dry_run)
        return cmd_status(place, args.json, args.brief)
    except (Stop, hl.ConfigError) as exc:
        if args.brief:
            return 0  # 세션 시작 알림은 조용히
        print(f"멈춤: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
