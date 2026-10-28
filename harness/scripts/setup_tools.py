"""셋업 도구: 필요한 도구만 고르고(node 는 쓸 때만) 감지 표 → 동의 → 설치 → 로그인 확인."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import List, Optional, Tuple
import urllib.request

import harnesslib as hl  # noqa: E402
import runners  # noqa: E402
from setup_common import Report, ask_yes, run, say  # noqa: E402


def needed_tools(cfg: dict) -> List[str]:
    tools = ["git"]
    # node 는 쓸 때만: codex(npm 설치) · node 스택 · pnpm. claude 는 네이티브 설치기라 필요 없다
    node_needed = ("codex" in cfg["engines"] or any("node" in hl.STACKS[r["stack"]][0] for r in cfg["repos"])
                   or any("pnpm" in c for r in cfg["repos"] for c in r["checks"]))
    if node_needed:
        tools.append("node")
    if any("pnpm" in hl.STACKS[r["stack"]][0] for r in cfg["repos"]) or any("pnpm" in c for r in cfg["repos"] for c in r["checks"]):
        tools.append("pnpm")
    tools.append("gh")
    tools += list(cfg["engines"])
    tools += runners.tools(cfg)
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


AFTER_NODE = "__after_node__"  # npm 이 node 설치 뒤에 생기므로 실행할 때 명령을 다시 고른다


INSTALLING = ("설치", "올림", "설치(선택)")


def plan_tools(cfg: dict, kind: str) -> List[dict]:
    """도구마다 {tool, found, action(건너뜀 · 올림 · 설치 · 설치(선택) · 안내), cmd, hint}.
    자동 설치가 되는 것은 모두 설치 줄로 만든다(동의 한 번). 안내는 자동 방법이 없을 때만."""
    have = lambda name: hl.find_tool(name, kind, hl.tool_extra_paths(name, kind)) is not None  # noqa: E731
    rows = [{"tool": "python", "found": sys.version.split()[0], "action": "건너뜀", "cmd": None,
             "hint": "3.9 이상이라 충분하다" if sys.version_info >= hl.MIN_PYTHON else "3.9 미만"}]
    node_coming = False
    for tool in needed_tools(cfg):
        path, info, ver = detect(tool, kind)
        row = {"tool": tool, "found": info if path else (ver or "없음"), "action": "건너뜀", "cmd": None, "hint": ""}
        if path:
            rows.append(row)
            continue
        if tool == "node" and ver:
            up = node_upgrade_command(kind)
            cmd, hint = up if up else hl.install_command("node", kind, have)
            row.update(action="올림" if cmd else "안내", cmd=cmd, hint=hint)
        else:
            cmd, hint = hl.install_command(tool, kind, have)
            if cmd is None and tool in ("codex", "pnpm") and node_coming:
                cmd, hint = [AFTER_NODE], "node 설치 뒤 npm install -g " + {"codex": "@openai/codex", "pnpm": "pnpm"}[tool]
            action = "설치" if cmd else "안내"
            row.update(action=action, cmd=cmd, hint=hint)
        if tool == "node" and row["action"] in INSTALLING:
            node_coming = True
        rows.append(row)
    if hl.orca_on(cfg) and kind == "windows" and not hl.find_bash(kind):
        # Orca 스크립트(bash)용. Git for Windows 가 있으면 설치 자리에서 찾으므로 여기 오지 않는다
        cmd, hint = hl.install_command("git", kind, have)
        rows.append({"tool": "bash(Git for Windows)", "found": "없음", "action": "설치" if cmd else "안내", "cmd": cmd, "hint": hint})
    return rows


def run_install(cmd: List[str], timeout: int = 1800) -> Tuple[int, str]:
    """설치 명령 하나. __download__ 는 파이썬으로 받는다(Linux Orca AppImage)."""
    if cmd[0] == "__download__":
        url, dest = cmd[1], Path(cmd[2])
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=300) as resp:
                dest.write_bytes(resp.read())
            dest.chmod(0o755)
            return 0, str(dest)
        except (OSError, ValueError) as exc:
            return 1, str(exc)
    exe = shutil.which(cmd[0]) or cmd[0]
    return run([exe] + cmd[1:], timeout=timeout)


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
    todo = [r for r in rows if r["action"] in INSTALLING]
    go = yes or (interactive and todo and ask_yes("\n위 표의 설치 · 올림을 진행할까요", True, on_eof=False))
    if todo and not go:
        say("설치는 건너뛰고 템플릿 받기와 셋업은 계속한다. 빠진 도구는 마지막 「나중에 할 일」에 적는다")
    have = lambda name: hl.find_tool(name, kind, hl.tool_extra_paths(name, kind)) is not None  # noqa: E731
    for r in rows:
        tool = r["tool"]
        if r["action"] == "건너뜀":
            rep.add(f"도구 {tool}", "통과", r["found"])
            continue
        if r["action"] == "안내" or not go:
            rep.add(f"도구 {tool}", "없음", f"{r['action']}: {r['hint']}")
            rep.todo(f"{tool} {'올리기' if r['action'] == '올림' else '설치'}: {r['hint']}")
            if tool == "orca" and r["action"] == "설치":  # 설치할 수 있었는데 안 했다(동의 안 함). 「안내」는 손 설치라 설정을 둔다
                orca_fallback(cfg, rep)
            continue
        cmd, hint = r["cmd"], r["hint"]
        if cmd and cmd[0] == AFTER_NODE:
            cmd, hint = hl.install_command(tool, kind, have)
            if not cmd:
                rep.add(f"도구 {tool}", "없음", "node 가 준비되지 않아 설치하지 못했다")
                rep.todo(f"{tool} 설치: node {hl.MIN_NODE_MAJOR} 을 넣은 뒤 {hint}")
                continue
        say(f"[{r['action']}] {hint}")
        code, out = run_install(cmd)
        if kind == "windows":
            hl.refresh_windows_path()  # winget 설치 뒤 새 창 없이 이어지게
        if tool in ("node",) or cmd[0] == "npm":
            refresh_node_path(kind)
        detected = detect(tool if not tool.startswith("bash") else "git", kind)[0] is not None
        if tool.startswith("bash"):
            detected = hl.find_bash(kind) is not None
        state, detail = hl.install_state(tool, code, detected)
        rep.add(f"도구 {tool}", state, detect(tool, kind)[1] if state == "통과" and not tool.startswith("bash") else detail
                + ("" if state == "통과" or code == 0 else f": {out[-160:]}"))
        if state != "통과":
            rep.todo(f"{tool}: {detail}")
            if tool == "orca":
                orca_fallback(cfg, rep)
    login_step(cfg, kind, rep)


def orca_fallback(cfg: dict, rep: Report) -> None:
    """Orca 를 골랐는데 설치하지 못했다: 없는 도구를 가리키는 하네스를 만들지 않고 기본 방식으로 만든 뒤 알린다."""
    cfg["runner"] = runners.DEFAULT_RUNNER
    rep.todo(f"Orca 를 골랐지만 설치되지 않아 작업자 실행 방식을 {runners.LABELS[runners.DEFAULT_RUNNER]}로 만들었다. "
             f"Orca 를 설치한 뒤 harness.json 의 runner 를 \"orca\" 로 바꾸고 {hl.boot_cmd('generate')} 를 돌린다")


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
