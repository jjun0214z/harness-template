#!/usr/bin/env python3
"""하네스 셋업 본체. macOS · Linux · Windows 공통(python3 표준 라이브러리만).

출발점은 막 산 컴퓨터일 수도, 일부 갖춘 컴퓨터일 수도 있다. 모든 단계는
「감지 → 버전 확인 → 충분하면 건너뜀 / 모자라면 올릴지 · 없으면 설치할지」를 한 표로 먼저 보여 주고 한 번에 동의받는다.
git · python · node 는 install.sh · install.ps1 이 파이썬보다 먼저 챙긴다. 여기서는 나머지(pnpm · 엔진 CLI · gh)를 챙긴다.

  run      질문(또는 --config) → 도구 표 · 동의 → 하네스 · 저장소 새로 만들기(또는 clone) → 생성 → 엔진 설치 → 원격 → Orca → doctor
  check    설정만 검증
  tools    도구 표(--yes 면 동의한 것으로 보고 설치)
  generate 생성기만
  update   템플릿 최신본의 엔진(harness/)을 가져와 다시 생성(사람이 채운 부분은 덮지 않는다)
  doctor   점검 표 + 나중에 할 일

두 번 돌려도 안전하다. 사람 손이 필요한 것(로그인 · 관리자 권한)은 멈추고 명령만 안내한다.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable, List, Optional, Tuple
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402

TEMPLATE_ROOT = gen.TEMPLATE_ROOT


class Report:
    """doctor 표 줄 모음과 「나중에 할 일」. 상태는 통과 · 실패 · 없음 · 건너뜀 · 안내."""

    def __init__(self):
        self.rows: List[Tuple[str, str, str]] = []
        self.todos: List[str] = []

    def add(self, item: str, state: str, detail: str = "") -> None:
        self.rows.append((item, state, detail))

    def todo(self, text: str) -> None:
        if text not in self.todos:
            self.todos.append(text)

    def table(self) -> str:
        lines = ["| 항목 | 결과 | 내용 |", "| --- | --- | --- |"]
        for item, state, detail in self.rows:
            lines.append(f"| {item} | {state} | {detail.replace('|', '/')} |")
        if self.todos:
            lines += ["", "## 나중에 할 일"] + [f"{i}. {t}" for i, t in enumerate(self.todos, 1)]
        return "\n".join(lines)

    def failed(self) -> List[Tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "실패"]


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 300, input_text: Optional[str] = None) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                           timeout=timeout, input=input_text)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout + r.stderr).strip()


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 질문

NO_INPUT = "대화형 입력이 없다. --config 파일을 주거나 터미널에서 실행한다"
_EOF = object()


def ask(prompt: str, default: str = "", reader: Callable[[str], str] = input, on_eof=_EOF) -> str:
    """입력이 끊기면(EOF) 기본값으로 넘어가지 않는다: 반복 질문이 영원히 돌지 않게 멈춘다(종료 코드 2).
    on_eof 를 주면 멈추지 않고 그 값을 돌려준다(설치 동의처럼 「아니오」로 봐도 되는 질문)."""
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        value = reader(shown).strip()
    except EOFError:
        if on_eof is not _EOF:
            return on_eof
        hl.eprint("\n" + NO_INPUT)
        raise SystemExit(2)
    return value or default


def ask_yes(prompt: str, default: bool, reader: Callable[[str], str] = input, on_eof=_EOF) -> bool:
    ans = ask(prompt + (" (Y/n)" if default else " (y/N)"), "", reader, on_eof="n" if on_eof is False else on_eof).lower()
    if not ans:
        return default
    return ans in ("y", "yes", "예", "네", "ㅇ")


def interview(kind: str, reader: Callable[[str], str] = input, default_root=None) -> dict:
    """네 묶음 질문 → 설정 dict."""
    cfg = hl.default_config()
    say("\n[1/4] 프로젝트")
    p = cfg["project"]
    p["name"] = ask("프로젝트 이름", "", reader)
    p["slug"] = ask("영문 슬러그(플러그인 이름, 소문자 · 숫자 · -)", re.sub(r"[^a-z0-9-]", "", p["name"].lower().replace(" ", "-")) or "myproject", reader)
    if default_root is not None:
        here = default_root(None, {"project": {"slug": p["slug"]}})
        cfg["_root"] = ask(f"여기에 만듭니다: {here}  (Enter = 그대로, 다른 경로 입력)", str(here), reader)
    p["github_org"] = ask("GitHub 조직 또는 사용자(없으면 빈칸: 로컬만 만든다)", "", reader)
    p["owner_title"] = ask("결정권자를 부르는 호칭", "대표님", reader)
    h = cfg["harness_repo"]
    how = ask("하네스 저장소: (1) 새로 만들기 (2) 이 컴퓨터에 있는 폴더 연결", "1", reader)
    if how == "2":
        h["source"] = "local"
        h["path"] = ask("  연결할 하네스 폴더 경로", "", reader)
        h["base_branch"] = ask("  기준 브랜치", hl.detect_base_branch(Path(os.path.expanduser(h["path"]))) or "main", reader)
    else:
        h["dir"] = ask("  하네스 저장소 폴더 이름", "orchestrator", reader)
        h["base_branch"] = ask("  기준 브랜치", "main", reader)

    say("\n[2/4] 저장소 (빈 키를 넣으면 끝). 저장소마다 새로 만들기 · 원격에서 받기 · 이 컴퓨터의 폴더 연결 중 고른다")
    while True:
        key = ask(f"저장소 {len(cfg['repos']) + 1} 키(영문, 작업자 이름 <키>-worker)", "", reader)
        if not key:
            if cfg["repos"]:
                break
            say("  저장소를 하나 이상 넣는다.")
            continue
        r = {"key": key}
        how = ask("  (1) 새로 만들기 (2) 원격 주소에서 받기(clone) (3) 이 컴퓨터에 있는 폴더 연결", "1", reader)
        if how == "3":
            r["source"] = "local"
            r["path"] = ask("  연결할 폴더 경로(다른 위치여도 된다. 옮기지 않는다)", "", reader)
            local = Path(os.path.expanduser(r["path"]))
            r["dir"] = local.name
            r["remote"] = ask("  GitHub 원격(조직/이름)", hl.github_remote(local), reader)
            r["base_branch"] = ask("  기준 브랜치(그 저장소의 기본 브랜치)", hl.detect_base_branch(local) or "main", reader)
            r["connect_files"] = ask_yes("  작업자용 최소 파일(CLAUDE.md · AGENTS.md · .claude/settings.json)을 없는 것만 더할까", False, reader)
        else:
            if how == "2":
                r["source"], r["url"] = "clone", ask("  원격 주소", "", reader)
            r["dir"] = ask("  폴더", key, reader)
            r["remote"] = ask("  GitHub 원격(조직/이름, 없으면 빈칸)", f"{p['github_org']}/{key}" if p["github_org"] else "", reader)
            r["base_branch"] = ask("  기준 브랜치", "main", reader)
        r["description"] = ask("  한 줄 설명", "", reader)
        stack = ask("  스택 (1) node/pnpm (2) python (3) 없음", "3", reader)
        r["stack"] = {"1": "node", "2": "python"}.get(stack, "none")
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
        cfg["repos"].append(r)

    say("\n[3/4] 규칙 스킬: 절차 뼈대 5개는 항상 넣는다")
    cfg["skills"]["fill"] = [s for s in hl.FILL_SKILLS if ask_yes(f"  채울 자리 틀 {s} 넣기", True, reader)]

    say("\n[4/4] 엔진 · Orca")
    choice = ask("엔진 (1) Claude (2) Codex (3) 둘 다", "1", reader)
    cfg["engines"] = {"1": ["claude"], "2": ["codex"], "3": ["claude", "codex"]}.get(choice, ["claude"])
    note = " (Windows: Orca 앱은 공식 지원, 이 하네스의 Orca 스크립트는 Git Bash 필요)" if kind == "windows" else ""
    cfg["orca"]["enabled"] = ask_yes("Orca 를 쓰나" + note, False, reader)
    return hl.normalize(cfg)


# ---------------------------------------------------------------- 도구

def needed_tools(cfg: dict) -> List[str]:
    tools = ["git"]
    node_needed = bool(cfg["engines"]) or any("node" in hl.STACKS[r["stack"]][0] for r in cfg["repos"])
    if node_needed:
        tools.append("node")
    if any("pnpm" in hl.STACKS[r["stack"]][0] for r in cfg["repos"]) or any("pnpm" in c for r in cfg["repos"] for c in r["checks"]):
        tools.append("pnpm")
    tools.append("gh")
    tools += list(cfg["engines"])
    if hl.orca_on(cfg):
        tools.append("orca")
    return tools


def detect(tool: str, kind: str) -> Tuple[Optional[str], str, Optional[str]]:
    """(경로 | None, 설명, 찾은 버전 | None). node 가 22 미만이면 경로는 None, 버전은 채운다."""
    if tool == "node":
        path, ver = hl.find_node(kind)
        if path is None:
            return None, "없음", None
        if hl.node_major(ver) < hl.MIN_NODE_MAJOR:
            return None, f"{ver} (22 이상 필요)", ver
        return str(path), f"{ver} ({path})", ver
    path = hl.find_tool(tool, kind, hl.tool_extra_paths(tool, kind))
    if not path:
        return None, "없음", None
    ver = hl.version_of([path]) if tool != "orca" else ""
    return path, (ver or "있음"), ver


def node_upgrade_command(kind: str) -> Optional[Tuple[List[str], str]]:
    """이미 쓰는 버전 관리자(nvm · fnm · nvm-windows)로 22 를 더한다. 기존 설치를 지우지 않는다."""
    nvm_sh = Path(os.environ.get("NVM_DIR") or (Path.home() / ".nvm")) / "nvm.sh"
    if kind != "windows" and nvm_sh.is_file() and shutil.which("bash"):
        return ["bash", "-c", f'. "{nvm_sh}" && nvm install {hl.MIN_NODE_MAJOR}'], f"nvm install {hl.MIN_NODE_MAJOR}"
    if shutil.which("fnm"):
        return ["fnm", "install", str(hl.MIN_NODE_MAJOR)], f"fnm install {hl.MIN_NODE_MAJOR}"
    if kind == "windows" and shutil.which("nvm"):
        return ["nvm", "install", str(hl.MIN_NODE_MAJOR)], f"nvm install {hl.MIN_NODE_MAJOR} (nvm-windows)"
    return None


def plan_tools(cfg: dict, kind: str) -> List[dict]:
    """도구마다 {tool, found, action(건너뜀 · 올림 · 설치 · 안내), cmd, hint}."""
    have = lambda name: hl.find_tool(name, kind, hl.tool_extra_paths(name, kind)) is not None  # noqa: E731
    rows = [{"tool": "python", "found": sys.version.split()[0], "action": "건너뜀", "cmd": None,
             "hint": "3.9 이상이라 충분하다" if sys.version_info >= hl.MIN_PYTHON else "3.9 미만"}]
    for tool in needed_tools(cfg):
        path, info, ver = detect(tool, kind)
        row = {"tool": tool, "found": info if path else (ver or "없음"), "action": "건너뜀", "cmd": None, "hint": ""}
        if path:
            rows.append(row)
            continue
        if tool == "orca":
            row["action"], row["hint"] = "안내", {
                "mac": "brew install --cask stablyai/orca/orca 또는 https://onorca.dev",
                "windows": "winget install --id StablyAI.Orca",
                "linux": "https://github.com/stablyai/orca/releases 의 AppImage"}[kind]
        elif tool == "node" and ver:
            up = node_upgrade_command(kind)
            cmd, hint = up if up else hl.install_command("node", kind, have)
            row.update(action="올림" if cmd else "안내", cmd=cmd, hint=hint)
        else:
            cmd, hint = hl.install_command(tool, kind, have)
            row.update(action="설치" if cmd else "안내", cmd=cmd, hint=hint)
        rows.append(row)
    return rows


def plan_table(rows: List[dict]) -> str:
    lines = ["| 도구 | 찾은 버전 | 할 일 | 명령 |", "| --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['tool']} | {r['found']} | {r['action']} | {r['hint'] or '-'} |")
    return "\n".join(lines)


def refresh_node_path(kind: str) -> None:
    """node 를 새로 넣은 뒤 새 셸 없이 이어지게 PATH 앞에 그 폴더를 둔다(brew node@22 는 keg-only)."""
    extra = []
    brew = shutil.which("brew")
    if brew:
        code, out = run([brew, "--prefix", f"node@{hl.MIN_NODE_MAJOR}"], timeout=30)
        if code == 0 and out:
            extra.append(str(Path(out.splitlines()[-1]) / "bin"))
    path, _ = hl.find_node(kind)
    if path:
        extra.append(str(path.parent))
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def tools_step(cfg: dict, kind: str, rep: Report, yes: bool, interactive: bool) -> None:
    rows = plan_tools(cfg, kind)
    say("\n도구 감지 결과\n" + plan_table(rows))
    todo = [r for r in rows if r["action"] in ("설치", "올림")]
    go = yes or (interactive and todo and ask_yes("\n위 표의 설치 · 올림을 진행할까요", True, on_eof=False))
    if todo and not go:
        say("설치는 건너뛰고 템플릿 받기와 셋업은 계속한다. 빠진 도구는 마지막 「나중에 할 일」에 적는다")
    for r in rows:
        tool = r["tool"]
        if r["action"] == "건너뜀":
            rep.add(f"도구 {tool}", "통과", r["found"])
        elif r["action"] == "안내" or not go:
            rep.add(f"도구 {tool}", "없음", f"{r['action']}: {r['hint']}")
            rep.todo(f"{tool} {'올리기' if r['action'] == '올림' else '설치'}: {r['hint']}")
        else:
            exe = shutil.which(r["cmd"][0]) or r["cmd"][0]
            say(f"[{r['action']}] {r['hint']}")
            code, out = run([exe] + r["cmd"][1:], timeout=1800)
            if tool == "node":
                refresh_node_path(kind)
            path, info, _ = detect(tool, kind)
            rep.add(f"도구 {tool}", "통과" if path else "실패", info if path else f"명령 실패({code}): {out[-200:]}")
    login_step(cfg, kind, rep)


def logged_in(tool: str, kind: str) -> Optional[bool]:
    """로그인됐으면 True, 안 됐으면 False, 잴 수 없으면 None."""
    path = hl.find_tool(tool, kind, hl.tool_extra_paths(tool, kind))
    if not path:
        return None
    if tool == "gh":
        return run([path, "auth", "status"], timeout=30)[0] == 0
    if tool == "codex":
        return run([path, "login", "status"], timeout=30)[0] == 0
    if tool == "claude":
        # 로그인 흔적: ~/.claude.json 의 oauthAccount 또는 API 키. 키체인에만 있으면 못 잰다(추정).
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True
        try:
            return "oauthAccount" in (Path.home() / ".claude.json").read_text(encoding="utf-8")
        except OSError:
            return False
    return None


def login_step(cfg: dict, kind: str, rep: Report) -> None:
    hints = {"claude": "터미널에서 claude 를 한 번 실행해 로그인한다", "codex": "! codex login"}
    for engine in cfg["engines"]:
        state = logged_in(engine, kind)
        if state is True:
            rep.add(f"{engine} 로그인", "통과", "로그인돼 있어 건너뜀")
        elif state is False:
            rep.add(f"{engine} 로그인", "없음", hints[engine])
            rep.todo(f"{engine} 로그인: {hints[engine]}")


# ---------------------------------------------------------------- 저장소

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
    listed = " ".join(f'"{f}"' for f in files)
    if not git_identity(dest):
        rep.todo(f"{label} 첫 커밋: git config --global user.name \"<이름>\" · git config --global user.email \"<메일>\" 뒤 "
                 f"git -C \"{dest}\" add -- {listed} && git -C \"{dest}\" commit -m \"{message}\"")
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
        ".gitignore": ".DS_Store\n__pycache__/\nnode_modules/\n.env\n.env.local\n",
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
        rep.todo(f"{r['dir']}: 작업자용 최소 파일(CLAUDE.md · AGENTS.md · .claude/settings.json)은 더하지 않았다. 원하면 harness.json 에 connect_files: true 로 다시 실행")
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
    if gh_ok and create:
        remote = cfg["harness_repo"].get("remote")
        if remote:
            labels = list(cfg["labels"]) + [f"repo:{r['key']}" for r in cfg["repos"]]
            bad = [lb for lb in labels if run([gh, "label", "create", lb, "-R", remote, "--force"], timeout=30)[0] != 0]
            rep.add("이슈 라벨", "실패" if bad else "통과", ("못 만든 것: " + ", ".join(bad)) if bad else " · ".join(labels))
    elif cfg["harness_repo"].get("remote"):
        rep.todo("이슈 라벨: 원격을 만든 뒤 bootstrap run --create-github 을 다시 돌리면 만든다")


# ---------------------------------------------------------------- 엔진 설치

def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def codex_config_lines(cfg: dict, target: Path, parent: Path) -> List[Tuple[str, str]]:
    """(헤더, 블록) 목록: 마켓플레이스 하나 + 저장소 신뢰."""
    slug = cfg["project"]["slug"]
    blocks = [(f"[marketplaces.{slug}-local]",
               f"[marketplaces.{slug}-local]\nsource_type = \"local\"\nsource = {gen.toml_str(str(target.resolve()))}\n")]
    for p in [target] + [hl.repo_path(r, parent) for r in cfg["repos"]]:
        header = f"[projects.{gen.toml_str(str(p.resolve()))}]"
        blocks.append((header, f"{header}\ntrust_level = \"trusted\"\n"))
    return blocks


def codex_config_step(cfg: dict, target: Path, parent: Path, rep: Report) -> None:
    """~/.codex/config.toml 에 없는 블록만 덧붙인다. 기존 항목은 덮지 않고, 바꾸기 전 백업. 내용은 출력하지 않는다."""
    config = codex_home() / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    missing = [(h, b) for h, b in codex_config_lines(cfg, target, parent) if h not in text]
    if not missing:
        rep.add("Codex 설정", "통과", "마켓플레이스 · 저장소 신뢰 항목 있음(건너뜀)")
        return
    if config.is_file():
        stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(config, config.with_name(f"config.toml.harness-bak-{stamp}"))
    config.parent.mkdir(parents=True, exist_ok=True)
    with open(config, "a", encoding="utf-8", newline="\n") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        for _, b in missing:
            fh.write("\n" + b)
    rep.add("Codex 설정", "통과", f"항목 {len(missing)}개 덧붙임(기존 항목 유지, 백업 후)")


def install_step(cfg: dict, target: Path, parent: Path, kind: str, rep: Report) -> None:
    slug = cfg["project"]["slug"]
    if hl.has(cfg, "claude"):
        claude = hl.find_tool("claude", kind, hl.tool_extra_paths("claude", kind))
        if not claude:
            rep.add("Claude 플러그인", "없음", "claude CLI 가 없다")
            rep.todo("claude CLI 설치 뒤 bootstrap run 을 다시 돌린다")
        else:
            code, out = run([claude, "plugin", "marketplace", "list"], timeout=60)
            if slug in out:
                code, out = run([claude, "plugin", "marketplace", "update", slug], timeout=120)
            else:
                code, out = run([claude, "plugin", "marketplace", "add", str(target.resolve())], timeout=120)
            ok = code == 0
            # 하네스는 커밋된 .claude/settings.json 이 켠다. 코드 저장소는 local 범위(커밋 안 되는 settings.local.json).
            bad = [] if run([claude, "plugin", "install", f"{slug}@{slug}", "--scope", "project"], cwd=target, timeout=120)[0] == 0 else [cfg["harness_repo"]["dir"]]
            for r in cfg["repos"]:
                d = hl.repo_path(r, parent)
                if d.is_dir() and run([claude, "plugin", "install", f"{slug}@{slug}", "--scope", "local"], cwd=d, timeout=120)[0] != 0:
                    bad.append(r["dir"])
            if ok and not bad:
                rep.add("Claude 플러그인", "통과", f"{slug}@{slug} 마켓플레이스 · 설치")
            else:
                rep.add("Claude 플러그인", "실패", f"마켓플레이스 {'통과' if ok else out[-120:]} · 설치 실패 {', '.join(bad) or '없음'}")
    if hl.has(cfg, "codex"):
        codex_config_step(cfg, target, parent, rep)
        codex = hl.find_tool("codex", kind, hl.tool_extra_paths("codex", kind))
        if not codex:
            rep.add("Codex 플러그인", "없음", "codex CLI 가 없다")
            rep.todo("codex CLI 설치 뒤 bootstrap run 을 다시 돌린다")
        else:
            code, out = run([codex, "plugin", "add", f"{slug}@{slug}-local", "--json"], cwd=target, timeout=180)
            rep.add("Codex 플러그인", "통과" if code == 0 else "실패",
                    f"{slug}@{slug}-local" if code == 0 else f"codex plugin add 실패: {out[-160:]}")


def orca_step(cfg: dict, target: Path, parent: Path, kind: str, rep: Report) -> None:
    if not hl.orca_on(cfg):
        return
    if kind == "windows" and not shutil.which("bash"):
        rep.add("Orca 스크립트", "안내", "scripts/orca-*.sh 는 bash 가 필요하다(Git for Windows)")
        rep.todo("Git for Windows(bash) 설치: winget install -e --id Git.Git. 없으면 작업자는 서브에이전트 · Codex 앱 워크트리로")
    orca = hl.find_tool("orca", kind, hl.tool_extra_paths("orca", kind))
    if not orca:
        rep.add("Orca 등록", "없음", "orca CLI 를 못 찾음(ORCA_BIN 으로 지정 가능)")
        rep.todo("Orca 설치 · 실행 뒤 bootstrap run 을 다시 돌린다")
        return
    code, out = run([orca, "repo", "list", "--json"], timeout=30)
    if code != 0:
        rep.add("Orca 등록", "실패", "Orca 앱을 켠 뒤 다시 실행")
        return
    try:
        known = {os.path.realpath(r.get("path", "")) for r in (json.loads(out).get("result") or {}).get("repos") or []}
    except ValueError:
        known = set()
    rows = [(cfg["harness_repo"]["dir"], target, cfg["harness_repo"]["base_branch"])]
    rows += [(r["dir"], hl.repo_path(r, parent), r["base_branch"]) for r in cfg["repos"]]
    bad, added = [], 0
    for name, path, base in rows:
        if not path.is_dir():
            continue
        if os.path.realpath(str(path)) not in known:
            if run([orca, "repo", "add", "--path", str(path), "--json"], timeout=60)[0] != 0:
                bad.append(name)
                continue
            added += 1
        if run([orca, "repo", "set-base-ref", "--repo", f"path:{path}", "--ref", f"origin/{base}", "--json"], timeout=60)[0] != 0:
            bad.append(name)
    rep.add("Orca 등록", "실패" if bad else "통과",
            f"실패: {', '.join(bad)}" if bad else f"새로 등록 {added} · 이미 등록된 것은 건너뜀 · 기준 브랜치")


# ---------------------------------------------------------------- update

def fetch_template(url: str, ref: str, dest: Path) -> Path:
    """템플릿 최신본을 dest 아래에 받는다. 로컬 폴더면 그대로, git 이 있으면 clone, 없으면 tar.gz 를 받아 푼다."""
    if os.path.isdir(url):
        return Path(url)
    if shutil.which("git"):
        code, out = run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref, url, str(dest / "t")], timeout=600)
        if code == 0:
            return dest / "t"
    m = re.match(r"https://github\.com/([^/]+)/([^/.]+)", url)
    if not m:
        raise RuntimeError(f"템플릿을 받지 못했다: {url}")
    tar_url = f"https://codeload.github.com/{m.group(1)}/{m.group(2)}/tar.gz/refs/heads/{ref}"
    with urllib.request.urlopen(tar_url, timeout=120) as resp:  # 공개 저장소라 인증이 필요 없다
        data = resp.read()
    safe_extract(data, dest / "x")
    return next((dest / "x").iterdir())


def safe_extract(data: bytes, out: Path) -> None:
    """tar.gz 를 푼다. 절대 경로 · .. · 심링크 · 하드링크 · 장치 파일은 거른다. 파일 권한(실행 비트)은 그대로 둔다."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = []
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise RuntimeError(f"압축 안에 이상한 경로가 있다: {m.name}")
            if m.isfile() or m.isdir():
                members.append(m)
        tf.extractall(out, members=members)


