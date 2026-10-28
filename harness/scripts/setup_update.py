"""셋업 갱신: 템플릿 최신본의 엔진(harness/)을 받아 다시 생성한다."""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402
from setup_common import run, say  # noqa: E402
from setup_place import resolve_target  # noqa: E402


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
