"""셋업 질문: 기본 질문(이름 · 엔진 · 작업자 실행 방식 · 작업 흐름)과 --detail 의 네 묶음 질문."""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Callable, Optional

import harnesslib as hl  # noqa: E402
import runners  # noqa: E402
from setup_common import ask, ask_yes, say  # noqa: E402
from setup_place import caller_base, project_root, root_problem  # noqa: E402


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]", "", text.strip().lower().replace(" ", "-").replace("_", "-"))
    s = re.sub(r"^[^a-z]+", "", s).strip("-")[:40]
    return s


def default_slug(name: str) -> str:
    """이름에서 슬러그를 만든다. 한글 이름처럼 영문이 없으면 폴더 이름, 그것도 없으면 project."""
    return slugify(name) or slugify(caller_base().name) or "project"


def ask_key(n: int, reader: Callable[[str], str]) -> Optional[str]:
    """저장소 키: 영문 소문자로 시작, 소문자 · 숫자 · - 만. 틀리면 다시 묻는다. 빈칸 = 끝."""
    while True:
        key = ask(f"저장소 {n} 키(영문 소문자로 시작, 작업자 이름 <키>-worker. 빈칸 = 끝)", "", reader)
        if not key or hl.REPO_KEY_RE.match(key):
            return key or None
        say(f"  「{key}」 는 키로 쓸 수 없다. 영문 소문자로 시작하고 소문자 · 숫자 · - 만 쓴다(예: web, api-server)")


def ask_git_path(prompt: str, reader: Callable[[str], str]) -> Path:
    """연결할 폴더 경로를 받자마자 확인한다: 없으면 다시 묻는다."""
    while True:
        raw = ask(prompt, "", reader)
        path = Path(os.path.expanduser(raw)).resolve() if raw else None
        if path and path.is_dir():
            return path
        say(f"  폴더가 없다: {raw or '(빈칸)'}. 다시 입력한다")


def ask_repo(org: str, n: int, reader: Callable[[str], str] = input, detail: bool = True) -> Optional[dict]:
    """저장소 하나를 묻는다(셋업 · add-repo 공통). 키를 빈칸으로 두면 None.
    detail=False 면 키 · 방식 · (경로 · 주소) · 스택만 묻고 나머지는 기본값(배포 의미 · push 브랜치 · 검사는 --detail 에서만)."""
    key = ask_key(n, reader)
    if not key:
        return None
    r = {"key": key}
    how = ask("  (1) 새로 만들기 (2) 원격 주소에서 받기(clone) (3) 이 컴퓨터에 있는 폴더 연결", "1", reader)
    if how == "3":
        r["source"] = "local"
        while True:
            local = ask_git_path("  연결할 폴더 경로(다른 위치여도 된다. 옮기지 않는다)", reader)
            if (local / ".git").exists():
                break
            say(f"  git 저장소가 아니다: {local}. 코드 저장소 연결은 git 저장소만 된다. 다시 입력한다")
        r["path"] = str(local)
        r["dir"] = local.name
        if detail:
            r["remote"] = ask("  GitHub 원격(조직/이름)", hl.github_remote(local), reader)
            r["base_branch"] = ask("  기준 브랜치(그 저장소의 기본 브랜치)", hl.detect_base_branch(local) or "main", reader)
        r["connect_files"] = ask_yes("  작업자용 최소 파일(CLAUDE.md · AGENTS.md · .claude/settings.json)을 없는 것만 더할까", False, reader)
    else:
        if how == "2":
            r["source"], r["url"] = "clone", ask("  원격 주소", "", reader)
        if detail:
            r["dir"] = ask("  폴더", key, reader)
            r["remote"] = ask("  GitHub 원격(조직/이름, 없으면 빈칸)", f"{org}/{key}" if org else "", reader)
            r["base_branch"] = ask("  기준 브랜치", "main", reader)
    if detail:
        r["description"] = ask("  한 줄 설명", "", reader)
    stack = ask("  스택 (1) node/pnpm (2) python (3) 없음", "3", reader)
    r["stack"] = {"1": "node", "2": "python"}.get(stack, "none")
    if not detail:
        return r
    deploy = {}
    for pair in filter(None, (s.strip() for s in ask("  배포 의미(브랜치=의미, 쉼표로. 예: develop=dev 배포,main=상용 배포)", "", reader).split(","))):
        if "=" in pair:
            b, m = pair.split("=", 1)
            deploy[b.strip()] = m.strip()
    r["deploy"] = deploy
    r["ask_on_push"] = [s.strip() for s in ask("  push 때 물을 브랜치(쉼표로, 없으면 빈칸)", "", reader).split(",") if s.strip()]
    checks = ask("  검사 명령(쉼표로, 빈칸이면 스택 기본값)", "", reader)
    if checks:
        r["checks"] = [s.strip() for s in checks.split(",") if s.strip()]
    return r