def cmd_update(args) -> int:
    target = resolve_target(args, None)
    try:
        cfg = hl.load_config(target / hl.CONFIG_NAME)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    src_url = args.source or cfg["template"]["url"]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            src = fetch_template(src_url, cfg["template"]["ref"], Path(tmp))
        except (RuntimeError, OSError) as exc:
            hl.eprint(exc)
            return 1
        if not (src / "harness" / "scripts" / "generate.py").is_file():
            hl.eprint(f"템플릿이 아니다(harness/scripts/generate.py 없음): {src_url}")
            return 1
        if src.resolve() == target.resolve():
            say("템플릿 자신이다. 가져올 것이 없다")
            return 0
        engine = target / "harness"
        new_files = {p.relative_to(src / "harness") for p in (src / "harness").rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts}
        for old in [p for p in engine.rglob("*") if p.is_file()] if engine.is_dir() else []:
            if old.relative_to(engine) not in new_files and "__pycache__" not in old.parts:
                old.unlink()
        for rel in sorted(new_files):
            dst = engine / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / "harness" / rel, dst)
        for name in gen.ROOT_ENTRY_FILES:  # 첫 생성과 같은 파일 집합(install.* 은 템플릿에만 둔다)
            if (src / name).is_file():
                shutil.copy2(src / name, target / name)
    # 새 엔진의 생성기로 다시 만든다(사람이 채운 부분은 생성기 규칙대로 둔다)
    code = subprocess.call([sys.executable, str(engine / "scripts" / "generate.py"), "--root", str(target)])
    say("갱신 끝: 바뀐 파일은 git diff 로 보고 경로를 명시해 커밋한다" if code == 0 else "생성 실패")
    return code


