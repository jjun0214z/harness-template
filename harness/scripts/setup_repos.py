"""셋업 저장소: 하네스 · 코드 저장소를 새로 만들기 · 받기 · 연결, 첫 커밋, 원격."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import List

import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402
from setup_common import Report, run  # noqa: E402
from setup_tools import logged_in  # noqa: E402


def is_local_url(url: str) -> bool:
    return url.startswith("file://") or os.path.isabs(url) or url.startswith(".")


def git_identity(cwd: Path) -> bool:
    """커밋할 이름 · 메일이 명시돼 있나(설정 또는 환경변수). git 이 호스트 이름으로 지어내는 것은 쓰지 않는다."""
    email = os.environ.get("GIT_AUTHOR_EMAIL") or run(["git", "config", "user.email"], cwd=cwd)[1]
    name = os.environ.get("GIT_AUTHOR_NAME") or run(["git", "config", "user.name"], cwd=cwd)[1]
    return bool(email) and bool(name)


def git_init(dest: Path, base: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if run(["git", "init", "-q", "-b", base], cwd=dest)[0] != 0:  # 옛 git 은 -b 가 없다
        run(["git", "init", "-q"], cwd=dest)
        run(["git", "symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=dest)


def has_commit(dest: Path) -> bool:
    return run(["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "HEAD"])[0] == 0


def first_commit(dest: Path, message: str, rep: Report, label: str, files: List[str]) -> None:
    """우리가 방금 만든 저장소에만 쓴다(커밋이 없을 때만). 우리가 만든 파일만 담는다(폴더에 원래 있던 .env 등은 담지 않는다).
    이름 · 이메일이 없으면 만들지 않고 할 일로 남긴다."""
    if has_commit(dest):
        return
    files = sorted({f for f in files if (dest / f).exists()})
    if not git_identity(dest):
        rep.identity_todo(label)
        return
    run(["git", "-C", str(dest), "add", "--", *files])
    code, out = run(["git", "-C", str(dest), "commit", "-q", "-m", message])
    if code != 0:
        rep.todo(f"{label} 첫 커밋 실패: {out[-160:]}")


SKELETON_COMMIT = "chore: 저장소 뼈대 (하네스 생성)"


HARNESS_COMMIT = "chore: 하네스 초기 생성"


def created_record(target: Path) -> dict:
    """하네스 .harness-manifest.json 의 「이 하네스가 만든 저장소」 기록: 폴더 → 첫 커밋 SHA."""
    try:
        return dict(json.loads((target / gen.MANIFEST).read_text(encoding="utf-8")).get("repos") or {})
    except (OSError, ValueError):
        return {}


def harness_created(target: Path) -> bool:
    """이 하네스 저장소를 bootstrap 이 새로 만들었나(manifest 기록). 첫 커밋을 미뤘다가 다시 돌릴 때 쓴다."""
    try:
        return bool(json.loads((target / gen.MANIFEST).read_text(encoding="utf-8")).get("created_harness"))
    except (OSError, ValueError):
        return False


def pending_first_commit(dest: Path, is_harness: bool, record: dict) -> bool:
    """우리가 만들었는데 아직 첫 커밋이 없는 저장소인가(이름 · 메일이 없어 미룬 것). 남의 저장소는 아니다."""
    if has_commit(dest):
        return False
    return harness_created(dest) if is_harness else record.get(dest.name) == ""


def first_sha(dest: Path) -> str:
    code, out = run(["git", "-C", str(dest), "rev-list", "--max-parents=0", "HEAD"])
    return out.splitlines()[-1] if code == 0 and out else ""


def made_by_us(dest: Path, record: dict) -> bool:
    """이 하네스가 만든 코드 저장소인가: 만들 때 적어 둔 폴더와 첫 커밋 SHA 로 판별한다.
    기록에 없으면(커밋 없는 빈 git 폴더 포함) 남의 것으로 보고 「기존 저장소 연결」로만 다룬다."""
    if dest.name not in record:
        return False
    sha = record[dest.name]
    return not sha or sha == first_sha(dest)


def repo_skeleton(cfg: dict, r: dict) -> dict:
    """새 코드 저장소의 최소 뼈대. 하네스 플러그인을 켜는 설정까지."""
    slug, harness = cfg["project"]["slug"], cfg["harness_repo"]["dir"]
    files = {
        "README.md": f"# {r['dir']}\n\n{r.get('description') or ''}\n",
        ".gitignore": ".DS_Store\n__pycache__/\nnode_modules/\n" + hl.SECRET_IGNORE,
        "CLAUDE.md": (f"# {r['dir']}\n\n{cfg['project']['name']} 의 {r['dir']} 저장소다. 작업 규칙은 `{slug}` 플러그인 스킬을 따른다.\n"
                      f"오케스트레이터(하네스)는 `../{harness}` 다. 기준 브랜치 `{r['base_branch']}`.\n"),
        "AGENTS.md": f"# {r['dir']}\n\n같은 폴더의 `CLAUDE.md` 를 처음부터 끝까지 읽고 따른다. 설치된 `{slug}` 플러그인의 스킬을 쓴다.\n",
    }
    if hl.has(cfg, "claude"):
        settings = {"enabledPlugins": {f"{slug}@{slug}": True}}
        remote = cfg["harness_repo"].get("remote")
        if remote:
            settings["extraKnownMarketplaces"] = {slug: {"source": {"source": "github", "repo": remote}}}
        files[".claude/settings.json"] = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    return files


def connect_files(cfg: dict, r: dict) -> dict:
    """연결한 저장소에 (동의했을 때만) 더하는 최소 파일. 있으면 덮지 않는다."""
    files = {k: v for k, v in repo_skeleton(cfg, r).items() if k in ("CLAUDE.md", "AGENTS.md", ".claude/settings.json")}
    return files


def connect_repo(cfg: dict, r: dict, dest: Path, rep: Report) -> None:
    """이미 이 컴퓨터에 있는 저장소 연결. 코드 · 이력 · 원격 · 브랜치는 건드리지 않는다."""
    label = f"저장소 {r['dir']}"
    if not (dest / ".git").exists():
        rep.add(label, "실패", f"연결할 git 저장소가 없다: {dest}")
        return
    added, kept = [], []
    if r.get("connect_files"):
        for rel, text in connect_files(cfg, r).items():
            path = dest / rel
            if path.exists():
                kept.append(rel)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            added.append(rel)
    detail = f"연결(코드 · 이력 · 원격 · 브랜치 그대로) {dest}"
    if added:
        detail += f" · 더한 파일 {', '.join(added)}"
        rep.todo(f"{r['dir']}: 더한 파일({', '.join(added)})을 검토하고 직접 커밋한다")
    for rel in kept:
        rep.todo(f"{r['dir']}: {rel} 이 이미 있어 덮지 않았다. 필요하면 {cfg['project']['slug']} 플러그인 켜기 · 하네스 안내를 직접 합친다")
    if not r.get("connect_files"):
        rep.todo(f"{r['dir']}: 작업자용 최소 파일(CLAUDE.md · AGENTS.md · .claude/settings.json)은 더하지 않았다. 원하면 harness.json 에 connect_files: true 로 두고 {hl.boot_cmd('run')}")
    rep.add(label, "통과", detail)


def repos_step(cfg: dict, parent: Path, rep: Report, offline: bool, record: dict) -> dict:
    """저장소를 준비하고, 이번에 새로 만든 저장소의 {폴더: 첫 커밋 SHA} 를 돌려준다."""
    created = {}
    for r in cfg["repos"]:
        dest = hl.repo_path(r, parent)
        label = f"저장소 {r['dir']}"
        if r["source"] == "local":
            connect_repo(cfg, r, dest, rep)
            continue
        if (dest / ".git").exists():
            if r["source"] == "clone" and (not offline or is_local_url(hl.repo_url(r))):
                run(["git", "-C", str(dest), "fetch", "--prune", "--quiet", "origin"], timeout=300)
            if r["source"] == "new" and record.get(r["dir"]) == "" and not has_commit(dest):
                # 이름 · 메일이 없어 첫 커밋을 미룬 우리 뼈대: 이제 신원이 있으면 뼈대 파일만 담아 커밋한다
                first_commit(dest, SKELETON_COMMIT, rep, r["dir"], list(repo_skeleton(cfg, r)))
            if record.get(r["dir"]) == "" and has_commit(dest):
                created[r["dir"]] = first_sha(dest)  # 이름 · 메일이 없어 커밋 전에 기록한 저장소: 이제 첫 커밋 SHA 를 채운다
            if r["source"] == "new" and not made_by_us(dest, record):
                rep.add(label, "안내", f"기존 저장소 연결(우리 뼈대가 아니라 덮지 · push 하지 않음) {dest}")
            else:
                rep.add(label, "통과", f"있음, 덮지 않음 {dest}")
            continue
        if r["source"] == "clone":
            url = hl.repo_url(r)
            if offline and not is_local_url(url):
                rep.add(label, "건너뜀", f"--offline 이라 clone 안 함 ({url})")
                rep.todo(f"{r['dir']} 받기: git clone {url} \"{dest}\"")
                continue
            code, out = run(["git", "clone", "--quiet", url, str(dest)], timeout=1800)
            if code != 0:
                rep.add(label, "실패", f"clone 실패: {out[-200:]}")
                continue
            base = r["base_branch"]
            if run(["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", base])[0] != 0:
                run(["git", "-C", str(dest), "fetch", "--quiet", "origin", f"{base}:{base}"], timeout=300)
            rep.add(label, "통과", f"clone {url}")
            continue
        if dest.exists() and any(dest.iterdir()):
            rep.add(label, "안내", f"git 이 아닌 폴더가 이미 있어 건드리지 않음 {dest}")
            rep.todo(f"{r['dir']}: 폴더 {dest} 를 확인하고 git init 하거나 비운다")
            continue
        git_init(dest, r["base_branch"])
        skeleton = repo_skeleton(cfg, r)
        for rel, text in skeleton.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        first_commit(dest, SKELETON_COMMIT, rep, r["dir"], list(skeleton))
        created[r["dir"]] = first_sha(dest)
        rep.add(label, "통과", f"새로 만듦(git init -b {r['base_branch']}) {dest}")
    return created


def remote_step(cfg: dict, target: Path, parent: Path, rep: Report, offline: bool, create: bool) -> None:
    """원격 연결. origin 이 있으면 건드리지 않는다. gh 가 있고 --create-github 면 없는 원격을 만들고 push, 있으면 연결만."""
    rows = [(cfg["harness_repo"]["dir"], target, cfg["harness_repo"].get("remote"), cfg["harness_repo"]["base_branch"])]
    rows += [(r["dir"], hl.repo_path(r, parent), r.get("remote"), r["base_branch"]) for r in cfg["repos"] if r["source"] == "new"]
    record = created_record(target)
    foreign = {r["dir"] for r in cfg["repos"] if r["source"] == "new" and (hl.repo_path(r, parent) / ".git").exists()
               and not made_by_us(hl.repo_path(r, parent), record)}
    gh = shutil.which("gh")
    gh_ok = bool(gh) and not offline and logged_in("gh", hl.os_kind()) is True
    if not offline:
        rep.add("GitHub 로그인", "통과" if gh_ok else "없음",
                "gh auth status" if gh_ok else ("gh 가 없다(선택)" if not gh else "로그인 안 됨(선택)"))
    for name, path, remote, base in rows:
        if not (path / ".git").exists():
            continue
        if run(["git", "remote", "get-url", "origin"], cwd=path)[0] == 0:
            continue
        if name in foreign:
            rep.todo(f"{name} 는 이 하네스가 만든 저장소가 아니다. 원격이 필요하면 직접 연결한다(자동으로 push 하지 않는다)")
            continue
        if not remote:
            rep.todo(f"{name} 원격 연결(원하면): GitHub 에 저장소를 만든 뒤 git -C \"{path}\" remote add origin <주소> && git -C \"{path}\" push -u origin {base}")
            continue
        url = f"https://github.com/{remote}.git"
        if gh_ok and create:
            if run([gh, "repo", "view", remote], timeout=30)[0] == 0:
                run(["git", "remote", "add", "origin", url], cwd=path)
                rep.add(f"원격 {name}", "통과", f"{remote} 이미 있어 연결만")
                rep.todo(f"{name} push: git -C \"{path}\" push -u origin {base} (원격 내용과 겹치는지 먼저 본다)")
            else:
                args = [gh, "repo", "create", remote, "--private", "--source", str(path)]
                if has_commit(path):
                    args.append("--push")
                code, out = run(args, timeout=120)
                rep.add(f"원격 {name}", "통과" if code == 0 else "실패", f"{remote} 비공개로 만듦" if code == 0 else out[-200:])
        else:
            rep.todo(f"{name} 원격 연결: gh repo create {remote} --private --source \"{path}\" --push  "
                     f"(gh 없이: GitHub 에서 {remote} 를 만든 뒤 git -C \"{path}\" remote add origin {url} && git -C \"{path}\" push -u origin {base})")
    harness_remote = cfg["harness_repo"].get("remote")
    if gh_ok and create and harness_remote:
        labels = list(cfg["labels"]) + [f"repo:{r['key']}" for r in cfg["repos"]]
        bad = [lb for lb in labels if run([gh, "label", "create", lb, "-R", harness_remote, "--force"], timeout=30)[0] != 0]
        rep.add("이슈 라벨", "실패" if bad else "통과", ("못 만든 것: " + ", ".join(bad)) if bad else " · ".join(labels))
    elif harness_remote:
        rep.todo(f"이슈 라벨: 원격을 만든 뒤 {hl.boot_cmd('run --create-github')} 을 다시 돌리면 만든다")
    else:
        # 원격이 비어 있으면 라벨 이야기가 한 줄도 안 나왔다. 이슈 추적이 원격에 달려 있다는 것을 알린다.
        rep.todo("이슈 추적: 하네스 원격을 만들면 이슈 · 라벨을 쓸 수 있다"
                 f"(만든 뒤 {hl.boot_cmd('run --create-github')} 이 라벨 {' · '.join(cfg['labels'])} 까지 만든다). 그때까지는 `상황판.md` 를 쓴다")
