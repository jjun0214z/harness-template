#!/usr/bin/env python3
"""하네스 공통 라이브러리: 설정 읽기 · 검증, OS 판정, 도구 찾기, 설치 명령 고르기, 파일 쓰기.

python3 표준 라이브러리만 쓴다(3.9 이상). macOS · Linux · Windows 에서 같은 코드가 돈다.
설정 한 장(`harness.json`)이 유일한 원본이다. 저장소 목록은 여기에만 있다.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Optional, Tuple

CONFIG_NAME = "harness.json"
TEMPLATE_MARK_FILE = ".harness-template"  # 템플릿 저장소 루트에만 있다. 생성된 하네스에는 복사하지 않는다
TEMPLATE_URL = "https://github.com/jjun0214z/harness-template"
TEMPLATE_REF = "main"
# 저장소 스택 → (필요한 런타임, 작업자 검사 명령 기본값). 빈 저장소라 의존성(pnpm install 등)은 설치하지 않는다.
STACKS = {
    "node": (("node", "pnpm"), ["pnpm lint", "pnpm test"]),
    "python": ((), ["python3 -m pytest -q"]),
    "none": ((), []),
}
MIN_PYTHON = (3, 9)
MIN_NODE_MAJOR = 22
ENGINES = ("claude", "codex")
DEFAULT_LABELS = ("결정대기", "진행중", "하네스", "규칙")
# 규칙 스킬. 절차 뼈대(범용)와 채울 자리(프로젝트 내용) 두 종류.
PROCEDURE_SKILLS = ("absolute-rules", "work-method", "git-rules", "deploy", "task-brief")
FILL_SKILLS = ("code-convention", "security-privacy", "operations", "design")
# 하네스 저장소에서 조용히 흔들리면 안 되는 경로. guard-rules · guard-push 가 같이 쓴다.
DEFAULT_PROTECTED = ("CLAUDE.md", "AGENTS.md", "harness.json", ".claude/", ".claude-plugin/",
                     ".codex/", ".agents/", "plugins/", "harness/")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
REPO_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")  # 저장소 키는 영문 소문자로 시작(윈도우 실측: 「1」 이 키로 들어갔다)
# Orca 공식 설치(https://github.com/stablyai/orca README, 2026-09-29 확인): mac brew cask, Linux releases/latest 의 AppImage.
# Windows 는 README 에 winget 줄이 없고 winget 패키지 StablyAI.Orca(winstall.app · wingetly.io 목록)를 쓴다.
ORCA_BREW_CASK = "stablyai/orca/orca"
ORCA_WINGET_ID = "StablyAI.Orca"
ORCA_APPIMAGE_URL = "https://github.com/stablyai/orca/releases/latest/download/orca-linux.AppImage"
SLUG_RE = KEY_RE
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- 설정

def default_config() -> dict:
    return {
        "version": 1,
        "project": {"name": "", "slug": "", "github_org": "", "owner_title": "대표님"},
        "harness_repo": {"dir": "orchestrator", "remote": "", "base_branch": "main", "source": "new", "path": ""},
        "repos": [],
        "skills": {"fill": list(FILL_SKILLS)},
        "engines": ["claude"],
        "orca": {"enabled": False, "workspaces_dir": "~/orca/workspaces"},
        "labels": list(DEFAULT_LABELS),
        "platform": {"python": ""},
        "template": {"url": TEMPLATE_URL, "ref": TEMPLATE_REF},
    }


def normalize(raw: dict) -> dict:
    """빠진 칸을 기본값으로 채운 새 dict. 검증은 validate 가 한다."""
    cfg = default_config()
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            merged = dict(cfg[key])
            merged.update(value)
            cfg[key] = merged
        else:
            cfg[key] = value
    if isinstance(cfg.get("orca"), bool):
        cfg["orca"] = {"enabled": cfg["orca"], "workspaces_dir": "~/orca/workspaces"}
    org = cfg["project"].get("github_org") or ""
    hr = cfg["harness_repo"]
    if hr.get("source") == "local" and hr.get("path"):
        hr["path"] = str(Path(os.path.expanduser(hr["path"])).resolve())
        hr["dir"] = Path(hr["path"]).name
    if not hr.get("remote") and org:
        hr["remote"] = f"{org}/{hr.get('dir') or 'orchestrator'}"
    repos = []
    for r in cfg.get("repos") or []:
        r = dict(r)
        if r.get("source") == "local" and r.get("path"):
            # 이미 이 컴퓨터에 있는 폴더 연결: 폴더 이름 · 기준 브랜치 · 원격은 그 저장소에서 읽는다(옮기거나 고치지 않는다)
            r["path"] = str(Path(os.path.expanduser(r["path"])).resolve())
            r.setdefault("dir", Path(r["path"]).name)
            r.setdefault("base_branch", detect_base_branch(Path(r["path"])) or "main")
            if not r.get("remote"):
                r["remote"] = github_remote(Path(r["path"])) or ""
            r.setdefault("connect_files", False)
        r.setdefault("dir", r.get("key", ""))
        if not r.get("remote") and org:
            r["remote"] = f"{org}/{r.get('key', '')}"
        r.setdefault("base_branch", "main")
        r.setdefault("description", "")
        r.setdefault("deploy", {})
        r.setdefault("ask_on_push", [])
        r.setdefault("stack", "none")
        r.setdefault("checks", list(STACKS.get(r["stack"], ((), []))[1]))
        r.setdefault("url", "")
        # 기본은 새로 만든다. url 을 적었거나 source=clone 이면 기존 원격을 받는다.
        r.setdefault("source", "clone" if r["url"] else "new")
        r.setdefault("path", "")
        repos.append(r)
    cfg["repos"] = repos
    if not cfg["platform"].get("python"):
        cfg["platform"]["python"] = python_command_name()
    return cfg


def validate(cfg: dict) -> List[str]:
    """문제 목록. 비어 있으면 통과."""
    errs: List[str] = []
    p = cfg.get("project") or {}
    if not (p.get("name") or "").strip():
        errs.append("project.name 이 비었다")
    if not SLUG_RE.match(p.get("slug") or ""):
        errs.append("project.slug 는 영문 소문자·숫자·- 로 시작해야 한다(플러그인 이름이 된다)")
    if not (p.get("owner_title") or "").strip():
        errs.append("project.owner_title(결정권자를 부르는 호칭)이 비었다")
    hr = cfg.get("harness_repo") or {}
    if hr.get("source", "new") not in ("new", "local"):
        errs.append("harness_repo.source 는 new(새로 만들기) 또는 local(기존 폴더 연결)이다")
    if hr.get("source") == "local" and not hr.get("path"):
        errs.append("harness_repo 는 local 인데 path 가 없다")
    if not safe_dir_name(hr.get("dir") or ""):
        errs.append("harness_repo.dir 이 폴더 이름으로 쓸 수 없다")
    if not BRANCH_RE.match(hr.get("base_branch") or ""):
        errs.append("harness_repo.base_branch 가 브랜치 이름이 아니다")
    if hr.get("remote") and not REMOTE_RE.match(hr["remote"]):
        errs.append("harness_repo.remote 는 <조직>/<저장소> 형식이다")
    repos = cfg.get("repos") or []
    # 저장소는 0개여도 된다(나중에 bootstrap.py add-repo 로 더한다)
    seen_keys, seen_dirs = set(), {hr.get("dir")}
    for i, r in enumerate(repos):
        where = f"repos[{i}]"
        key = r.get("key") or ""
        if not REPO_KEY_RE.match(key):
            errs.append(f"{where}.key 는 영문 소문자로 시작하고 소문자 · 숫자 · - 만 쓴다(작업자 이름 <key>-worker 가 된다)")
        if key in seen_keys:
            errs.append(f"{where}.key '{key}' 가 겹친다")
        seen_keys.add(key)
        d = r.get("dir") or ""
        if not safe_dir_name(d):
            errs.append(f"{where}.dir 이 폴더 이름으로 쓸 수 없다")
        if d in seen_dirs:
            errs.append(f"{where}.dir '{d}' 가 겹친다(하네스 폴더 포함)")
        seen_dirs.add(d)
        if not BRANCH_RE.match(r.get("base_branch") or ""):
            errs.append(f"{where}.base_branch 가 브랜치 이름이 아니다")
        if r.get("remote") and not REMOTE_RE.match(r["remote"]):
            errs.append(f"{where}.remote 는 <조직>/<저장소> 형식이다")
        if r.get("source") == "clone" and not r.get("remote") and not r.get("url"):
            errs.append(f"{where} 는 clone 인데 remote(또는 url) 가 없다")
        if r.get("stack") not in STACKS:
            errs.append(f"{where}.stack 은 {', '.join(STACKS)} 중 하나다")
        if r.get("source") not in ("new", "clone", "local"):
            errs.append(f"{where}.source 는 new(새로 만들기) · clone(원격 받기) · local(이 컴퓨터의 폴더 연결) 중 하나다")
        if r.get("source") == "local" and not r.get("path"):
            errs.append(f"{where} 는 local 인데 path(연결할 폴더)가 없다")
        if not isinstance(r.get("deploy"), dict):
            errs.append(f"{where}.deploy 는 {{브랜치: 의미}} 다")
        for k in ("ask_on_push", "checks"):
            if not isinstance(r.get(k), list):
                errs.append(f"{where}.{k} 는 목록이다")
    engines = cfg.get("engines") or []
    if not engines or any(e not in ENGINES for e in engines) or len(set(engines)) != len(engines):
        errs.append("engines 는 [\"claude\"] · [\"codex\"] · [\"claude\", \"codex\"] 중 하나다")
    fill = (cfg.get("skills") or {}).get("fill") or []
    if any(s not in FILL_SKILLS for s in fill):
        errs.append(f"skills.fill 은 {', '.join(FILL_SKILLS)} 중에서 고른다")
    if not isinstance((cfg.get("orca") or {}).get("enabled"), bool):
        errs.append("orca.enabled 는 true/false 다")
    return errs


def safe_dir_name(name: str) -> bool:
    if not name or name in (".", "..") or len(name) > 80:
        return False
    return not any(c in name for c in '/\\:*?"<>|') and name.strip() == name


def load_config(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"설정을 못 읽음: {path} ({exc})") from exc
    if not isinstance(raw, dict):
        raise ConfigError("설정 최상위는 객체다")
    cfg = normalize(raw)
    errs = validate(cfg)
    if errs:
        raise ConfigError("설정 오류:\n- " + "\n- ".join(errs))
    return cfg


def dump_config(cfg: dict) -> str:
    return json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"


def all_dirs(cfg: dict) -> List[str]:
    return [cfg["harness_repo"]["dir"]] + [r["dir"] for r in cfg["repos"]]


def repo_path(repo: dict, parent: Path) -> Path:
    """저장소 폴더. 연결(local)이면 그 경로 그대로(프로젝트 루트 밖이어도 된다), 아니면 프로젝트 루트 아래."""
    if repo.get("source") == "local" and repo.get("path"):
        return Path(repo["path"])
    return Path(parent) / repo["dir"]


def repo_ref(repo: dict) -> str:
    """문서에 적는 자리: 연결이면 절대 경로, 아니면 하네스 옆 ../<폴더>."""
    return repo["path"] if repo.get("source") == "local" and repo.get("path") else f"../{repo['dir']}"


def _git_out(path: Path, *args) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def detect_base_branch(path: Path) -> str:
    """그 저장소의 기본 브랜치: origin/HEAD 가 가리키는 것, 없으면 지금 브랜치."""
    head = _git_out(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head.startswith("origin/"):
        return head[len("origin/"):]
    cur = _git_out(path, "symbolic-ref", "--short", "HEAD")
    return cur


def github_remote(path: Path) -> str:
    url = _git_out(path, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def repo_url(repo: dict) -> str:
    return repo.get("url") or f"https://github.com/{repo['remote']}.git"


def has(cfg: dict, engine: str) -> bool:
    return engine in cfg["engines"]


def orca_on(cfg: dict) -> bool:
    return bool(cfg["orca"]["enabled"])


# ---------------------------------------------------------------- OS

def os_kind(system: Optional[str] = None) -> str:
    """'mac' · 'windows' · 'linux'. system 을 주면 흉내 낸다(테스트용)."""
    s = (system or platform.system()).lower()
    if s == "darwin":
        return "mac"
    if s.startswith("win") or s.startswith("cygwin") or s.startswith("msys"):
        return "windows"
    return "linux"


def python_command_name(kind: Optional[str] = None, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """훅 명령에 쓸 파이썬 이름. Windows 는 python3 가 없고 py · python 만 있는 경우가 많다."""
    kind = kind or os_kind()
    if kind != "windows":
        return "python3"
    for name, cmd in (("python3", "python3"), ("py", "py -3"), ("python", "python")):
        found = which(name)
        if found and "WindowsApps" not in found:  # 스토어 연결용 가짜 실행 파일은 건너뛴다
            return cmd
    return "python"


# ---------------------------------------------------------------- 도구 찾기

def home() -> Path:
    return Path.home()


def node_candidates(kind: str, env: Optional[Dict[str, str]] = None, home_dir: Optional[Path] = None) -> List[Path]:
    """node 실행 파일 후보. PATH 다음에 nvm · nvm-windows · fnm 설치 폴더를 훑는다. 경로를 고정하지 않는다."""
    env = dict(os.environ if env is None else env)
    h = Path(home_dir or home())
    exe = "node.exe" if kind == "windows" else "node"
    out: List[Path] = []
    found = shutil.which("node", path=env.get("PATH"))
    if found:
        out.append(Path(found))
    if path_only():
        return out
    patterns: List[str] = []
    nvm_dir = env.get("NVM_DIR") or str(h / ".nvm")
    patterns.append(os.path.join(nvm_dir, "versions", "node", "*", "bin", exe))
    if kind == "windows":
        for base in filter(None, (env.get("NVM_HOME"), os.path.join(env.get("APPDATA", ""), "nvm") if env.get("APPDATA") else "")):
            patterns.append(os.path.join(base, "v*", exe))
        for base in filter(None, (env.get("FNM_DIR"), os.path.join(env.get("APPDATA", ""), "fnm") if env.get("APPDATA") else "")):
            patterns.append(os.path.join(base, "node-versions", "*", "installation", exe))
    else:
        for base in filter(None, (env.get("FNM_DIR"), str(h / ".fnm"), str(h / ".local" / "share" / "fnm"),
                                  str(h / "Library" / "Application Support" / "fnm"))):
            patterns.append(os.path.join(base, "node-versions", "*", "installation", "bin", exe))
    for pat in patterns:
        for p in sorted(glob.glob(pat), reverse=True):
            out.append(Path(p))
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def path_only() -> bool:
    """HARNESS_PATH_ONLY=1 이면 PATH 밖(앱 번들 · nvm 폴더 등)을 찾지 않는다. 테스트가 이 기기의 실제 도구를 건드리지 않게 한다."""
    return os.environ.get("HARNESS_PATH_ONLY") == "1"


def version_of(cmd: List[str], timeout: int = 10) -> Optional[str]:
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout.strip() or r.stderr.strip()).splitlines()[0] if (r.stdout or r.stderr) else ""


def node_major(version: Optional[str]) -> int:
    m = re.search(r"(\d+)\.", version or "")
    return int(m.group(1)) if m else 0


def find_node(kind: str) -> Tuple[Optional[Path], Optional[str]]:
    """22 이상인 첫 node. 없으면 가장 먼저 찾은 것과 버전(모자라도)."""
    first: Tuple[Optional[Path], Optional[str]] = (None, None)
    for cand in node_candidates(kind):
        v = version_of([str(cand)])
        if v is None:
            continue
        if first[0] is None:
            first = (cand, v)
        if node_major(v) >= MIN_NODE_MAJOR:
            return cand, v
    return first


def find_tool(name: str, kind: str, extra: Iterable[Path] = ()) -> Optional[str]:
    """PATH 먼저, 그다음 흔한 설치 자리. Windows 는 .cmd · .exe 도 본다."""
    found = shutil.which(name)
    if found:
        return found
    if path_only():
        return None
    for p in extra:
        p = Path(os.path.expanduser(str(p)))
        if p.is_file():
            return str(p)
    return None


def tool_extra_paths(name: str, kind: str) -> List[Path]:
    h = home()
    if name == "codex":
        paths = [h / ".local" / "bin" / "codex"]
        if kind == "mac":
            paths.append(Path("/Applications/ChatGPT.app/Contents/Resources/codex"))
        return paths
    if name == "claude":
        return [h / ".local" / "bin" / "claude", h / ".claude" / "local" / "claude"]
    if name == "orca":
        paths = []
        if os.environ.get("ORCA_BIN"):
            paths.append(Path(os.environ["ORCA_BIN"]))
        if kind == "mac":
            paths.append(Path("/Applications/Orca.app/Contents/Resources/bin/orca"))
        elif kind == "windows":
            local = os.environ.get("LOCALAPPDATA")
            if local:
                paths.append(Path(local) / "Programs" / "Orca" / "resources" / "bin" / "orca.cmd")
        return paths
    if name == "brew":
        return [Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew"), Path("/home/linuxbrew/.linuxbrew/bin/brew")]
    return []


# ---------------------------------------------------------------- 설치 명령

def install_command(tool: str, kind: str, have: Callable[[str], bool]) -> Tuple[Optional[List[str]], str]:
    """(실행할 명령 | None, 사람에게 보일 안내). 명령이 None 이면 안내만 한다.

    mac=brew, Windows=winget(없으면 안내), Linux=안내만. claude · codex CLI 는 npm 전역 설치(공식 방법 중 OS 공통).
    """
    if tool in ("claude", "codex"):
        pkg = "@anthropic-ai/claude-code" if tool == "claude" else "@openai/codex"
        if have("npm"):
            return ["npm", "install", "-g", pkg], f"npm install -g {pkg}"
        return None, f"node {MIN_NODE_MAJOR} 을 먼저 설치한 뒤 npm install -g {pkg}"
    if tool == "orca":
        if kind == "mac" and have("brew"):
            return ["brew", "install", "--cask", ORCA_BREW_CASK], f"brew install --cask {ORCA_BREW_CASK}"
        if kind == "windows" and have("winget"):
            cmd = ["winget", "install", "-e", "--id", ORCA_WINGET_ID, "--accept-source-agreements", "--accept-package-agreements"]
            return cmd, "winget install -e --id " + ORCA_WINGET_ID
        if kind == "linux":
            dest = str(Path.home() / ".local" / "bin" / "orca-linux.AppImage")
            return ["__download__", ORCA_APPIMAGE_URL, dest], f"{ORCA_APPIMAGE_URL} 를 {dest} 로 받기"
        return None, "https://github.com/stablyai/orca/releases 에서 받는다"
    if tool == "pnpm":
        if kind == "windows" and have("npm"):
            # corepack enable 은 C:\Program Files\nodejs 에 쓰려다 관리자 권한 오류(EPERM)가 난다(윈도우 실측)
            return ["npm", "install", "-g", "pnpm"], "npm install -g pnpm"
        if have("corepack"):
            return ["corepack", "enable", "pnpm"], "corepack enable pnpm"
        if have("npm"):
            return ["npm", "install", "-g", "pnpm"], "npm install -g pnpm"
        return None, "node 를 먼저 설치한다(pnpm 은 corepack 으로 켠다)"
    brew = {"git": "git", "gh": "gh", "node": f"node@{MIN_NODE_MAJOR}", "python": "python@3.12"}
    winget = {"git": "Git.Git", "gh": "GitHub.cli", "node": "OpenJS.NodeJS.LTS", "python": "Python.Python.3.12"}
    linux = {"git": "sudo apt install git  (또는 dnf install git)",
             "gh": "https://github.com/cli/cli/blob/trunk/docs/install_linux.md",
             "node": f"https://nodejs.org 또는 nvm 으로 node {MIN_NODE_MAJOR}",
             "python": "sudo apt install python3  (또는 dnf install python3)"}
    if tool not in brew:
        return None, f"{tool} 설치 방법을 모른다"
    if kind == "mac":
        if have("brew"):
            return ["brew", "install", brew[tool]], f"brew install {brew[tool]}"
        return None, "Homebrew 를 먼저 설치한다: https://brew.sh 다음 brew install " + brew[tool]
    if kind == "windows":
        if have("winget"):
            cmd = ["winget", "install", "-e", "--id", winget[tool]]
            return cmd, " ".join(cmd)
        return None, f"winget 이 없다. Microsoft Store 의 「앱 설치 관리자」를 설치하거나 직접 받는다({winget[tool]})"
    return None, linux[tool]


# ---------------------------------------------------------------- 파일 쓰기

TEMPLATE_MARK = "<!-- harness:template -->"
BLOCK_BEGIN = "<!-- harness:begin {name} (생성기가 관리한다. 이 블록 안은 고치지 않는다) -->"
BLOCK_END = "<!-- harness:end {name} -->"


def block(name: str, body: str) -> str:
    return BLOCK_BEGIN.format(name=name) + "\n" + body.rstrip("\n") + "\n" + BLOCK_END.format(name=name) + "\n"


def replace_blocks(existing: str, fresh: str) -> str:
    """existing 안의 관리 블록만 fresh 의 같은 이름 블록으로 바꾼다. 블록 밖(사람이 채운 부분)은 둔다."""
    pat = re.compile(r"<!-- harness:begin (\S+)[^>]*-->\n.*?<!-- harness:end \1 -->\n?", re.S)
    fresh_blocks = {m.group(1): m.group(0) for m in pat.finditer(fresh)}
    if not fresh_blocks:
        return existing

    def swap(m: re.Match) -> str:
        new = fresh_blocks.get(m.group(1), m.group(0))
        return new if new.endswith("\n") else new + "\n"
    return pat.sub(swap, existing)


class Writer:
    """생성물을 쓴다. 내용이 같으면 건드리지 않는다(두 번 돌려도 변경 0)."""

    def __init__(self, root: Path, dry_run: bool = False):
        self.root = Path(root)
        self.dry_run = dry_run
        self.changed: List[str] = []
        self.kept: List[str] = []
        self.protect: set = set()     # 사람 파일이라 덮지 않는 경로
        self.first_run = False

    def guard(self, rel: str) -> bool:
        """써도 되나. 처음 생성할 때 이미 있던 파일(사람 것)은 보호 목록에 넣고 쓰지 않는다."""
        if rel in self.protect:
            return False
        if self.first_run and (self.root / rel).exists():
            self.protect.add(rel)
            return False
        return True

    def _write(self, rel: str, text: str, executable: bool = False) -> None:
        path = self.root / rel
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        self.changed.append(rel)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if executable and os_kind() != "windows":
            path.chmod(path.stat().st_mode | 0o111)

    def managed(self, rel: str, text: str, executable: bool = False) -> None:
        """생성기가 주인인 파일: 항상 설정대로 맞춘다."""
        self._write(rel, text, executable)

    def seed(self, rel: str, text: str) -> None:
        """사람이 채우는 파일: 없을 때만 만든다."""
        if (self.root / rel).exists():
            self.kept.append(rel)
            return
        self._write(rel, text)

    def mixed(self, rel: str, text: str) -> None:
        """관리 블록 + 사람 칸이 섞인 파일: 있으면 블록만 바꾼다."""
        path = self.root / rel
        if not path.is_file():
            self._write(rel, text)
            return
        existing = path.read_text(encoding="utf-8")
        if "<!-- harness:begin " in existing:
            self._write(rel, replace_blocks(existing, text))
        elif TEMPLATE_MARK in existing:
            self._write(rel, text)  # 템플릿 저장소의 셋업 전 안내 파일: 통째로 바꾼다
        else:
            # 사람이 쓴 파일인데 블록이 없다: 블록을 앞에 붙이고 원래 내용은 아래에 둔다
            self._write(rel, text.rstrip("\n") + "\n\n## 이전 내용 (생성 전 파일)\n\n" + existing)

    def copy(self, src: Path, rel: str) -> None:
        data = Path(src).read_bytes()
        path = self.root / rel
        same_mode = os_kind() == "windows" or (path.is_file() and (path.stat().st_mode & 0o777) == (Path(src).stat().st_mode & 0o777))
        if path.is_file() and path.read_bytes() == data and same_mode:
            return
        self.changed.append(rel)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if os_kind() != "windows":
            shutil.copymode(src, path)  # 원본 권한 그대로(update 의 copy2 와 같은 결과)


def git_common_parent(start: Path) -> Optional[Path]:
    """start 가 속한 저장소의 본 체크아웃 부모 폴더. 워크트리에서 돌려도 공유 체크아웃 옆을 가리킨다."""
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        return None
    common = Path(out)
    main = common.parent if common.name == ".git" else common
    return main.parent


def refresh_windows_path(env: Optional[Dict[str, str]] = None, winreg_mod=None) -> str:
    """설치 뒤 새 창 없이 이어지게 레지스트리(시스템 + 사용자)의 PATH 를 다시 읽어 지금 PATH 앞에 붙인다."""
    env = os.environ if env is None else env
    try:
        winreg = winreg_mod or __import__("winreg")
    except ImportError:
        return env.get("PATH", "")
    parts = []
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                parts += [os.path.expandvars(p) for p in str(value).split(";") if p]
        except OSError:
            continue
    old = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    merged = list(dict.fromkeys(parts + old))
    env["PATH"] = os.pathsep.join(merged)
    return env["PATH"]


def find_bash(kind: str, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Orca 스크립트용 bash. Windows 는 PATH 에 없어도 Git for Windows 설치 자리를 본다."""
    env = dict(os.environ if env is None else env)
    found = shutil.which("bash", path=env.get("PATH"))
    if found or kind != "windows":
        return found
    for base in filter(None, (env.get("ProgramFiles"), env.get("ProgramW6432"), env.get("ProgramFiles(x86)"),
                              os.path.join(env["LOCALAPPDATA"], "Programs") if env.get("LOCALAPPDATA") else None)):
        for rel in (("Git", "bin", "bash.exe"), ("Git", "usr", "bin", "bash.exe")):
            p = os.path.join(base, *rel)
            if os.path.isfile(p):
                return p
    return None


