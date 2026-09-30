#!/usr/bin/env python3
"""생성기: harness.json 한 장 → 하네스 파일 전부.

사용: python3 harness/scripts/generate.py [--root <하네스 폴더>] [--dry-run]
- 생성기가 주인인 파일(AGENTS.md · 작업자 · manifest · 훅 설정 · 플러그인 스크립트)은 설정대로 다시 맞춘다.
- CLAUDE.md · core.md 는 관리 블록만 바꾸고 블록 밖(사람이 적은 곳)은 둔다.
- 규칙 스킬 본문 · README.md · 상황판.md · docs/기록/README.md 는 없을 때만 만든다(사람이 채운 것을 덮지 않는다).
  규칙 스킬 안에서 설정으로 만든 표(저장소 지도 · 배포 · 검사 명령 · 맡기는 방법)만 관리 블록으로 다시 맞춘다.
- 전에 만들었는데 설정이 바뀌어 필요 없어진 관리 파일은 지운다(.harness-manifest.json 으로 추적).
두 번 돌려도 결과가 같다(내용이 같으면 파일을 건드리지 않는다).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402

ENGINE_ROOT = HERE.parent            # harness/
TEMPLATE_ROOT = ENGINE_ROOT.parent   # harness/ 를 품은 저장소 루트
SKELETON = ENGINE_ROOT / "skeleton"
MANIFEST = ".harness-manifest.json"
PLUGIN_VERSION = "0.1.0"
ROOT_ENTRY_FILES = ("bootstrap.sh", "bootstrap.ps1")  # 하네스 루트에 싣는 진입 래퍼. update 도 같은 목록을 쓴다


# ---------------------------------------------------------------- 문맥

def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|")


def deploy_text(r: dict) -> str:
    d = r.get("deploy") or {}
    if not d:
        return "배포 없음"
    return " · ".join(f"{b} = {m}" for b, m in d.items())


def tracker_text(cfg: dict, labels: str) -> Dict[str, str]:
    """작업 추적처를 가리키는 문장. 원격이 없으면 이슈를 쓸 수 없으니 `상황판.md` 를 가리킨다.
    없는 저장소 이름(`<폴더> 저장소`)을 만들어 `gh issue list -R` 에 넣지 않는다."""
    h, py = cfg["harness_repo"], cfg["platform"]["python"]
    remote = h.get("remote")
    if remote:
        return {
            "tracker": f"GitHub Issues `{remote}`",
            "tracker_labels": f"GitHub Issues `{remote}` (라벨 {labels})",
            "tracker_row": f"| GitHub Issues `{remote}` | **작업 추적의 주인.** 라벨 {labels} |",
            "board_row": "| `상황판.md` | 지금 집중하는 것 한 장. 목록은 이슈가 갖는다 |",
            "board_intro": f"지금 집중하는 것 한 장. 목록은 GitHub Issues `{remote}` 가 갖고, 여기는 가리키기만 한다.",
            "session_open": f"`상황판.md` 와 열린 이슈(`gh issue list -R {remote}`)부터 본다",
            "record_from": f"이슈(`{remote}`)에서 링크한다",
            "bug_where": f"이슈(`{remote}`)에 등록한다",
            "status_step": "일의 상태는 이슈에 남긴다: 시작하면 `진행중`, 결정이 필요하면 `결정대기`, 끝나면 커밋을 적고 닫는다",
        }
    note = "원격이 없어 아직 이슈를 쓰지 않는다"
    return {
        "tracker": f"하네스 `상황판.md` ({note})",
        "tracker_labels": f"하네스 `상황판.md` ({note})",
        "tracker_row": f"| GitHub Issues | **작업 추적의 주인이 될 자리.** 원격이 없어 아직 쓸 수 없다. 원격을 만든 뒤 "
                       f"`{py} harness/scripts/bootstrap.py run --create-github` 을 돌리면 라벨({labels})까지 만든다. "
                       "그때까지는 `상황판.md` 를 쓴다 |",
        "board_row": "| `상황판.md` | **지금은 작업 추적의 주인.** 지금 집중하는 것과 남은 준비 한 장. 원격을 만들면 목록은 이슈가 갖는다 |",
        "board_intro": "지금 집중하는 것과 남은 준비 한 장. 원격이 없어 지금은 이 파일이 작업 추적의 주인이다. 원격을 만들면 목록은 이슈가 갖는다.",
        "session_open": f"`상황판.md` 부터 본다({note})",
        "record_from": "`상황판.md` 에서 링크한다",
        "bug_where": "`상황판.md` 「결정 대기」에 적는다(원격을 만들면 이슈로 옮긴다)",
        "status_step": "일의 상태는 `상황판.md` 에 남긴다(원격을 만들면 이슈와 라벨 `진행중` · `결정대기` 로 옮긴다)",
    }


def context(cfg: dict) -> Dict[str, str]:
    p, h = cfg["project"], cfg["harness_repo"]
    py = cfg["platform"]["python"]  # 안내 명령의 파이썬(mac · Linux python3, Windows py -3 등)
    owner = p["owner_title"]
    rows = ["| 폴더 | 원격 | 기준 브랜치 | 그 브랜치에 들어가면 | 작업자 |", "| --- | --- | --- | --- | --- |",
            f"| `.` ({h['dir']}) | {h.get('remote') or '-'} | `{h['base_branch']}` | 배포 없음. 하네스 규칙과 실행 장치 | 오케스트레이터 |"]
    for r in cfg["repos"]:
        rows.append(f"| `{hl.repo_ref(r)}` | {r.get('remote') or r.get('url') or '로컬만'} | `{r['base_branch']}` | {md_escape(deploy_text(r))} | `{r['key']}-worker` |")
    if not cfg["repos"]:
        rows.append("")
        rows.append(f"> **코드 저장소가 아직 없다.** 나중에 하네스 폴더에서 `{py} harness/scripts/bootstrap.py add-repo` 로 더한다"
                    "(새로 만들기 · 원격에서 받기 · 이 컴퓨터의 폴더 연결). 더하면 이 표와 작업자가 생긴다.")
    deploy_rows = ["| 저장소 | 브랜치 | 들어가면 | 승인 |", "| --- | --- | --- | --- |",
                   f"| {h['dir']} (하네스) | `{h['base_branch']}` | 규칙 반영 | 규칙 파일이 바뀌었으면 {owner}께 묻는다(훅이 묻는다) |"]
    for r in cfg["repos"]:
        branches = list(dict.fromkeys([r["base_branch"], *(r.get("deploy") or {}).keys(), *r.get("ask_on_push", [])]))
        for b in branches:
            ask = b in r.get("ask_on_push", [])
            meaning = (r.get("deploy") or {}).get(b, "")
            rows_ok = f"**{owner}께 매번 묻는다** (훅이 묻는다)" if ask else "상시"
            deploy_rows.append(f"| {r['dir']} | `{b}` | {md_escape(meaning) or '-'} | {rows_ok} |")
    checks = ["| 저장소 | 검사 명령 |", "| --- | --- |"]
    for r in cfg["repos"]:
        cmds = " · ".join(f"`{c}`" for c in r["checks"]) or "아직 없다(`harness.json` repos[].checks 에 적는다)"
        checks.append(f"| {r['dir']} | {cmds} |")
    if hl.orca_on(cfg):
        cleanup = ("Orca 작업자는 작업자 출력의 `bash scripts/orca-finish-worker.sh <dispatch> <워크트리>`, "
                   f"Orca 밖 작업자는 `{py} harness/scripts/finish_worker.py <워크트리>`. "
                   f"오래 남은 작업 공간은 `{py} harness/scripts/cleanup_worktrees.py` 로 미리 보고 `--apply`.")
    else:
        cleanup = (f"`{py} harness/scripts/finish_worker.py <워크트리>`. "
                   f"오래 남은 작업 공간은 `{py} harness/scripts/cleanup_worktrees.py` 로 미리 보고 `--apply`(원격에 다 들어간 것만 지운다).")
    labels = " · ".join(f"`{x}`" for x in cfg["labels"]) + " · `repo:<키>`"
    ctx = {
        "project": p["name"], "owner": owner, "slug": p["slug"], "labels": labels,
        "repo_table": "\n".join(rows), "deploy_table": "\n".join(deploy_rows), "checks_table": "\n".join(checks),
        "cleanup_line": cleanup,
        "dispatch_table": dispatch_table(cfg), "harness_dir": h["dir"], "py": py,
        "engine_cmds": " 또는 ".join(f"`{e}`" for e in cfg["engines"]),
    }
    ctx.update(tracker_text(cfg, labels))
    return ctx


def dispatch_table(cfg: dict) -> str:
    rows = ["| 어디서 | 코드를 고치는 과제 | 조사 · 검토 |", "| --- | --- | --- |"]
    engines = cfg["engines"]
    if hl.orca_on(cfg):
        agents = " · ".join(f"`bash scripts/orca-worker.sh --agent {e} <키> <작업이름> @브리프파일`" for e in engines)
        extra = " 또는 Codex 앱에서 저장 프로젝트를 골라 워크트리로 새 작업" if "codex" in engines else ""
        rows.append(f"| Orca 가 켜진 기기 | {agents}{extra}. 코드 작업자를 서브에이전트로 띄우지 않는다 | 서브에이전트 (`researcher` · `reviewer`) |")
        rows.append("| Orca 가 없는 곳(클라우드 · 폰) | 서브에이전트 작업자 (`<키>-worker`, 워크트리 격리) | 서브에이전트 |")
    else:
        parts = []
        if "claude" in engines:
            parts.append("Claude: 서브에이전트 `<키>-worker`(워크트리 격리) 또는 해당 저장소에서 `claude --worktree <작업이름>`")
        if "codex" in engines:
            parts.append("Codex: 앱에서 해당 저장 프로젝트를 골라 새 작업을 워크트리로, 또는 `.codex/agents/<키>-worker` 서브에이전트")
        rows.append(f"| 어디서나 | {' / '.join(parts)} | 서브에이전트 (`researcher` · `reviewer`) |")
    return "\n".join(rows)


def render(text: str, ctx: Dict[str, str]) -> str:
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v)
    return text


# ------------------------------------------------- 옛 생성물에 관리 블록 심기 (이미 설치한 하네스 고치기)
#
# 이 장치가 있기 전 생성기는 규칙 스킬 · 상황판 · 기록 README 를 **처음 한 번만** 만들고(seed) 다시 손대지 않았다.
# 그래서 이미 설치한 하네스는 설정에서 만든 표(저장소 지도 · 배포 · 검사 명령 · 맡기는 방법 · 기록의 자리)가
# 영구히 낡은 채 남는다. 마커가 없으니 `replace_blocks` 도 아무것도 하지 못한다.
#
# 판정 기준은 **옛 생성기 출력을 글자 그대로 재현해 대조**하는 것 하나다. 아래 `LEGACY_*` 는 옛 생성기가
# 그 자리에 넣었던 글의 원본이고, `legacy_contexts` 가 그것을 채울 값을 만든다. 파일 안에 그 글이 딱 한 번
# 그대로 있을 때만 마커로 감싸고 새 내용으로 바꾼다(바꾸기 전 백업). 한 글자라도 다르면 사람이 손댄 것으로
# 보고 건드리지 않고 `doctor` 에 「직접 고쳐야 한다」로 알린다. 애매하면 안 건드리는 쪽이다.
#
# 주의(퇴행 함정): 아래 `git-rules-repos` · `dispatch-table` · `deploy-table` · `deploy-cleanup` 4개는 옛 글 후보가
# `{{repo_table}}` 처럼 **지금 생성기가 만드는 표 그 자체**다(옛 생성기도 같은 함수를 썼기 때문에 맞는다).
# 즉 이 4개는 생성기를 거울로 쓴다. 그 표 함수가 틀어지면 옛 글 후보도 똑같이 틀어져
# `tests/test_migrate_blocks.py` 의 검사가 퇴행을 잡지 못하고, 옛 설치본에서 이관이 조용히 멈춘다
# (블록을 못 심고 「직접 고쳐야 한다」로만 넘어가 사용자는 표가 낡은 줄 모른다).
# 그래서 `LEGACY_*` · `legacy_contexts` 를 건드릴 때는 검사 통과만으로 끝내지 말고
# **진짜 옛 템플릿(커밋 `f51adf7`) 설치본과 대조해 다시 재라.**
# 2026-09-30 검토: 실제 `f51adf7` 설치본에 update 를 돌려 8개 블록이 모두 심기는 것을 확인했다.
LEGACY_SKILL_BLOCKS: Dict[str, Dict[str, List[str]]] = {
    "git-rules": {"git-rules-repos": ["{{repo_table}}\n"]},
    "deploy": {"deploy-table": ["{{deploy_table}}\n"],
               "deploy-cleanup": ["① 작업자 정리(finish): {{cleanup_line}}\n"]},
    "code-convention": {"checks-table": ["{{checks_table}}\n"]},
    "task-brief": {"dispatch-table": ["{{dispatch_table}}\n"]},
    "work-method": {"work-method-records": [
        "| 무엇 | 어디 |\n"
        "| --- | --- |\n"
        "| 할 일 · 진행 · 결정 대기 · 끝난 일 | GitHub Issues `{{issues_repo}}` (라벨 {{labels}}) |\n"
        "| 결정의 근거(명령과 출력, 파일과 줄) | 하네스 `docs/기록/YYYY-MM-DD-제목.md`, 이슈에서 링크 |\n"
        "| 지금 집중하는 것 한 장 | 하네스 `상황판.md` (가리키기만 한다) |\n"]},
    "absolute-rules": {"absolute-rules-tracker": [
        "- R6. 버그 · 추가요건은 기준 문서에 적지 않는다. 이슈({{issues_repo}})에 등록한다.\n"]},
}
LEGACY_BOARD: Dict[str, List[str]] = {
    "board-intro": ["> 지금 집중하는 것 한 장. 목록은 GitHub Issues `{{issues_repo}}` 가 갖고, 여기는 가리키기만 한다.\n"]}
LEGACY_RECORDS: Dict[str, List[str]] = {
    "records-link": ["- 이슈(`{{issues_repo}}`)에서 링크한다. 고쳐 쓰지 않고 쌓는다(틀린 것은 새 기록에서 정정한다).\n"]}
# 사람이 읽을 절 이름. doctor 가 「어느 파일 어느 절이 낡았나」를 적을 때 쓴다.
BLOCK_TITLES = {
    hl.LINE_BLOCK_NAME: "무시 목록(생성기 몫)",
    "git-rules-repos": "저장소별 기준 브랜치 표", "deploy-table": "저장소별 반영 · 승인 표",
    "deploy-cleanup": "작업자 정리 명령", "checks-table": "커밋 전 필수 검사 표",
    "dispatch-table": "맡기는 방법 표", "work-method-records": "기록의 자리 표",
    "absolute-rules-tracker": "R6 버그 · 추가요건 기록처", "board-intro": "머리글(작업 추적의 주인)",
    "records-link": "기록을 어디서 링크하나", "claude-md": "CLAUDE.md 본문", "core": "핵심 요약 본문",
}


def legacy_checks_table(cfg: dict) -> str:
    """이 장치가 있기 전 생성기의 검사 명령 표(빈 칸이 `<!-- 채울 자리 -->` 였다). 지금 것과 달라서 따로 재현한다."""
    rows = ["| 저장소 | 검사 명령 |", "| --- | --- |"]
    for r in cfg["repos"]:
        rows.append(f"| {r['dir']} | {' · '.join(f'`{c}`' for c in r['checks']) or '<!-- 채울 자리 -->'} |")
    return "\n".join(rows)


def legacy_contexts(cfg: dict, ctx: Dict[str, str]) -> List[Dict[str, str]]:
    """옛 글을 채울 값. 지금 설정과 **저장소가 0개였던 때**를 함께 본다: 옛 생성기는 규칙 스킬을 설치 때
    한 번만 만들었으므로 파일에 남은 표는 설치 시점 설정이다(그 뒤 `add-repo` 로 저장소가 늘어도 그대로였다).
    그 밖의 시점(엔진 · Orca · 프로젝트 이름을 바꾼 뒤)은 재현하지 않는다 → 안 건드리고 알린다."""
    out = []
    for c in (cfg, {**cfg, "repos": []}):
        base = dict(ctx) if c is cfg else dict(context(c))
        h = c["harness_repo"]
        base["issues_repo"] = h.get("remote") or f"{h['dir']} 저장소"
        base["checks_table"] = legacy_checks_table(c)
        out.append(base)
    return out


def legacy_blocks(templates: Dict[str, List[str]], ctxs: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """블록 이름 → 옛 생성기가 그 자리에 넣었을 후보 글 목록."""
    return {name: [render(t, c) for t in tpls for c in ctxs] for name, tpls in templates.items()}


def block_where(pairs: List[Tuple[str, str]]) -> str:
    """(파일, 블록) 목록을 사람이 읽는 「파일 「절 이름」」로."""
    return " · ".join(f"{rel} 「{BLOCK_TITLES.get(name, name)}」" for rel, name in pairs)


def block_lines(w: hl.Writer) -> List[str]:
    """생성 뒤 사람이 알아야 할 관리 블록 소식: 심은 것 · 백업 · 직접 고쳐야 하는 것."""
    out = [f"  (관리 블록 심음) {block_where([p])}" for p in w.inserted]
    out += [f"  (백업) {rel}" for rel in w.backups]
    out += [f"  (직접 고쳐야 한다) {block_where([p])}: 사람이 고친 자리라 건드리지 않았다. 설정이 바뀌었으면 직접 맞춘다"
            for p in w.stale]
    return out


# ---------------------------------------------------------------- 문서

def claude_md(cfg: dict, ctx: Dict[str, str]) -> str:
    p, h = cfg["project"], cfg["harness_repo"]
    owner, slug = p["owner_title"], p["slug"]
    engines = " · ".join({"claude": "Claude", "codex": "Codex"}[e] for e in cfg["engines"])
    worker_rule = (f"- 코드를 고치는 작업자는 해당 저장소의 **격리 워크트리 세션**에서만 일한다(Orca `scripts/orca-worker.sh`"
                   + (" 또는 Codex 앱 저장 프로젝트 워크트리" if hl.has(cfg, "codex") else "") + ").\n"
                   if hl.orca_on(cfg) else
                   "- 코드를 고치는 작업자는 해당 저장소의 **격리 워크트리**에서만 일한다(서브에이전트 워크트리 격리 · `claude --worktree` · Codex 앱 워크트리).\n")
    body = f"""# {p['name']} 오케스트레이터