# ---------------------------------------------------------------- doctor

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
    hook_smoke(cfg, target, rep)
    parent = hl.git_common_parent(target) or target.parent
    for p, name in [(target, cfg["harness_repo"]["dir"])] + [(hl.repo_path(r, parent), r["dir"]) for r in cfg["repos"]]:
        if not (p / ".git").exists():
            rep.add(f"저장소 {name}", "없음", str(p))
            continue
        origin = run(["git", "remote", "get-url", "origin"], cwd=p)[0] == 0
        rep.add(f"저장소 {name}", "통과", ("원격 연결됨" if origin else "로컬만(원격 없음)") + f" · {p}")
        if not has_commit(p):
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


# ---------------------------------------------------------------- 명령

def caller_base() -> Path:
    """install · bootstrap 을 실행한 셸의 현재 폴더. install.sh 가 cd 하기 전에 HARNESS_CALLER_CWD 로 넘긴다.
    템플릿 폴더 안에서 실행했으면 템플릿 옆(템플릿 안에 프로젝트를 만들지 않는다)."""
    base = Path(os.environ.get("HARNESS_CALLER_CWD") or os.getcwd()).expanduser().resolve()
    tpl = TEMPLATE_ROOT.resolve()
    if base == tpl or tpl in base.parents:
        return tpl.parent
    return base


