"""셋업 엔진: Claude · Codex 플러그인 설치 · 신뢰 설정, Orca 를 골랐을 때 등록."""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import List, Tuple

import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402
from setup_common import Report, run, say  # noqa: E402


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
            rep.todo(f"claude CLI 설치 뒤 {hl.boot_cmd('run')} 를 다시 돌린다")
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
                if r["source"] == "local" and not r.get("connect_files"):
                    # 연결한 남의 저장소: 동의 없이 그 저장소 설정(settings.local.json)을 바꾸지 않는다
                    rep.todo(f"{r['dir']}: 연결한 저장소라 플러그인을 켜지 않았다. 원하면 그 폴더에서 claude plugin install {slug}@{slug} --scope local")
                    continue
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
            rep.todo(f"codex CLI 설치 뒤 {hl.boot_cmd('run')} 를 다시 돌린다")
        else:
            code, out = run([codex, "plugin", "add", f"{slug}@{slug}-local", "--json"], cwd=target, timeout=180)
            rep.add("Codex 플러그인", "통과" if code == 0 else "실패",
                    f"{slug}@{slug}-local" if code == 0 else f"codex plugin add 실패: {out[-160:]}")


def orca_step(cfg: dict, target: Path, parent: Path, kind: str, rep: Report) -> None:
    if not hl.orca_on(cfg):
        return
    if kind == "windows" and not hl.find_bash(kind):
        rep.add("Orca 스크립트", "안내", "scripts/orca-*.sh 는 bash 가 필요하다(Git for Windows)")
        rep.todo("Git for Windows(bash) 설치: winget install -e --id Git.Git. 없으면 작업자는 서브에이전트 · Codex 앱 워크트리로")
    orca = hl.find_tool("orca", kind, hl.tool_extra_paths("orca", kind))
    if not orca:
        rep.add("Orca 등록", "없음", "orca CLI 를 못 찾음(ORCA_BIN 으로 지정 가능)")
        rep.todo(f"Orca 설치 · 실행 뒤 {hl.boot_cmd('run')} 를 다시 돌린다")
        return
    code, out = run([orca, "repo", "list", "--json"], timeout=30)
    if code != 0:
        # CLI 는 앱에 붙는다: 앱을 한 번 열고 응답할 때까지 기다린다(최대 60초)
        launch = hl.orca_launch_command(kind)
        if launch:
            say(f"[Orca] 앱을 연다: {' '.join(launch)}")
            try:
                subprocess.Popen(launch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        deadline = time.time() + float(os.environ.get("HARNESS_ORCA_WAIT", "60"))
        while time.time() < deadline:
            time.sleep(1)
            code, out = run([orca, "repo", "list", "--json"], timeout=30)
            if code == 0:
                break
        if code != 0:
            rep.add("Orca 등록", "안내", "Orca 앱이 응답하지 않는다")
            rep.todo(f"Orca 를 한 번 열고 {hl.boot_cmd('run', kind)} 를 다시 돌린다(저장소 등록)")
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