> 셋업 · 점검: 「셋업해」라고 하면 `setup` 스킬(`plugins/{slug}/skills/setup/SKILL.md`)을 따른다. 설정 원본은 `harness.json` 하나다.

이 저장소({h['dir']})는 {p['name']} **하네스**다. 여기서 뜬 에이전트({engines})는 **오케스트레이터**다.
실행 엔진과 무관하게 같은 규칙과 작업 방식을 쓴다. 대화 기록 · 사용량 · 세션 정리는 실제로 실행한 엔진에만 속한다. 한 세션에서 다른 엔진을 추가로 띄워 이중 사용하지 않는다.

## 오케스트레이터가 하는 일 / 안 하는 일

| 한다 | 안 한다 |
| --- | --- |
| {owner} 지시를 과제로 쪼갠다 | 코드를 직접 고친다 |
| 과제를 작업자(플러그인 `{slug}` 의 agents)에게 맡기고 결론만 받는다 | 코드 · 문서를 통째로 읽는다 |
| 검토원 판정을 받아 push · 배포한다 | 작업자가 본 내용을 다시 읽는다 (의심될 때만) |
| 진행과 결정을 기록에 남기고 `상황판.md` 에 지금 집중할 것을 가리킨다 | 대화 기억에 기대 판단한다 |