def detect_engines(kind: str) -> str:
    """엔진 기본값: 설치 · 로그인된 것. 둘 다 있으면 Claude. 아무것도 없으면 Claude."""
    have = {e: hl.find_tool(e, kind, hl.tool_extra_paths(e, kind)) is not None for e in hl.ENGINES}
    if have["claude"]:
        return "1"
    if have["codex"]:
        return "2"
    return "1"


def simple_interview(kind: str, reader: Callable[[str], str] = input, root_fixed: Optional[Path] = None,
                     runner: Optional[str] = None, title: Optional[str] = None,
                     workflow: Optional[bool] = None) -> dict:
    """기본 셋업: 질문 4개(프로젝트 이름 · 엔진 · 작업자 실행 방식 · 작업 흐름) + 요약 확인.
    --runner · --workflow/--no-workflow 를 주면 그 질문은 묻지 않는다. 호칭은 묻지 않고 기본값(--title · --detail 로 바꾼다).
    Orca 같은 선택 도구는 사용자가 고를 때만 깔린다(묻지 않고 켜지 않는다)."""
    cfg = hl.default_config()
    p = cfg["project"]
    n = 2 + (runner is None) + (workflow is None)
    say(f"\n하네스 셋업 (질문 {n}개. 전부 고르려면 --detail)")
    p["name"] = ask("1) 프로젝트 이름", caller_base().name, reader)
    p["slug"] = default_slug(p["name"])
    choice = ask("2) 엔진 (1) Claude (2) Codex (3) 둘 다", detect_engines(kind), reader)
    cfg["engines"] = {"1": ["claude"], "2": ["codex"], "3": ["claude", "codex"]}.get(choice, ["claude"])
    i = 3
    if runner is None:
        runner = runners.from_choice(ask(f"{i}) {runners.QUESTION}", "1", reader))
        i += 1
    cfg["runner"] = runner
    if workflow is None:
        workflow = ask_yes(f"{i}) 작업 흐름(작업 번호 · 저장소별 워크트리 · git 으로 계산하는 상태)을 쓸까요",
                           hl.PACK_DEFAULT["workflow"], reader)
    cfg["packs"] = ["workflow"] if workflow else []
    p["owner_title"] = title or hl.DEFAULT_OWNER_TITLE
    root = root_fixed or project_root(None, cfg)
    engines = " · ".join({"claude": "Claude", "codex": "Codex"}[e] for e in cfg["engines"])
    orca = f"작업자 {runners.label(cfg)} · 작업 흐름 {'씀' if workflow else '안 씀'}"
    problem = root_problem(root, cfg, False)
    if problem:
        say("\n" + problem)
        ans = ask("  다른 경로 입력 · Enter 또는 n = 그만", "", reader)
    else:
        ans = ask(f"\n여기에 만듭니다: {root} · 엔진 {engines} · 저장소는 나중에(add-repo) · {orca}"
                  f" · 호칭 {p['owner_title']}{'' if title else '(바꾸려면 --title)'}\n"
                  "  Enter = 진행 · 다른 경로를 치면 그 폴더에 · n = 그만", "", reader)
    while True:
        if ans.lower() in ("n", "no", "아니오") or (problem and not ans):
            raise hl.ConfigError("그만뒀다. 빈 폴더에서 다시 실행하거나 다른 경로를 준다. 전부 고르려면 --detail")
        if not ans:
            break
        root = Path(os.path.expanduser(ans)).resolve()
        problem = root_problem(root, cfg, False)
        if not problem:
            break
        say(problem)
        ans = ask("  다른 경로 입력 · Enter 또는 n = 그만", "", reader)
    cfg["_root"] = str(root)
    return hl.normalize(cfg)


