"""셋업 점검: doctor 표(설정 · 생성물 · 관리 블록 · 낡은 이름 · 훅 · 저장소 · 도구)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402
from setup_common import Report, run  # noqa: E402
from setup_tools import detect, needed_tools  # noqa: E402
from setup_repos import created_record, git_identity, has_commit, pending_first_commit  # noqa: E402
from setup_engines import codex_config_lines, codex_home  # noqa: E402


def hook_smoke(cfg: dict, target: Path, rep: Report) -> None:
    slug = cfg["project"]["slug"]
    pdir = target / "plugins" / slug
    env = dict(os.environ, PLUGIN_ROOT=str(pdir))
    payload = json.dumps({"tool_name": "Bash", "cwd": str(target), "tool_input": {"command": "git push --force origin main"}})
    try:
        r = subprocess.run([sys.executable, str(pdir / "scripts" / "guard-push.py")], input=payload,
                           capture_output=True, text=True, env=env, timeout=30)
        denied = '"deny"' in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        denied = False
    rep.add("훅 push 가드", "통과" if denied else "실패", "강제 push 를 막는다" if denied else "강제 push 를 막지 못했다")
    try:
        r = subprocess.run([sys.executable, str(pdir / "scripts" / "core-context.py")], input="{}",
                           capture_output=True, text=True, env=env, timeout=30)
        ok = cfg["project"]["name"] in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    rep.add("훅 핵심 요약", "통과" if ok else "실패", "core.md 를 세션에 넣는다" if ok else "core.md 출력 없음")


def generate_cmd(cfg: dict) -> str:
    return hl.boot_cmd("", python=cfg["platform"]["python"]).replace("bootstrap.py", "generate.py").strip()


def stale_note(cfg: dict, w: hl.Writer, rep: Report) -> None:
    """사람이 고쳐서 생성기가 건드리지 않은 자리를 파일 · 절 이름과 함께 알린다. run · add-repo · doctor 가 같이 쓴다."""
    if not w.stale:
        return
    where = gen.block_where(w.stale)
    rep.add("설정 표(사람이 고침)", "안내", f"{where} 는 사람이 고친 자리라 건드리지 않는다. 낡았으면 **직접 고쳐야 한다**")
    rep.todo(f"직접 고쳐야 하는 설정 표: {where}. `{generate_cmd(cfg)}` 는 이 자리를 건드리지 않는다"
             "(생성기 출력 그대로가 아니면 사람이 쓴 글로 본다). 내용을 보고 직접 맞춘다")


def report_blocks(cfg: dict, w: hl.Writer, rep: Report) -> None:
    """생성 결과 중 관리 블록 소식(심었다 · 직접 고쳐야 한다)을 표에 적는다."""
    if w.inserted:
        rep.add("낡은 설정 표", "통과",
                f"관리 블록 {len(w.inserted)}곳을 심고 갱신했다(바꾸기 전 `.{hl.BACKUP_SUFFIX}-<시각>` 백업): "
                f"{gen.block_where(w.inserted)}")
    stale_note(cfg, w, rep)


def blocks_step(cfg: dict, target: Path, rep: Report) -> None:
    """설정에서 만드는 표(관리 블록)가 낡았는지. 고칠 수 있는 것과 사람이 직접 고쳐야 하는 것을 갈라 파일 이름으로 보여 준다.
    판정은 생성기를 `--dry-run` 으로 돌려 받는다(파일을 쓰지 않는다. 판정 규칙이 생성기 한 곳에만 있게 한다)."""
    try:
        w = gen.generate(cfg, target, dry_run=True)
    except (OSError, ValueError, KeyError) as exc:
        rep.add("설정 표(관리 블록)", "실패", f"확인하지 못했다: {exc}")
        return
    cmd = generate_cmd(cfg)
    if w.inserted:
        rep.add("설정 표(관리 블록)", "없음",
                f"낡은 표 {len(w.inserted)}곳: {gen.block_where(w.inserted)} → `{cmd}` 를 돌리면 갱신한다")
        rep.todo(f"낡은 설정 표 갱신: `{cmd}` 를 돌린다(바꾸기 전 `.{hl.BACKUP_SUFFIX}-<시각>` 백업을 남긴다). "
                 f"대상: {gen.block_where(w.inserted)}")
    else:
        rep.add("설정 표(관리 블록)", "통과", "설정에서 만드는 표가 모두 최신이다" if not w.stale else "자동으로 맞출 것은 없다")
    stale_note(cfg, w, rep)


def doctor(target: Path, kind: str, offline: bool, rep: Optional[Report] = None, tools: bool = True) -> Report:
    rep = rep or Report()
    rep.add("운영체제", "안내", f"{kind} · python {sys.version.split()[0]}")
    try:
        cfg = hl.load_config(target / hl.CONFIG_NAME)
    except hl.ConfigError as exc:
        rep.add("설정", "실패", str(exc).replace("\n", " "))
        return rep
    rep.add("설정", "통과", f"{target / hl.CONFIG_NAME}")
    try:
        files = json.loads((target / gen.MANIFEST).read_text(encoding="utf-8"))["files"]
        missing = [f for f in files if not (target / f).is_file()]
        rep.add("생성물", "실패" if missing else "통과", ("빠짐: " + ", ".join(missing[:5])) if missing else f"관리 파일 {len(files)}개")
    except (OSError, ValueError, KeyError):
        rep.add("생성물", "실패", "생성 기록이 없다. generate 를 돌린다")
    blocks_step(cfg, target, rep)
    stale = gen.stale_from_manifest(target, cfg)
    rep.add("낡은 이름 · 호칭", "안내" if stale else "통과",
            f"{len(stale)}곳 남음" if stale else "바꾼 이름 · 호칭이 남은 자리 없음")
    if stale:
        rep.todo("\n".join(gen.stale_lines(stale)))
    hook_smoke(cfg, target, rep)
    parent = hl.git_common_parent(target) or target.parent
    record = created_record(target)
    for p, name in [(target, cfg["harness_repo"]["dir"])] + [(hl.repo_path(r, parent), r["dir"]) for r in cfg["repos"]]:
        if not (p / ".git").exists():
            rep.add(f"저장소 {name}", "없음", str(p))
            continue
        origin = run(["git", "remote", "get-url", "origin"], cwd=p)[0] == 0
        rep.add(f"저장소 {name}", "통과", ("원격 연결됨" if origin else "로컬만(원격 없음)") + f" · {p}")
        if not has_commit(p) and pending_first_commit(p, p == target, record) and not git_identity(p):
            rep.identity_todo(name)  # run 이 남긴 것과 같은 할 일 하나로 모인다
        elif not has_commit(p) and pending_first_commit(p, p == target, record):
            rep.todo(f"{name} 첫 커밋: 하네스 폴더에서 {hl.boot_cmd('run')} 을 다시 돌리면 생성한 파일만 담아 만든다")
        elif not has_commit(p):
            rep.todo(f"{name} 첫 커밋: git -C \"{p}\" status 로 담을 파일을 확인하고 경로를 명시해 add 한 뒤 커밋한다(원래 있던 .env 같은 파일은 담지 않는다)")
    if tools:
        for tool in needed_tools(cfg):
            path, info, _ = detect(tool, kind)
            rep.add(f"도구 {tool}", "통과" if path else "없음", info)
    if hl.has(cfg, "codex"):
        cpath = codex_home() / "config.toml"
        text = cpath.read_text(encoding="utf-8") if cpath.is_file() else ""
        miss = [h for h, _ in codex_config_lines(cfg, target, parent) if h not in text]
        rep.add("Codex 설정", "통과" if not miss else "없음", "마켓플레이스 · 신뢰" if not miss else f"빠진 항목 {len(miss)}개. run 을 다시 돌린다")
    if hl.has(cfg, "claude"):
        claude = hl.find_tool("claude", kind, hl.tool_extra_paths("claude", kind))
        if claude:
            code, out = run([claude, "plugin", "marketplace", "list"], timeout=60)
            slug = cfg["project"]["slug"]
            rep.add("Claude 마켓플레이스", "통과" if code == 0 and slug in out else "없음", slug)
    return rep