**머리를 비워 두는 것이 역할이다.** 사실이 필요하면 직접 읽지 말고 `researcher`(조사원)나 해당 저장소 작업자에게 맡긴다.

## 저장소 지도

{ctx['repo_table']}

## 역할과 작업 공간 경계
{worker_rule}- 하네스 자체 작업(규칙 · 훅 · 기록)도 하네스 저장소의 격리 워크트리 또는 현재 세션의 서브에이전트에서 한다.
- 공유 체크아웃에서 코드나 문서를 직접 고치지 않는다. 역할 · 저장소 · 워크트리 중 하나라도 맞지 않으면 시작하지 않는다.

## 하네스 구성

| 위치 | 역할 |
| --- | --- |
| `harness.json` | **설정 원본 한 장.** 저장소 목록 · 엔진 · Orca 여부. 바꾼 뒤 `{cfg['platform']['python']} harness/scripts/generate.py`. 저장소 추가는 `{cfg['platform']['python']} harness/scripts/bootstrap.py add-repo` |
| `CLAUDE.md` · `AGENTS.md` | 공통 지침과 Codex 진입점. `AGENTS.md` 는 이 파일을 읽게만 한다 |
| 플러그인 `{slug}` (`plugins/{slug}/`) | 모든 저장소와 엔진이 같이 쓰는 스킬 · 작업자 · 안전 훅(push 가드) |
| `harness/hooks/` | 이 저장소 전용 훅: 규칙 파일 보호 · 원격 하네스 동기화 |
| `harness/scripts/` | 셋업(`bootstrap.py`) · 생성기(`generate.py`) · 작업자 정리(`finish_worker.py`) · 작업 공간 정리(`cleanup_worktrees.py`) |
{ctx['tracker_row']}
| `docs/기록/` | 결정의 근거 원본. {ctx['record_from']} |
{ctx['board_row']}
| `README.md` | 설치한 사람용 안내 한 장. 「하고 싶은 것 → 이렇게 말한다」 |
{"| `scripts/orca-*.sh` | Orca 작업자 띄우기 · 중간 지시 · 정리 |" + chr(10) if hl.orca_on(cfg) else ""}
## 일하는 순서