def interview(kind: str, reader: Callable[[str], str] = input, default_root=None, title: Optional[str] = None) -> dict:
    """네 묶음 질문 → 설정 dict. title 은 호칭 질문의 기본값(--title)."""
    cfg = hl.default_config()
    say("\n[1/4] 프로젝트")
    p = cfg["project"]
    p["name"] = ask("프로젝트 이름", caller_base().name, reader)
    p["slug"] = ask("영문 슬러그(플러그인 이름, 소문자 · 숫자 · -)", default_slug(p["name"]), reader)
    if default_root is not None:
        here = default_root(None, {"project": {"slug": p["slug"]}, "harness_repo": {"dir": "orchestrator"}})
        cfg["_root"] = ask(f"여기에 만듭니다: {here}  (Enter = 그대로, 다른 경로 입력)", str(here), reader)
    p["github_org"] = ask("GitHub 조직 또는 사용자(없으면 빈칸: 로컬만 만든다)", "", reader)
    p["owner_title"] = ask("결정권자를 부르는 호칭", title or hl.DEFAULT_OWNER_TITLE, reader)
    h = cfg["harness_repo"]
    how = ask("하네스 저장소: (1) 새로 만들기 (2) 이 컴퓨터에 있는 폴더 연결", "1", reader)
    if how == "2":
        h["source"] = "local"
        path = ask_git_path("  연결할 하네스 폴더 경로", reader)
        if not (path / ".git").exists():
            if ask_yes(f"  git 저장소가 아니다: {path}. 여기에 git init 해서 하네스로 쓸까요(원래 있던 파일은 커밋에 넣지 않는다)", True, reader):
                h["git_init"] = True
            else:
                raise hl.ConfigError("하네스로 연결할 폴더가 git 저장소가 아니다. git init 하거나 새로 만들기를 고른다")
        h["path"] = str(path)
        h["base_branch"] = ask("  기준 브랜치", hl.detect_base_branch(path) or "main", reader)
    else:
        h["dir"] = ask("  하네스 저장소 폴더 이름", "orchestrator", reader)
        h["base_branch"] = ask("  기준 브랜치", "main", reader)

    say("\n[2/4] 저장소 (빈 키를 넣으면 끝). 저장소마다 새로 만들기 · 원격에서 받기 · 이 컴퓨터의 폴더 연결 중 고른다")
    say("  첫 저장소 키를 빈칸으로 두면 저장소 없이 셋업하고, 나중에 `bootstrap.py add-repo` 로 더한다")
    while True:
        r = ask_repo(p.get("github_org") or "", len(cfg["repos"]) + 1, reader)
        if r is None:
            if not cfg["repos"]:
                say("  저장소는 나중에 추가한다(add-repo)")
            break
        cfg["repos"].append(r)

    say("\n[3/4] 규칙 스킬: 절차 뼈대 5개는 항상 넣는다")
    cfg["skills"]["fill"] = [s for s in hl.FILL_SKILLS if ask_yes(f"  채울 자리 틀 {s} 넣기", True, reader)]

    say("\n[4/4] 엔진 · 작업자 실행 방식")
    choice = ask("엔진 (1) Claude (2) Codex (3) 둘 다", "1", reader)
    cfg["engines"] = {"1": ["claude"], "2": ["codex"], "3": ["claude", "codex"]}.get(choice, ["claude"])
    note = " (Windows: Orca 앱은 공식 지원, 이 하네스의 Orca 스크립트는 Git Bash 필요)" if kind == "windows" else ""
    cfg["runner"] = runners.from_choice(ask(runners.QUESTION + note, "1", reader))
    say("선택 묶음 (고른 것만 붙는다. Enter = 넣지 않음)")
    cfg["packs"] = [k for k in hl.PACKS if (k != "codex-desktop" or "codex" in cfg["engines"])
                    and ask_yes(f"  {k}: {hl.PACK_INFO[k]}", hl.PACK_DEFAULT.get(k, False), reader, on_eof=False)]
    return hl.normalize(cfg)
