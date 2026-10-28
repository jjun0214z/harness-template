#!/usr/bin/env python3
"""하네스 셋업 본체. macOS · Linux · Windows 공통(python3 표준 라이브러리만).

출발점은 막 산 컴퓨터일 수도, 일부 갖춘 컴퓨터일 수도 있다. 모든 단계는
「감지 → 버전 확인 → 충분하면 건너뜀 / 모자라면 올릴지 · 없으면 설치할지」를 한 표로 먼저 보여 주고 한 번에 동의받는다.
git · python 은 install.sh · install.ps1 이 파이썬보다 먼저 챙긴다. 여기서는 고른 것에 필요한 나머지
(엔진 CLI · gh · 쓸 때만 node · pnpm · 고를 때만 Orca)를 챙긴다.

  run      질문(또는 --config) → 도구 표 · 동의 → 하네스 · 저장소 새로 만들기(또는 clone) → 생성 → 엔진 설치 → 원격 → Orca → doctor
  check    설정만 검증
  tools    도구 표(--yes 면 동의한 것으로 보고 설치)
  generate 생성기만
  update   템플릿 최신본의 엔진(harness/)을 가져와 다시 생성(사람이 채운 부분은 덮지 않는다)
  add-repo 저장소를 나중에 하나 더한다(셋업 때 저장소 0개여도 된다)
  doctor   점검 표 + 나중에 할 일

두 번 돌려도 안전하다. 사람 손이 필요한 것(로그인 · 관리자 권한)은 멈추고 명령만 안내한다.

이 파일은 명령 입구다. 단계별 본체는 같은 폴더의 setup_*.py 에 있다:
  setup_common(보고 · 실행 · 질문 입력) · setup_place(자리) · setup_ask(질문) · setup_tools(도구)
  setup_repos(저장소) · setup_engines(엔진 · Orca) · setup_update(갱신) · setup_doctor(점검)
예전 `bootstrap.<이름>` 은 그대로 쓸 수 있게 여기서 다시 내보낸다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional

sys.dont_write_bytecode = True  # 형제 모듈 import 가 __pycache__ 를 하네스 저장소에 남기지 않게 한다
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402
import runners  # noqa: E402
# 단계별 본체. 예전 bootstrap.<이름> 을 그대로 쓰도록 다시 내보낸다(F401)
from setup_common import HERE, NO_INPUT, Report, TEMPLATE_ROOT, ask, ask_yes, run, say  # noqa: E402,F401
from setup_place import caller_base, dir_is_empty, project_root, resolve_target, root_problem  # noqa: E402,F401
from setup_ask import ask_git_path, ask_key, ask_repo, default_slug, detect_engines, interview, simple_interview, slugify  # noqa: E402,F401
from setup_tools import AFTER_NODE, INSTALLING, detect, logged_in, login_step, needed_tools, node_upgrade_command, orca_fallback, plan_table, plan_tools, refresh_node_path, run_install, tools_step  # noqa: E402,F401
from setup_repos import HARNESS_COMMIT, SKELETON_COMMIT, connect_files, connect_repo, created_record, first_commit, first_sha, git_identity, git_init, harness_created, has_commit, is_local_url, made_by_us, pending_first_commit, remote_step, repo_skeleton, repos_step  # noqa: E402,F401
from setup_engines import codex_config_lines, codex_config_step, codex_home, install_step, orca_step  # noqa: E402,F401
from setup_update import cmd_update, fetch_template, safe_extract  # noqa: E402,F401
from setup_doctor import blocks_step, doctor, generate_cmd, hook_smoke, report_blocks, stale_note  # noqa: E402,F401


def load_or_ask(args, kind: str) -> dict:
    if args.config:
        return hl.load_config(Path(args.config))
    if args.target and (Path(args.target) / hl.CONFIG_NAME).is_file():
        return hl.load_config(Path(args.target) / hl.CONFIG_NAME)
    if (TEMPLATE_ROOT / hl.CONFIG_NAME).is_file():
        return hl.load_config(TEMPLATE_ROOT / hl.CONFIG_NAME)
    if not args.target and not args.root:
        # 같은 폴더에서 다시 실행: 이미 만든 하네스가 있으면 새로 묻지 않고 그것을 쓴다(멱등)
        base = caller_base()
        for cand in [base] + sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
            if (cand / hl.CONFIG_NAME).is_file() and (cand / gen.MANIFEST).is_file():
                say(f"이미 만든 하네스를 쓴다: {cand}")
                args.target = str(cand)
                return hl.load_config(cand / hl.CONFIG_NAME)
    if args.non_interactive:
        raise hl.ConfigError("--non-interactive 에는 --config 가 필요하다")
    title = (getattr(args, "title", None) or "").strip() or None
    if getattr(args, "detail", False):
        cfg = interview(kind, default_root=None if (args.root or args.target) else project_root, title=title)
    else:
        fixed = Path(args.target).expanduser().resolve().parent if args.target else (Path(args.root).expanduser().resolve() if args.root else None)
        cfg = simple_interview(kind, root_fixed=fixed, runner=chosen_runner(args), title=title,
                               workflow=getattr(args, "workflow", None))
    chosen = cfg.pop("_root", None)
    if chosen and not args.target:
        args.root = chosen
    errs = hl.validate(cfg)
    if errs:
        raise hl.ConfigError("설정 오류:\n- " + "\n- ".join(errs))
    return cfg


def cmd_run(args) -> int:
    kind = hl.os_kind()
    try:
        cfg = load_or_ask(args, kind)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    title = (args.title or "").strip()
    if title and title != cfg["project"]["owner_title"]:
        say(f"--title {title} 은 새로 셋업할 때만 쓴다. 지금 설정의 호칭은 {cfg['project']['owner_title']} 그대로다"
            "(바꾸려면 harness.json 의 project.owner_title 을 고치고 generate)")
    target = resolve_target(args, cfg)
    if not args.target and cfg["harness_repo"].get("source") != "local" and not (target / hl.CONFIG_NAME).is_file():
        problem = root_problem(project_root(args, cfg), cfg, args.force)
        if problem:
            hl.eprint(problem)
            return 2
    if target.resolve() == TEMPLATE_ROOT.resolve() and (TEMPLATE_ROOT / hl.TEMPLATE_MARK_FILE).is_file():
        hl.eprint("템플릿 저장소 자신에 하네스를 만들지 않는다. --target 으로 새 프로젝트 하네스 폴더를 준다")
        return 2
    reason = None if cfg["harness_repo"].get("source") == "local" else target_problem(target, args.force)
    local_init = False
    if cfg["harness_repo"].get("source") == "local" and not (target / ".git").exists():
        if cfg["harness_repo"].get("git_init") and target.is_dir():
            git_init(target, cfg["harness_repo"]["base_branch"])
            local_init = True  # 방금 git init 한 폴더: 첫 커밋에는 생성한 파일만 담는다
        else:
            reason = f"하네스로 연결할 git 저장소가 없다: {target}"
    if reason:
        hl.eprint(reason)
        return 2
    rep = Report()
    say(f"하네스 폴더: {target}  (프로젝트 루트: {target.parent} · 템플릿 원본: {TEMPLATE_ROOT})")
    tools_step(cfg, kind, rep, yes=args.yes, interactive=not args.non_interactive)
    created = local_init or (cfg["harness_repo"].get("source") != "local" and (
        not (target / ".git").exists() or (not has_commit(target) and not (target / hl.CONFIG_NAME).is_file())))
    if not (target / ".git").exists():
        git_init(target, cfg["harness_repo"]["base_branch"])
    elif created:  # 먼저 git init 만 해 둔 빈 저장소: 기준 브랜치 이름을 맞춘다
        run(["git", "symbolic-ref", "HEAD", f"refs/heads/{cfg['harness_repo']['base_branch']}"], cwd=target)
    rep.add("하네스 저장소", "통과", f"{'새로 만듦' if created else '있음'} {target}")
    text = hl.dump_config(cfg)
    cpath = target / hl.CONFIG_NAME
    if not cpath.is_file() or cpath.read_text(encoding="utf-8") != text:
        with open(cpath, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    parent = hl.git_common_parent(target) or target.parent
    new_repos = repos_step(cfg, parent, rep, args.offline, created_record(target))
    pending = not created and harness_created(target) and not has_commit(target)
    w = gen.generate(cfg, target, created_repos=new_repos, created_harness=created)
    rep.add("생성", "통과", f"바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)} · 원래 있던 파일 보호 {len(w.protect)}")
    report_blocks(cfg, w, rep)
    for rel in sorted(w.protect):
        rep.todo(f"하네스 {rel} 은 원래 있던 파일이라 덮지 않았다. 템플릿 내용(harness/skeleton 또는 새 폴더에 생성해 본 것)과 직접 합친다")
    if cfg["harness_repo"].get("source") == "local":
        rep.todo(f"하네스 연결: {target} 에 더한 파일을 git status 로 보고 경로를 명시해 커밋한다")
    if created or pending:
        # 생성기가 이번에 쓰거나 둔 파일만(원래 있던 .env · 보호 파일은 빠진다). 다시 돌린 run 도 같은 목록을 다시 얻는다
        made = w.owned + [hl.CONFIG_NAME, gen.MANIFEST]
        first_commit(target, HARNESS_COMMIT, rep, cfg["harness_repo"]["dir"], made)
    remote_step(cfg, target, parent, rep, args.offline, args.create_github)
    if args.skip_install:
        rep.add("엔진 설치", "건너뜀", "--skip-install")
    else:
        install_step(cfg, target, parent, kind, rep)
        orca_step(cfg, target, parent, kind, rep)
    doctor(target, kind, args.offline, rep, tools=False)
    say("\n" + rep.table())
    say("\n" + next_steps(cfg, target))
    return 1 if rep.failed() else 0


def next_steps(cfg: dict, target: Path) -> str:
    """install 의 마지막 사람용 출력. 외울 것은 **폴더 하나와 「시작해」 한 마디**뿐이게 한다.
    「채울 자리」는 첫 할 일이 아니다: 저장소가 있어야 채울 수 있고, 갓 설치한 하네스는 저장소가 0개일 수 있다.
    남은 준비는 화면이 아니라 하네스 안의 파일(README.md · 상황판.md)에 남고, start 스킬이 상태를 보고 하나씩 안내한다."""
    engines = " 또는 ".join(f"`{e}`" for e in cfg["engines"])
    return "\n".join([
        f"하네스가 준비됐습니다: {target}",
        f"다음: 그 폴더에서 {engines} 를 열고 「시작해」라고 하세요. 남은 준비를 순서대로 하나씩 안내합니다.",
        "막히면 「점검해」 · 처음부터 다시 잡으려면 「셋업해」라고 하세요.",
        f"같은 안내가 {target / 'README.md'} 와 {target / '상황판.md'} 에도 남아 있습니다.",
    ])


def target_problem(target: Path, force: bool) -> Optional[str]:
    """하네스를 만들면 안 되는 자리면 이유. 비었거나 없거나 우리 하네스(harness.json)면 None."""
    if not target.exists() or (target / hl.CONFIG_NAME).is_file():
        return None
    entries = [p for p in target.iterdir() if p.name != ".DS_Store"]
    if not entries:
        return None
    if [p.name for p in entries] == [".git"] and not has_commit(target):
        return None  # 사용자가 먼저 git init 만 해 둔 빈 폴더
    if (target / ".git").exists():
        return (f"{target} 는 이미 다른 git 저장소다(harness.json 없음). 남의 저장소에 하네스를 섞지 않는다. "
                "--force 여도 멈춘다. 빈 폴더나 새 이름을 --target 으로 준다")
    if not force:
        return (f"{target} 가 비어 있지 않고 하네스 표시(harness.json)가 없다. 안에 있는 파일을 덮거나 커밋하지 않도록 멈춘다. "
                "그래도 여기에 만들려면 --force (원래 있던 파일은 커밋에 넣지 않는다)")
    return None


def cmd_add_repo(args) -> int:
    """저장소 하나를 나중에 더한다: harness.json 에 추가 → 준비(새로 만들기 · 받기 · 연결) → 생성 → 그 저장소에만 설치 · 신뢰 · 등록. 멱등."""
    kind = hl.os_kind()
    target = resolve_target(args, None)
    cpath = target / hl.CONFIG_NAME
    try:
        cfg = hl.load_config(cpath)
    except hl.ConfigError as exc:
        hl.eprint(f"{exc}\n하네스 폴더에서 부르거나 --target 으로 하네스 폴더를 준다")
        return 2
    if args.repo:
        try:
            raw = json.loads(Path(args.repo).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            hl.eprint(f"저장소 설정 조각을 못 읽음: {args.repo} ({exc})")
            return 2
        new = raw if isinstance(raw, list) else [raw]
    else:
        r = ask_repo(cfg["project"].get("github_org") or "", len(cfg["repos"]) + 1, detail=args.detail)
        if r is None:
            say("더할 저장소가 없다")
            return 0
        new = [r]
    known = {r["key"]: r for r in cfg["repos"]}
    added = []
    for r in new:
        if r.get("key") in known:
            say(f"저장소 {r['key']} 는 이미 있다. 설정은 그대로 두고 준비 · 설치만 다시 확인한다")
            continue
        cfg["repos"].append(r)
        added.append(r.get("key"))
    raw_cfg = json.loads(cpath.read_text(encoding="utf-8"))
    raw_cfg["repos"] = [dict(r) for r in raw_cfg.get("repos", [])] + [r for r in new if r.get("key") in added]
    cfg = hl.normalize(raw_cfg)
    errs = hl.validate(cfg)
    if errs:
        hl.eprint("설정 오류:\n- " + "\n- ".join(errs))
        return 2
    keys = {r.get("key") for r in new}
    sub = dict(cfg, repos=[r for r in cfg["repos"] if r["key"] in keys])
    text = hl.dump_config(cfg)
    if cpath.read_text(encoding="utf-8") != text:
        with open(cpath, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    rep = Report()
    parent = hl.git_common_parent(target) or target.parent
    new_repos = repos_step(sub, parent, rep, args.offline, created_record(target))
    w = gen.generate(cfg, target, created_repos=new_repos)
    rep.add("생성", "통과", f"바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)}")
    report_blocks(cfg, w, rep)
    remote_step(sub, target, parent, rep, args.offline, args.create_github)
    if args.skip_install:
        rep.add("엔진 설치", "건너뜀", "--skip-install")
    else:
        install_step(sub, target, parent, kind, rep)
        orca_step(sub, target, parent, kind, rep)
    # 새 작업자 정의는 세션이 시작할 때 읽힌다: 이미 열려 있는 세션에서는 그 이름을 부를 수 없다(실측: Agent type not found).
    if harness_created(target) and not has_commit(target) and not git_identity(target):
        rep.identity_todo(cfg["harness_repo"]["dir"])  # 첫 커밋을 미룬 하네스도 같은 할 일의 대상이다
    workers = " · ".join(f"`{k}-worker`" for k in added)
    if added:
        rep.todo(f"새 작업자 {workers} 를 쓰려면 세션(claude · codex)을 닫고 다시 연다. "
                 "에이전트 목록은 세션이 시작할 때 읽히므로 지금 열려 있는 세션에서는 그 이름을 찾지 못한다")
    say(rep.table())
    say(f"\n더한 저장소: {', '.join(added) or '없음(이미 있음)'}. 하네스 변경(harness.json · 작업자 · 지도)을 보고 경로를 명시해 커밋한다")
    if added:
        say(f"새 작업자 {workers} 는 지금 열려 있는 세션에서는 보이지 않습니다. "
            "세션(claude · codex)을 닫고 다시 열면 쓸 수 있습니다(에이전트 목록은 세션이 시작할 때 읽힙니다).")
    return 1 if rep.failed() else 0


def chosen_runner(args) -> Optional[str]:
    """--runner 가 있으면 그것. 옛 --no-orca 는 「기본 방식」으로 읽는다(질문을 건너뛴다). 없으면 None(묻는다)."""
    if getattr(args, "runner", None):
        return args.runner
    return runners.DEFAULT_RUNNER if getattr(args, "no_orca", False) else None


def config_for(args) -> dict:
    return hl.load_config(Path(args.config) if args.config else resolve_target(args, None) / hl.CONFIG_NAME)


def cmd_check(args) -> int:
    try:
        cfg = config_for(args)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    say(f"설정 통과: {cfg['project']['name']} · 저장소 {len(cfg['repos'])} · 엔진 {', '.join(cfg['engines'])} · 작업자 {runners.label(cfg)} · 묶음 {', '.join(cfg['packs']) or '없음'}")
    return 0


def cmd_tools(args) -> int:
    try:
        cfg = config_for(args)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    rep = Report()
    tools_step(cfg, hl.os_kind(), rep, yes=args.yes, interactive=False)
    say(rep.table())
    return 0


def cmd_generate(args) -> int:
    target = resolve_target(args, None)
    path = Path(args.config) if args.config else target / hl.CONFIG_NAME
    try:
        if hl.migrate_config_file(path):
            say(f"설정 이전: {path} 의 orca.enabled 를 runner 로 옮겼다")
        cfg = hl.load_config(path)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    w = gen.generate(cfg, target)
    say(f"생성: 바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)}")
    for line in gen.block_lines(w):
        say(line)
    for line in gen.stale_lines(getattr(w, "stale_names", [])):
        say(line)
    return 0


def cmd_doctor(args) -> int:
    rep = doctor(resolve_target(args, None), hl.os_kind(), args.offline)
    say(rep.table())
    return 1 if rep.failed() else 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    if sys.version_info < hl.MIN_PYTHON:
        hl.eprint("python 3.9 이상이 필요하다")
        return 2
    ap = argparse.ArgumentParser(description="하네스 셋업")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="harness.json 경로")
    common.add_argument("--root", help="프로젝트 루트(기본: 실행한 폴더/<프로젝트 슬러그>). 그 안에 하네스와 새 저장소를 만든다")
    common.add_argument("--target", help="하네스 폴더를 직접 준다(기본: <프로젝트 루트>/<harness_repo.dir>)")
    common.add_argument("--offline", action="store_true", help="gh 를 부르지 않고 원격 clone · fetch 를 건너뛴다(로컬 경로 원격은 받는다)")
    common.add_argument("--yes", "--install-tools", dest="yes", action="store_true", help="도구 표의 설치 · 올림에 동의한 것으로 본다(무인 진행)")
    p = sub.add_parser("run", parents=[common], help="끝까지 셋업")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--create-github", action="store_true", help="gh 로그인이 있으면 원격 저장소(비공개) · 라벨을 만든다")
    p.add_argument("--skip-install", action="store_true", help="엔진 플러그인 · Orca 등록을 건너뛴다")
    p.add_argument("--force", action="store_true", help="비어 있지 않은 폴더(git 저장소가 아닌 것)에 하네스를 만든다")
    p.add_argument("--detail", action="store_true", help="기본값을 쓰지 않고 전부 묻는다(GitHub · 호칭 · 하네스 연결 · 저장소 · 스킬 · 작업자 실행 방식)")
    p.add_argument("--runner", choices=runners.RUNNERS,
                   help=f"작업자 실행 방식(기본: {runners.DEFAULT_RUNNER}). 주면 그 질문을 묻지 않는다. orca 를 고를 때만 Orca 를 설치한다")
    p.add_argument("--no-orca", action="store_true", help=argparse.SUPPRESS)  # 옛 옵션: --runner subagent 와 같다
    p.add_argument("--workflow", dest="workflow", action="store_true", default=None, help="작업 흐름 묶음을 쓴다(질문을 건너뜀)")
    p.add_argument("--no-workflow", dest="workflow", action="store_false", help="작업 흐름 묶음을 쓰지 않는다(질문을 건너뜀)")
    p.add_argument("--title", "--owner-title", dest="title",
                   help=f"결정권자를 부르는 호칭(기본: {hl.DEFAULT_OWNER_TITLE}). 새로 셋업할 때만 쓴다. 이미 만든 하네스는 harness.json 을 고친다")
    for name in ("check", "tools", "generate", "doctor"):
        sub.add_parser(name, parents=[common])
    a = sub.add_parser("add-repo", parents=[common], help="저장소를 나중에 하나 더한다(새로 만들기 · 원격 받기 · 폴더 연결)")
    a.add_argument("--repo", help="저장소 설정 조각(JSON, 객체 하나 또는 목록). 없으면 대화로 묻는다")
    a.add_argument("--create-github", action="store_true", help="gh 로그인이 있으면 새 저장소의 원격(비공개)을 만든다")
    a.add_argument("--skip-install", action="store_true", help="엔진 플러그인 · Orca 등록을 건너뛴다")
    a.add_argument("--detail", action="store_true", help="폴더 · 원격 · 브랜치 · 설명 · 배포 의미 · push 브랜치 · 검사 명령까지 묻는다")
    u = sub.add_parser("update", parents=[common], help="템플릿 최신본의 엔진을 가져와 다시 생성")
    u.add_argument("--source", help="템플릿 주소 또는 로컬 폴더(기본: harness.json template.url)")
    handlers = {"run": cmd_run, "check": cmd_check, "tools": cmd_tools, "generate": cmd_generate,
                "doctor": cmd_doctor, "update": cmd_update, "add-repo": cmd_add_repo}
    argv = list(argv if argv is not None else sys.argv[1:])
    # 하위 명령 없이 옵션만 오면 run 의 옵션이다: 한 줄 설치의 `bash -s -- --title 팀장님` · `--detail`
    if not argv or (argv[0] not in handlers and argv[0] not in ("-h", "--help")):
        argv = ["run"] + argv
    args = ap.parse_args(argv)
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