1. 세션을 열면 {ctx['session_open']}.
2. 지시를 과제로 쪼갠다. **과제 1 = 저장소 1.** 같은 저장소에 작업자를 여럿 둘 때는 과제마다 워크트리 · 브랜치를 따로 쓰고 파일 범위를 나눠 적는다.
3. 작업자에게 넘기는 글은 `task-brief` 스킬 형식을 따른다. 맡기는 방법:

{ctx['dispatch_table']}

4. 작업자 보고(바꾼 파일 · 검증 결과 · 한 줄 요약)를 받으면 `reviewer`(검토원)에게 넘긴다. 오케스트레이터는 판정과 근거만 본다.
5. push · 배포는 `deploy` 스킬을 따른다. 작업자는 push 하지 않는다.
6. {ctx['status_step']}.
7. 근거가 붙은 보고(명령과 출력, 파일과 줄)는 `docs/기록/YYYY-MM-DD-제목.md` 로 남기고 {ctx['record_from']}.
8. push 가 끝나면 `deploy` 스킬 「작업 공간 · 앱 세션 정리」대로 정리한다: 작업자 정리(finish) → Codex 자동 보관 → Claude 앱 세션은 브라우저 도구로 **링크가 `/code/<bridgeSessionId>` 로 맞고 오프라인인 것만 보관** → 보관한 제목을 보고. 작업 중 · 응답 대기 세션은 건드리지 않는다.

## {owner}께 보고

짧게 · 표와 굵은 글씨. 결정이 필요한 것은 선택지로 묻는다. 값에는 「누가 · 언제 · 어떻게 쟀나」를 붙인다. 추정은 추정이라고 쓴다.
"""
    human = f"""
## 프로젝트 메모 (사람이 채운다. 생성기가 덮지 않는다)
<!-- 채울 자리: 이 프로젝트만의 결정 · 주의점. 규칙은 플러그인 스킬에 둔다. -->
"""
    return hl.block("claude-md", body) + human


def agents_md(cfg: dict) -> str:
    slug = cfg["project"]["slug"]
    return f"""# {cfg['project']['name']} 에이전트 진입점

> 셋업 · 점검: 「셋업해」라고 하면 `plugins/{slug}/skills/setup/SKILL.md` 를 읽고 따른다.

이 파일은 Codex 용 진입점이다. 규칙을 따로 복제하지 않는다.

1. 같은 폴더의 `CLAUDE.md` 를 처음부터 끝까지 읽고 공통 원본 지침으로 따른다. 파일명 안의 `Claude` 는 제품명일 뿐이고 역할 · 검증 · git · 배포 규칙은 Codex 에도 같다.
2. 작업에 해당하는 `.claude/rules/` 파일이 있으면 함께 읽는다.
3. 설치된 `{slug}` 플러그인의 관련 스킬을 쓴다. 플러그인이 없으면 작업 전에 설치 누락을 보고한다.
4. 한 세션에서 다른 엔진을 추가로 실행해 이중으로 사용량을 쓰지 않는다.

작업자 이름이 필요하면 `.codex/agents/` 정의를 쓴다.
"""


def core_md(cfg: dict, ctx: Dict[str, str]) -> str:
    p = cfg["project"]
    owner, slug = p["owner_title"], p["slug"]
    ask = [f"`{r['dir']}` `{b}`" for r in cfg["repos"] for b in r["ask_on_push"]]
    ask_line = (f"- 매번 {owner}께 묻는 push: {' · '.join(ask)} (훅이 묻는다)." if ask else f"- 매번 묻는 push 는 설정에 없다(`harness.json` repos[].ask_on_push).")
    engine = ("- 작업자는 Orca 로 해당 저장소 워크트리에 띄운다(`scripts/orca-worker.sh`). 도구가 실패하면 조용히 서브에이전트로 바꾸지 않고 알린다."
              if hl.orca_on(cfg) else
              "- 작업자는 해당 저장소의 격리 워크트리(서브에이전트 · `claude --worktree` · Codex 앱 워크트리)에서만 일한다.")
    skills = ["start"] + [s for s in hl.PROCEDURE_SKILLS] + list(cfg["skills"]["fill"])
    when = {"start": "설치 직후 · 「시작해」 · 「뭐부터 해」 · 「다음 뭐야」 (지금 할 것 한 개를 고른다)",
            "absolute-rules": "모든 작업 시작 전 · 새 지시가 기준을 건드릴 때", "work-method": "재거나 판정 · 보고하기 전",
            "git-rules": "커밋 · push · 브랜치 작업 전", "deploy": "push 직전", "task-brief": "작업자에게 과제를 넘기기 직전",
            "code-convention": "코드 수정 · 커밋 전", "security-privacy": "API · 권한 · 개인정보를 건드리기 전",
            "operations": "환경변수 · 배포 설정 · DB 변경 전 · 장애", "design": "화면 · 색 · 아이콘 · 이미지 작업 전"}
    table = "\n".join(f"| `{s}` | {when[s]} |" for s in skills)
    body = f"""# {p['name']} 핵심 규칙 ({slug} 플러그인이 세션마다 넣는 요약)
요약이다. 전문은 플러그인 스킬(`{slug}:<이름>`)이 주인이고, 갈리면 스킬이 이긴다.

