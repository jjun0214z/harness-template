"""셋업 자리: 프로젝트 루트 · 하네스 폴더를 정하고 비어 있는지 본다."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import harnesslib as hl  # noqa: E402
from setup_common import TEMPLATE_ROOT  # noqa: E402


def caller_base() -> Path:
    """install · bootstrap 을 실행한 셸의 현재 폴더. install.sh 가 cd 하기 전에 HARNESS_CALLER_CWD 로 넘긴다.
    템플릿 폴더 안에서 실행했으면 템플릿 옆(템플릿 안에 프로젝트를 만들지 않는다)."""
    base = Path(os.environ.get("HARNESS_CALLER_CWD") or os.getcwd()).expanduser().resolve()
    tpl = TEMPLATE_ROOT.resolve()
    if base == tpl or tpl in base.parents:
        return tpl.parent
    return base


def dir_is_empty(path: Path) -> bool:
    try:
        return not [p for p in path.iterdir() if p.name not in (".DS_Store", "desktop.ini", "Thumbs.db")]
    except OSError:
        return False


def project_root(args, cfg: Optional[dict]) -> Path:
    """프로젝트 루트 = --root, 없으면 실행한 폴더 기준:
    - 실행한 폴더가 비어 있거나 이미 이 하네스(<하네스 폴더>/harness.json)가 있을 때만 그 폴더 자체
    - 아니면 <실행한 폴더>/<슬러그> (폴더 이름이 슬러그와 같아도 비어 있지 않으면 섞지 않는다)
    <슬러그> 도 비어 있지 않은 남의 폴더면 root_problem 이 멈춘다. 그 안에 하네스와 새 저장소들."""
    if getattr(args, "root", None):
        return Path(args.root).expanduser().resolve()
    slug = cfg["project"]["slug"] if cfg else "project"
    hdir = cfg["harness_repo"]["dir"] if cfg else "orchestrator"
    base = caller_base()
    if (base / hdir / hl.CONFIG_NAME).is_file() or dir_is_empty(base):
        return base
    return base / slug


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
    allowed = {cfg["harness_repo"]["dir"], ".DS_Store", "desktop.ini", "Thumbs.db", ".work"} | {r["dir"] for r in cfg["repos"]}
    extra = sorted(p.name for p in root.iterdir() if p.name not in allowed)
    if extra:
        return (f"프로젝트 루트 {root} 가 이미 있고 하네스 표시(harness.json)가 없는데 다른 것이 있다({', '.join(extra[:5])}). "
                "섞지 않도록 멈춘다. 빈 폴더에서 다시 실행하거나 --root 로 다른 경로를 준다")
    return None