def project_root(args, cfg: Optional[dict]) -> Path:
    """프로젝트 루트 = --root, 없으면 <실행한 폴더>/<프로젝트 슬러그>. 그 안에 하네스와 새 저장소들."""
    if getattr(args, "root", None):
        return Path(args.root).expanduser().resolve()
    slug = cfg["project"]["slug"] if cfg else "project"
    return caller_base() / slug


def resolve_target(args, cfg: Optional[dict]) -> Path:
    if getattr(args, "target", None):
        return Path(args.target).expanduser().resolve()
    if cfg and cfg["harness_repo"].get("source") == "local":
        return Path(cfg["harness_repo"]["path"])
    if (TEMPLATE_ROOT / hl.CONFIG_NAME).is_file() and not (TEMPLATE_ROOT / hl.TEMPLATE_MARK_FILE).is_file():
        return TEMPLATE_ROOT  # 생성된 하네스 안의 엔진으로 돈다: 여기가 하네스
    if not cfg and (Path.cwd() / hl.CONFIG_NAME).is_file():
        return Path.cwd().resolve()  # doctor · generate 를 하네스 폴더에서 부른 경우
    name = cfg["harness_repo"]["dir"] if cfg else "orchestrator"
    return project_root(args, cfg) / name


def root_problem(root: Path, cfg: dict, force: bool) -> Optional[str]:
    """프로젝트 루트가 이미 있고 하네스 · 저장소 말고 다른 것이 있으면 멈춘다(harness.json 이 있으면 우리 프로젝트)."""
    if not root.is_dir() or (root / cfg["harness_repo"]["dir"] / hl.CONFIG_NAME).is_file() or force:
        return None
    allowed = {cfg["harness_repo"]["dir"], ".DS_Store", ".work"} | {r["dir"] for r in cfg["repos"]}
    extra = sorted(p.name for p in root.iterdir() if p.name not in allowed)
    if extra:
        return (f"프로젝트 루트 {root} 가 이미 있고 하네스 표시(harness.json)가 없는데 다른 것이 있다({', '.join(extra[:5])}). "
                "섞지 않도록 멈춘다. --root 로 다른 폴더를 주거나 그래도 여기면 --force")
    return None