## 실행 엔진과 세션
{engine}
- 한 세션에서 두 엔진을 이중 실행하지 않는다. 대화 기록 · 사용량 · 세션 보관은 실행한 엔진별로 분리한다.

## 거짓 금지 · 근거
- 확인하지 않은 것을 됐다고 쓰지 않는다. 커밋 ≠ push ≠ 배포 ≠ 동작.
- 값에는 누가 · 언제 · 어떻게 쟀나를 붙인다. 추정은 추정이라고 쓴다. 못 쟀으면 「미확인」.
- 기준(한도 · 요금 · 권한 · 완료 기준)은 문서 먼저 → 코드. 바꾸려면 {owner} 승인.

## push · 배포 (`deploy` 스킬)
- push 는 오케스트레이터만 한다. 강제 push 금지(훅이 막는다).
{ask_line}
- push 전 `git log --oneline origin/<기준>..<밀 SHA>`: 내 SHA 가 없거나 · 많거나 · 비면 멈춘다.

## git (`git-rules` 스킬)
- 작업은 공유 체크아웃이 아니라 과제마다 워크트리에서. add 는 경로를 명시한다.
- 커밋 메시지: `type(scope): 요약` (feat · fix · docs · chore · refactor · test).

## 기록
- 작업 기록은 {ctx['tracker']}. 근거는 하네스 `docs/기록/`.

## 어떤 스킬을 언제 읽나
| 스킬 | 언제 |
| --- | --- |
{table}
"""
    human = """
## 프로젝트 핵심 (채울 자리. 생성기가 덮지 않는다)
<!-- 세션마다 꼭 들어가야 할 이 프로젝트의 규칙 몇 줄 -->
"""
    return hl.block("core", body) + human


def worker_md(cfg: dict, r: dict) -> str:
    p, slug = cfg["project"], cfg["project"]["slug"]
    checks = " · ".join(f"`{c}`" for c in r["checks"]) or "저장소의 lint · 타입 검사 · 테스트 전체(명령은 harness.json 에 채운다)"
    desc = r.get("description") or r["dir"]
    return f"""---
name: {r['key']}-worker
description: {p['name']} {r['dir']} 저장소({r.get('remote') or r.get('url') or '로컬'}, {md_escape(desc)}) 과제를 맡는 작업자. 오케스트레이터가 {r['dir']} 저장소 과제 하나를 줄 때 쓴다.
---

너는 {p['name']} **{r['dir']} 저장소 작업자**다. 받은 과제 하나만 하고, 결론만 돌려준다.

## 자리
- 저장소: 오케스트레이터에서 부르면 `{hl.repo_ref(r)}`, 그 저장소 세션에서 부르면 현재 저장소
- 기준 브랜치: `origin/{r['base_branch']}` ({deploy_text(r)})
- 시작: 이미 격리 워크트리 안이면 그대로 쓴다. 아니면 `git -C <저장소> fetch origin && git -C <저장소> worktree add <저장소 부모>/.work/{r['key']}-<과제-슬러그> -b <과제-슬러그> origin/{r['base_branch']}` 로 만들고 그 안에서만 일한다.

## 먼저 읽을 것
- 작업 공간의 `CLAUDE.md` · `AGENTS.md` 와 `.claude/rules/` (있으면).
- `{slug}` 플러그인 스킬 중 이번 과제에 해당하는 것만. `absolute-rules` 는 항상.
- 그 밖에는 과제에 필요한 파일만 연다. 저장소 전체를 훑지 않는다.

## 검증
{checks}

## 금지
- `git push` · 머지 · 배포. 커밋까지만 하고 오케스트레이터에게 넘긴다.
- 다른 저장소 수정 · 받은 과제 밖의 수정(필요해 보이면 보고에 적는다).
- `git add -A` · `git commit -a`. 경로를 명시해서 add 한다.

## 보고 (마지막 메시지 형식)
| 항목 | 내용 |
| --- | --- |
| 한 줄 요약 | |
| 브랜치 · 커밋 | |
| 바꾼 파일 | |
| 검증 | 실행한 명령과 결과 |
| 남은 의문 | 없으면 없음 |

추정은 추정이라고 쓴다. 확인하지 않은 것을 됐다고 쓰지 않는다.
"""


def researcher_md(cfg: dict) -> str:
    p, slug = cfg["project"], cfg["project"]["slug"]
    return f"""---
name: researcher
description: {p['name']} 저장소 · 문서를 읽고 사실만 조사해 돌려주는 조사원. 코드를 고치지 않는다. 오케스트레이터가 판단에 사실이 필요할 때 쓴다.
tools: Read, Grep, Glob, Bash
---

너는 {p['name']} **조사원**이다. 질문 하나에 사실로 답하고, 아무것도 고치지 않는다.

## 규칙
- 작업 규칙의 주인은 `{slug}` 플러그인 스킬이다. 규칙을 묻는 질문이면 거기부터 본다. 해당 저장소에 `.claude/rules/` 가 있으면 그것도 함께 본다(없는 하네스도 있다).
- 파일 수정 · 커밋 · push 하지 않는다. Bash 는 읽기 명령(`git log` · `git show` · `grep` · `ls` 등)에만 쓴다.
- 답마다 근거를 붙인다: 파일 경로와 줄, 또는 실행한 명령과 출력.
- 확인 못 한 것은 「확인 못 함」, 추정은 「추정」이라고 쓴다. 사실의 주인은 코드 · DB · git 이다.

## 돌려줄 형식
| 질문 | 답 | 근거 |
| --- | --- | --- |
"""


def reviewer_md(cfg: dict) -> str:
    p, slug, owner = cfg["project"], cfg["project"]["slug"], cfg["project"]["owner_title"]
    return f"""---
name: reviewer
description: 작업자가 커밋한 변경을 push 전에 검토하는 검토원. 규칙 위반 · 범위 밖 수정 · 검증 누락을 찾고 판정만 돌려준다. 코드를 고치지 않는다. 오케스트레이터가 deploy 스킬 1번 단계에서 쓴다.
tools: Read, Grep, Glob, Bash
---

너는 {p['name']} **검토원**이다. 작업자 커밋(또는 브랜치) 하나를 보고 **통과 / 고침 필요 / {owner} 확인 필요** 중 하나로 판정한다. 아무것도 고치지 않는다.

## 보는 순서
1. **범위**: `git -C <저장소> log --oneline origin/<기준 브랜치>..<SHA>` 에 작업자 커밋만 있는가. 남의 커밋이 끼었으면 멈추고 보고한다.
2. **diff**: `git -C <저장소> diff origin/<기준 브랜치>...<SHA>`. 과제 밖 수정이 섞였는가.
3. **규칙**: `{slug}` 플러그인 스킬 중 해당하는 것(`absolute-rules` 는 항상). 대상 저장소에 `.claude/rules/` 가 있으면 그것도 본다(없는 하네스도 있다). 위반은 자리와 절 · 줄을 붙인다.
4. **검증**: 작업자가 적은 검증 명령을 직접 다시 돌린다(읽기 전용 명령과 테스트 · 검사만). 출력이 없으면 검증 없음으로 본다.