def boot_cmd(args: str, kind: Optional[str] = None, python: Optional[str] = None) -> str:
    """안내 문구에 넣는 bootstrap 명령을 OS 에 맞게: mac · Linux `python3 harness/scripts/bootstrap.py …`,
    Windows `py -3 harness\\scripts\\bootstrap.py …`(하네스 폴더에서)."""
    kind = kind or os_kind()
    py = python or python_command_name(kind)
    sep = "\\" if kind == "windows" else "/"
    return f"{py} harness{sep}scripts{sep}bootstrap.py {args}".rstrip()


def orca_launch_command(kind: str, env: Optional[Dict[str, str]] = None) -> Optional[List[str]]:
    """설치 뒤 Orca 앱을 한 번 연다(CLI 가 앱에 붙기 때문). mac open -a, Windows Start-Process, Linux AppImage."""
    env = dict(os.environ if env is None else env)
    if kind == "mac":
        return ["open", "-a", "Orca"]
    if kind == "windows":
        local = env.get("LOCALAPPDATA", "")
        exe = os.path.join(local, "Programs", "Orca", "Orca.exe") if local else "Orca.exe"
        return ["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{exe}'"]
    app = Path.home() / ".local" / "bin" / "orca-linux.AppImage"
    return [str(app)] if app.is_file() else None


def install_state(tool: str, code: int, detected: bool) -> Tuple[str, str]:
    """설치 명령 뒤 표에 적을 (상태, 설명). 종료 코드 0 인데 아직 안 보이면 실패가 아니라 새 창에서 확인(윈도우 실측)."""
    if detected:
        return "통과", "설치됨"
    if code == 0:
        return "안내", "설치는 끝났는데 이 창의 PATH 에 아직 없다. 새 터미널에서 다시 확인한다"
    if tool == "pnpm":
        return "없음", "pnpm 설치 실패(치명 아님). 관리자 PowerShell 에서 corepack enable pnpm 또는 npm install -g pnpm"
    return "실패", f"설치 명령 실패(종료 코드 {code})"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)