def load_or_ask(args, kind: str) -> dict:
    if args.config:
        return hl.load_config(Path(args.config))
    if args.target and (Path(args.target) / hl.CONFIG_NAME).is_file():
        return hl.load_config(Path(args.target) / hl.CONFIG_NAME)
    if (TEMPLATE_ROOT / hl.CONFIG_NAME).is_file():
        return hl.load_config(TEMPLATE_ROOT / hl.CONFIG_NAME)
    if args.non_interactive:
        raise hl.ConfigError("--non-interactive 에는 --config 가 필요하다")
    cfg = interview(kind, default_root=None if (args.root or args.target) else project_root)
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
    if cfg["harness_repo"].get("source") == "local" and not (target / ".git").exists():
        reason = f"하네스로 연결할 git 저장소가 없다: {target}"
    if reason:
        hl.eprint(reason)
        return 2
    rep = Report()
    say(f"하네스 폴더: {target}  (프로젝트 루트: {target.parent} · 템플릿 원본: {TEMPLATE_ROOT})")
    tools_step(cfg, kind, rep, yes=args.yes, interactive=not args.non_interactive)
    created = cfg["harness_repo"].get("source") != "local" and (
        not (target / ".git").exists() or (not has_commit(target) and not (target / hl.CONFIG_NAME).is_file()))
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
    w = gen.generate(cfg, target, created_repos=new_repos)
    rep.add("생성", "통과", f"바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)} · 원래 있던 파일 보호 {len(w.protect)}")
    for rel in sorted(w.protect):
        rep.todo(f"하네스 {rel} 은 원래 있던 파일이라 덮지 않았다. 템플릿 내용(harness/skeleton 또는 새 폴더에 생성해 본 것)과 직접 합친다")
    if cfg["harness_repo"].get("source") == "local":
        rep.todo(f"하네스 연결: {target} 에 더한 파일을 git status 로 보고 경로를 명시해 커밋한다")
    if created:
        made = [f for f in w.changed if not f.startswith("(지움)")] + w.kept + [hl.CONFIG_NAME, gen.MANIFEST]
        first_commit(target, HARNESS_COMMIT, rep, cfg["harness_repo"]["dir"], made)
    remote_step(cfg, target, parent, rep, args.offline, args.create_github)
    if args.skip_install:
        rep.add("엔진 설치", "건너뜀", "--skip-install")
    else:
        install_step(cfg, target, parent, kind, rep)
        orca_step(cfg, target, parent, kind, rep)
    doctor(target, kind, args.offline, rep, tools=False)
    say("\n" + rep.table())
    say("\n다음: 규칙 스킬의 「채울 자리」를 채운다. 템플릿이 갱신되면 python3 harness/scripts/bootstrap.py update")
    return 1 if rep.failed() else 0


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