## 금지
- 파일 수정, 커밋, push, 머지, 운영 환경 조회.

## 돌려줄 형식
| 항목 | 판정 | 근거 |
| --- | --- | --- |
| 범위 | | |
| 규칙 | | 파일:줄 |
| 검증 | | 실행한 명령과 결과 |

**최종: 통과 / 고침 필요 / {owner} 확인 필요** 한 줄과 이유. 추정은 추정이라고 쓴다.
"""


def setup_checklist(cfg: dict, ctx: Dict[str, str]) -> List[str]:
    """설치 직후 남은 준비. 지금 상태에서 참인 줄만 만든다(할 수 없는 일을 할 일로 적지 않는다)."""
    py = ctx["py"]
    items = []
    if not cfg["repos"]:
        items.append(f"- [ ] **코드 저장소 붙이기** (`{py} harness/scripts/bootstrap.py add-repo`). 이것이 먼저다. "
                     "저장소가 없으면 규칙의 채울 자리도 채울 수 없다")
    if not cfg["harness_repo"].get("remote"):
        items.append(f"- [ ] **하네스 원격 만들기.** 이슈 추적 · 라벨이 여기서 켜진다 "
                     f"(원격을 만든 뒤 `{py} harness/scripts/bootstrap.py run --create-github`)")
    items.append(f"- [ ] **규칙의 「채울 자리」 채우기** (`plugins/{cfg['project']['slug']}/skills/` 중 "
                 f"{' · '.join(cfg['skills']['fill']) or '해당하는 것'}). 붙인 저장소에 필요한 것만")
    return items


def board_md(cfg: dict, ctx: Dict[str, str]) -> str:
    checklist = "\n".join(setup_checklist(cfg, ctx))
    return f"""# 상황판

{hl.block("board-intro", "> " + ctx['board_intro'])}
## 지금 집중

**설치 직후입니다. 남은 준비가 아래에 있습니다.** 위에서부터 하나씩 합니다.
무엇부터 할지 모르겠으면 이 폴더에서 {ctx['engine_cmds']} 를 열고 「시작해」라고 말하면 지금 할 것 한 개를 안내합니다.

{checklist}

위가 다 끝나면 이 절을 지우고 지금 집중하는 것 한 줄(이슈 번호와 제목)로 바꾼다.

## 결정 대기
<!-- 채울 자리: `결정대기` 라벨 이슈 -->
"""


def readme_md(cfg: dict, ctx: Dict[str, str]) -> str:
    """설치한 사람이 폴더를 열었을 때 읽는 안내 한 장. 처음 한 번만 만들고 다시 생성해도 덮지 않는다."""
    p, h = cfg["project"], cfg["harness_repo"]
    py = ctx["py"]
    repos = ", ".join(f"`{r['key']}`" for r in cfg["repos"]) or "없음(아래 「저장소 추가해」로 붙인다)"
    remote = f"`{h['remote']}`" if h.get("remote") else "없음(로컬만. 만들면 이슈 추적 · 라벨이 켜진다)"
    engines = " · ".join({"claude": "Claude", "codex": "Codex"}[e] for e in cfg["engines"])
    return f"""# {p['name']} 하네스

이 폴더가 **오케스트레이터**입니다. 여기서 {ctx['engine_cmds']} 를 열고 아래 표의 말을 그대로 하시면 됩니다.
명령이나 경로를 외우지 않아도 됩니다. 에이전트가 지금 상태를 보고 다음 할 것을 안내합니다.

| 하고 싶은 것 | 이렇게 말한다 |
| --- | --- |
| 지금 뭐부터 할지 모르겠다 | **시작해** |
| 코드 저장소를 붙이고 싶다 | **저장소 추가해** |
| 제대로 깔렸는지 보고 싶다 | **점검해** |
| 처음부터 다시 · 다른 기기에 깔고 싶다 | **셋업해** |

## 지금 상태 (설치할 때 기준)

| 항목 | 값 |
| --- | --- |
| 코드 저장소 | {repos} |
| 하네스 원격 | {remote} |
| 엔진 | {engines} |

남은 준비는 `상황판.md` 「지금 집중」에 체크리스트로 있습니다.

## 어느 파일이 무슨 역할인가

| 파일 | 역할 |
| --- | --- |
| `상황판.md` | 지금 집중하는 것 · 남은 준비 한 장 |
| `CLAUDE.md` | 오케스트레이터 지침 · 저장소 지도 · 일하는 순서 |
| `harness.json` | 설정 원본 한 장(저장소 · 엔진 · Orca). 고친 뒤 `{py} harness/scripts/generate.py` |
| `plugins/{p['slug']}/skills/` | 규칙 스킬. 「채울 자리」는 사람이 채운다 |
| `docs/기록/` | 결정의 근거(명령과 출력, 파일과 줄) |

터미널에서 직접 할 때: 저장소 추가 `{py} harness/scripts/bootstrap.py add-repo` ·
점검 `{py} harness/scripts/bootstrap.py doctor` · 템플릿 갱신 `{py} harness/scripts/bootstrap.py update`.

> 이 파일은 처음 한 번만 만듭니다. 고쳐도 다시 생성할 때 덮지 않습니다.
"""


def records_readme(cfg: dict, ctx: Dict[str, str]) -> str:
    return f"""# 기록 (결정의 근거 원본)

- 파일 이름: `YYYY-MM-DD-제목.md`. 한 파일 = 한 조사 또는 한 결정.
- 담는 것: 실행한 명령과 출력, 파일과 줄, 누가 · 언제 쟀나. 결론만 남기지 않는다.
{hl.block("records-link", f"- {ctx['record_from']}. 고쳐 쓰지 않고 쌓는다(틀린 것은 새 기록에서 정정한다).")}"""


# ---------------------------------------------------------------- 설정 파일

def jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def plugin_config(cfg: dict) -> dict:
    h = cfg["harness_repo"]
    repos = {}
    hid = h.get("remote") or h["dir"]
    repos[hid] = {"harness": True, "base": h["base_branch"], "ask": {}}
    for r in cfg["repos"]:
        rid = r.get("remote") or r["dir"]
        repos[rid] = {"ask": {b: (r.get("deploy") or {}).get(b, "") for b in r["ask_on_push"]}}
    protected = (cfg.get("rules") or {}).get("protected") or list(hl.DEFAULT_PROTECTED)
    return {"project": cfg["project"]["name"], "owner_title": cfg["project"]["owner_title"],
            "approve_env": "HARNESS_APPROVED_PUSH", "protected": protected, "repos": repos,
            "orca_workspaces": cfg["orca"]["workspaces_dir"]}


def hook_cmd(py: str, script: str) -> str:
    return f'{py} "${{PLUGIN_ROOT:-${{CLAUDE_PLUGIN_ROOT}}}}/scripts/{script}"'


def plugin_hooks(cfg: dict) -> dict:
    py = cfg["platform"]["python"]
    start = [{"type": "command", "command": hook_cmd(py, "core-context.py")}]
    if hl.has(cfg, "codex"):
        start.append({"type": "command", "command": hook_cmd(py, "codex-bind-session.py")})
    hooks = {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": hook_cmd(py, "guard-push.py")}]}],
        "SessionStart": [{"matcher": "startup|resume|clear|compact", "hooks": start}],
    }
    if hl.has(cfg, "codex") and hl.orca_on(cfg):
        hooks["SessionEnd"] = [{"hooks": [{"type": "command", "command": hook_cmd(py, "archive-codex-session.py")}]}]
    return {"hooks": hooks}


def plugin_scripts(cfg: dict) -> List[str]:
    names = ["guard-push.py", "core-context.py"]
    if hl.has(cfg, "codex"):
        names += ["codex-bind-session.py", "codex_project_binding.py"]
        if hl.orca_on(cfg):
            names.append("archive-codex-session.py")
    return names


def claude_settings(cfg: dict) -> dict:
    py, slug = cfg["platform"]["python"], cfg["project"]["slug"]
    cmd = lambda s: f'{py} "$CLAUDE_PROJECT_DIR/harness/hooks/{s}"'  # noqa: E731
    settings = {
        "permissions": {"additionalDirectories": [hl.repo_ref(r) for r in cfg["repos"]]},
        "hooks": {
            "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                            "hooks": [{"type": "command", "command": cmd("guard-rules.py")}]}],
            "SessionStart": [{"matcher": "startup|resume", "hooks": [{"type": "command", "command": cmd("harness-sync.py")}]}],
            "Stop": [{"hooks": [{"type": "command", "command": cmd("harness-sync.py")}]}],
        },
        "enabledPlugins": {f"{slug}@{slug}": True},
    }
    remote = cfg["harness_repo"].get("remote")
    if remote:
        settings["extraKnownMarketplaces"] = {slug: {"source": {"source": "github", "repo": remote}}}
    return settings


def codex_hooks(cfg: dict) -> dict:
    py = cfg["platform"]["python"]
    cmd = lambda s: f'{py} "$(git rev-parse --show-toplevel)/harness/hooks/{s}"'  # noqa: E731
    return {"hooks": {
        "PreToolUse": [{"matcher": "Bash|apply_patch", "hooks": [{"type": "command", "command": cmd("guard-rules.py")}]}],
        "SessionStart": [{"matcher": "startup|resume", "hooks": [{"type": "command", "command": cmd("harness-sync.py")}]}],
        "Stop": [{"hooks": [{"type": "command", "command": cmd("harness-sync.py")}]}],
    }}


def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # TOML 기본 문자열은 JSON 문자열과 같은 이스케이프를 쓴다


def codex_agent_toml(cfg: dict, name: str, desc: str) -> str:
    slug = cfg["project"]["slug"]
    instr = (f"원본 지시는 하네스 저장소 루트의 plugins/{slug}/agents/{name}.md 다. Claude 와 같은 원본이므로 그 파일을 "
             "처음부터 끝까지 읽고 frontmatter 아래 본문을 그대로 따른다. 여기에 규칙을 복제하지 않는다. "
             "파일을 찾지 못하면 작업을 시작하지 말고 그 사실을 보고한다.")
    return f"name = {toml_str(name)}\ndescription = {toml_str(desc)}\ndeveloper_instructions = {toml_str(instr)}\n"


# ---------------------------------------------------------------- 생성

def engine_files() -> List[Path]:
    """하네스 저장소에 같이 실리는 엔진(harness/) 파일. 캐시 · 테스트 산출물은 뺀다."""
    out = []
    for p in sorted(ENGINE_ROOT.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            out.append(p)
    return out


def generate(cfg: dict, root: Path, dry_run: bool = False, created_repos: Optional[Dict[str, str]] = None,
             created_harness: bool = False) -> hl.Writer:
    root = Path(root)
    w = hl.Writer(root, dry_run=dry_run)
    ctx = context(cfg)
    slug = cfg["project"]["slug"]
    pdir = f"plugins/{slug}"
    produced: List[str] = []
    old, repos, protected, made_here, engine_prev = [], {}, [], created_harness, []
    mpath = root / MANIFEST
    first = not mpath.is_file()
    if not first:
        try:
            prev = json.loads(mpath.read_text(encoding="utf-8"))
            old, repos = prev.get("files", []), dict(prev.get("repos") or {})
            protected = list(prev.get("protected") or [])
            made_here = made_here or bool(prev.get("created_harness"))
            engine_prev = list(prev.get("engine") or [])
        except ValueError:
            old = []
    # 처음 생성할 때 이미 있던 파일(기존 폴더 연결 · --force)은 사람 것이다: 덮지 않고 기록해 두고 다음에도 덮지 않는다
    w.protect = set(protected)
    w.first_run = first

    def managed(rel, text, executable=False):
        if w.guard(rel):
            produced.append(rel)
            w.managed(rel, text, executable)

    # 엔진과 진입 래퍼: 템플릿 저장소 자신에서 돌면(같은 경로) 그대로 둔다.
    # 생성된 하네스 안에서 다시 돌 때는 템플릿에서 복사해 둔 목록(manifest 의 engine)만 하네스 것이다.
    # harness/ 아래를 다시 훑으면 사람이 둔 파일(harness/local/… 등)까지 첫 커밋에 담긴다.
    in_place = root.resolve() == TEMPLATE_ROOT.resolve()
    engine_rels: List[str] = []
    if in_place:
        for rel in engine_prev:
            if rel not in w.protect and (root / rel).is_file():
                w.own(rel)
                engine_rels.append(rel)
    else:
        entries = [(src, "harness/" + src.relative_to(ENGINE_ROOT).as_posix()) for src in engine_files()]
        entries += [(TEMPLATE_ROOT / name, name) for name in ROOT_ENTRY_FILES if (TEMPLATE_ROOT / name).is_file()]
        for src, rel in entries:
            if w.guard(rel):
                w.copy(src, rel)
                engine_rels.append(rel)

    w.mixed("CLAUDE.md", claude_md(cfg, ctx))
    managed("AGENTS.md", agents_md(cfg))
    if w.guard(".gitignore"):  # 블록만 생성기 몫. 블록 밖에 사람이 더한 줄은 update 가 덮지 않는다
        produced.append(".gitignore")
        w.line_block(".gitignore", ".DS_Store\n__pycache__/\n*.pyc\n.claude/worktrees/\n.claude/settings.local.json\n.work/\n"
                                   f"*.{hl.BACKUP_SUFFIX}-*\n" + hl.SECRET_IGNORE)
    managed(".gitattributes", "* text=auto eol=lf\n*.ps1 text eol=crlf\n*.cmd text eol=crlf\n")

    # 플러그인
    managed(f"{pdir}/config.json", jdump(plugin_config(cfg)))
    managed(f"{pdir}/hooks/hooks.json", jdump(plugin_hooks(cfg)))
    for name in plugin_scripts(cfg):
        if w.guard(f"{pdir}/scripts/{name}"):
            produced.append(f"{pdir}/scripts/{name}")
            w.copy(SKELETON / "plugin" / "scripts" / name, f"{pdir}/scripts/{name}")
    w.mixed(f"{pdir}/core.md", core_md(cfg, ctx))
    managed(f"{pdir}/agents/researcher.md", researcher_md(cfg))
    managed(f"{pdir}/agents/reviewer.md", reviewer_md(cfg))
    for r in cfg["repos"]:
        managed(f"{pdir}/agents/{r['key']}-worker.md", worker_md(cfg, r))
    # setup · start 는 채울 자리가 없고 상태 판정만 담으므로 생성기가 주인이다(템플릿이 바뀌면 update 로 들어온다).
    for s in hl.MANAGED_SKILLS:
        managed(f"{pdir}/skills/{s}/SKILL.md", render((SKELETON / "plugin" / "skills" / s / "SKILL.md").read_text(encoding="utf-8"), ctx))
    # 나머지 규칙 스킬은 사람이 채운다: 없을 때만 만들고, 설정에서 만든 표(관리 블록)만 다시 맞춘다.
    # 마커가 없는 옛 파일은 `legacy` 로 그 자리를 찾아 한 번 심는다(사람이 손댔으면 건드리지 않는다).
    lctx = legacy_contexts(cfg, ctx)
    for s in list(hl.PROCEDURE_SKILLS) + list(cfg["skills"]["fill"]):
        w.seed_blocks(f"{pdir}/skills/{s}/SKILL.md",
                      render((SKELETON / "plugin" / "skills" / s / "SKILL.md").read_text(encoding="utf-8"), ctx),
                      legacy=legacy_blocks(LEGACY_SKILL_BLOCKS.get(s, {}), lctx))

    desc = f"{cfg['project']['name']} 공통 하네스: 세션마다 핵심 요약, 규칙 스킬, push 가드 훅, 작업자 · 조사원 · 검토원"
    if hl.has(cfg, "claude"):
        managed(f"{pdir}/.claude-plugin/plugin.json", jdump({"name": slug, "description": desc, "version": PLUGIN_VERSION,
                                                             "author": {"name": cfg["project"].get("github_org") or slug}}))
        managed(".claude-plugin/marketplace.json", jdump({
            "name": slug, "owner": {"name": cfg["project"].get("github_org") or slug},
            "plugins": [{"name": slug, "source": f"./{pdir}", "description": desc}]}))
        managed(".claude/settings.json", jdump(claude_settings(cfg)))
    if hl.has(cfg, "codex"):
        managed(f"{pdir}/.codex-plugin/plugin.json", jdump({
            "name": slug, "version": PLUGIN_VERSION, "description": desc,
            "author": {"name": cfg["project"].get("github_org") or slug}, "skills": "./skills/",
            "interface": {"displayName": f"{cfg['project']['name']} Harness", "shortDescription": desc[:60],
                          "developerName": cfg["project"].get("github_org") or slug, "category": "Developer Tools",
                          "capabilities": ["Read", "Write"]}}))
        managed(".agents/plugins/marketplace.json", jdump({
            "name": f"{slug}-local", "interface": {"displayName": cfg["project"]["name"]},
            "plugins": [{"name": slug, "source": {"source": "local", "path": f"./{pdir}"},
                         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                         "category": "Developer Tools"}]}))
        managed(".codex/hooks.json", jdump(codex_hooks(cfg)))
        managed(".codex/agents/researcher.toml", codex_agent_toml(cfg, "researcher", "저장소 · 문서를 읽고 사실만 돌려주는 조사원"))
        managed(".codex/agents/reviewer.toml", codex_agent_toml(cfg, "reviewer", "작업자 변경을 독립적으로 검토하고 판정하는 검토원"))
        for r in cfg["repos"]:
            managed(f".codex/agents/{r['key']}-worker.toml",
                    codex_agent_toml(cfg, f"{r['key']}-worker", f"{r['dir']} 저장소 과제를 구현하고 검증하는 작업자"))
    if hl.orca_on(cfg):
        for src in sorted((SKELETON / "orca").glob("*.sh")):
            if w.guard(f"scripts/{src.name}"):
                produced.append(f"scripts/{src.name}")
                w.copy(src, f"scripts/{src.name}")

    w.seed("README.md", readme_md(cfg, ctx))
    w.seed_blocks("상황판.md", board_md(cfg, ctx), legacy=legacy_blocks(LEGACY_BOARD, lctx))
    w.seed_blocks("docs/기록/README.md", records_readme(cfg, ctx), legacy=legacy_blocks(LEGACY_RECORDS, lctx))

    # 설정이 바뀌어 필요 없어진 관리 파일 지우기
    # 이 하네스가 새로 만든 코드 저장소: 폴더 → 첫 커밋 SHA(아직 커밋 전이면 빈 문자열). 남의 저장소 판별에 쓴다
    for d, sha in (created_repos or {}).items():
        if not repos.get(d):
            repos[d] = sha
    for rel in sorted(set(old) - set(produced)):
        target = root / rel
        if target.is_file():
            w.changed.append(f"(지움) {rel}")
            if not dry_run:
                target.unlink()
    record = {"note": "생성기가 만든 관리 파일 목록과 이 하네스가 만든 저장소. 고치지 않는다.",
              "files": sorted(produced), "repos": dict(sorted(repos.items())), "protected": sorted(w.protect)}
    if made_here:  # bootstrap 이 이 하네스 저장소를 새로 만들었다: 첫 커밋을 미뤘다면 다음 run 이 이어서 만든다
        record["created_harness"] = True
        record["engine"] = sorted(engine_rels)  # 템플릿에서 복사한 엔진 · 진입 래퍼(첫 커밋 대상)
    w.managed(MANIFEST, jdump(record))
    return w


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="harness.json → 하네스 파일 생성")
    ap.add_argument("--root", type=Path, default=TEMPLATE_ROOT, help="하네스 저장소 루트(harness.json 이 있는 곳)")
    ap.add_argument("--config", type=Path, help="설정 파일(기본 <root>/harness.json)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = hl.load_config(args.config or args.root / hl.CONFIG_NAME)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    w = generate(cfg, args.root, dry_run=args.dry_run)
    print(f"생성: 바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)}")
    for rel in w.changed:
        print(f"  {rel}")
    for line in block_lines(w):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