def config_for(args) -> dict:
    return hl.load_config(Path(args.config) if args.config else resolve_target(args, None) / hl.CONFIG_NAME)


def cmd_check(args) -> int:
    try:
        cfg = config_for(args)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    say(f"설정 통과: {cfg['project']['name']} · 저장소 {len(cfg['repos'])} · 엔진 {', '.join(cfg['engines'])} · Orca {'예' if hl.orca_on(cfg) else '아니오'}")
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
    try:
        cfg = hl.load_config(Path(args.config) if args.config else target / hl.CONFIG_NAME)
    except hl.ConfigError as exc:
        hl.eprint(exc)
        return 2
    w = gen.generate(cfg, target)
    say(f"생성: 바뀐 파일 {len(w.changed)} · 사람이 채운 파일 유지 {len(w.kept)}")
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
    for name in ("check", "tools", "generate", "doctor"):
        sub.add_parser(name, parents=[common])
    u = sub.add_parser("update", parents=[common], help="템플릿 최신본의 엔진을 가져와 다시 생성")
    u.add_argument("--source", help="템플릿 주소 또는 로컬 폴더(기본: harness.json template.url)")
    args = ap.parse_args(argv)
    handlers = {"run": cmd_run, "check": cmd_check, "tools": cmd_tools, "generate": cmd_generate,
                "doctor": cmd_doctor, "update": cmd_update}
    if not args.cmd:
        args = ap.parse_args(["run"] + list(argv if argv is not None else sys.argv[1:]))
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
