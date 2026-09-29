"""테스트 공통: 임시 HOME · 가짜 도구(claude · codex · orca · gh) · 로컬 원격 저장소.

이 기기의 실제 ~/.claude · ~/.codex · Orca 를 건드리지 않는다:
- HOME · CODEX_HOME 을 임시 폴더로 바꾼다.
- HARNESS_PATH_ONLY=1 로 PATH 밖(앱 번들 · nvm 폴더)의 도구를 찾지 않게 한다.
- PATH 는 가짜 도구 폴더 + git · python 이 있는 시스템 폴더만 둔다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

TEMPLATE = Path(__file__).resolve().parents[1]
SCRIPTS = TEMPLATE / "harness" / "scripts"
BOOTSTRAP = SCRIPTS / "bootstrap.py"
# 원본 프로젝트 이름 · 호칭 · 이 기기 경로. 이 파일도 공개 검사를 받으므로 조각을 붙여 만든다.
FORBIDDEN = ("gada" + "log", "가다" + "로그", "사장" + "님", "/Us" + "ers/")

FAKE_TOOL = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    name = os.path.basename(sys.argv[0])
    log = os.environ["FAKE_TOOL_LOG"]
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({{"tool": name, "args": sys.argv[1:], "cwd": os.getcwd()}}, ensure_ascii=False) + "\\n")
    args = sys.argv[1:]
    if args[:1] == ["--version"] and name != "node":
        print(name + " 0.0.0-fake"); sys.exit(0)
    if name == "orca" and args[:2] == ["repo", "list"]:
        print(json.dumps({{"result": {{"repos": []}}}})); sys.exit(0)
    if name == "claude" and args[:3] == ["plugin", "marketplace", "list"]:
        state = os.path.join(os.environ["HOME"], ".fake-claude-marketplaces")
        print(open(state, encoding="utf-8").read() if os.path.exists(state) else ""); sys.exit(0)
    if name == "claude" and args[:3] == ["plugin", "marketplace", "add"]:
        state = os.path.join(os.environ["HOME"], ".fake-claude-marketplaces")
        with open(state, "a", encoding="utf-8") as f:
            f.write(os.environ.get("FAKE_SLUG", "") + "\\n")
    if name == "gh" and args[:2] == ["auth", "status"]:
        sys.exit(int(os.environ.get("FAKE_GH_AUTH", "0")))
    if name == "gh" and args[:2] == ["repo", "view"]:
        sys.exit(int(os.environ.get("FAKE_GH_VIEW", "1")))
    if name == "codex" and args[:1] == ["archive"]:
        sys.exit(int(os.environ.get("FAKE_CODEX_ARCHIVE", "0")))
    if name == "orca" and args[:2] == ["orchestration", "worker-show"]:
        print(json.dumps({{"result": {{"dispatch": {{"status": os.environ.get("FAKE_DISPATCH_STATUS", "completed")}}}}}})); sys.exit(0)
    if name == "codex" and args[:2] == ["login", "status"]:
        sys.exit(int(os.environ.get("FAKE_CODEX_LOGIN", "0")))
    if name == "node":
        print(os.environ.get("FAKE_NODE_VERSION", "v22.0.0")); sys.exit(0)
    sys.exit(0)
    ''')


class Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harness-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "tools.log"
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        self.remotes = self.tmp / "remotes"
        self.remotes.mkdir()

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_tools(self, names=("claude", "codex", "orca", "gh")):
        for n in names:
            p = self.bin / n
            p.write_text(FAKE_TOOL.format(python=sys.executable), encoding="utf-8")
            p.chmod(0o755)

    def env(self, **extra):
        sys_dirs = []
        for tool in ("git", "sh"):
            found = shutil.which(tool)
            if found:
                sys_dirs.append(os.path.dirname(found))
        path = os.pathsep.join([str(self.bin)] + list(dict.fromkeys(sys_dirs)))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE", "CODEX", "ORCA", "NVM", "FNM", "PLUGIN_ROOT"))}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), PATH=path, HARNESS_PATH_ONLY="1",
                   FAKE_TOOL_LOG=str(self.log), GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        env.update(extra)
        return env

    def bare_repo(self, name, branch):
        """커밋 하나가 있는 로컬 원격 저장소. clone 을 네트워크 없이 시험한다."""
        bare = self.remotes / f"{name}.git"
        work = self.tmp / f"seed-{name}"
        env = self.env()
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=env)
        subprocess.run(["git", "init", "-q", str(work)], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", branch], check=True, env=env)
        (work / "README.md").write_text(name + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "README.md"], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "push", "-q", str(bare), branch], check=True, env=env)
        subprocess.run(["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", f"refs/heads/{branch}"], check=True, env=env)
        return str(bare)

    def config(self, engines, orca, slug="demo"):
        cfg = {
            "version": 1,
            "project": {"name": "Demo 프로젝트", "slug": slug, "github_org": "demo-org", "owner_title": "대표님"},
            "harness_repo": {"dir": "orchestrator", "remote": "demo-org/orchestrator", "base_branch": "main"},
            "repos": [
                {"key": "web", "dir": "web", "remote": "demo-org/web", "url": self.bare_repo("web", "develop"),
                 "base_branch": "develop", "description": "웹", "deploy": {"develop": "dev 배포", "main": "상용 배포"},
                 "ask_on_push": ["main"], "checks": ["npm test"]},
                {"key": "api", "dir": "api 서버", "remote": "demo-org/api", "url": self.bare_repo("api", "main"),
                 "base_branch": "main", "description": "API", "deploy": {"main": "dev DB 마이그레이션"},
                 "ask_on_push": ["main"], "checks": []},
            ],
            "engines": engines,
            "orca": {"enabled": orca, "workspaces_dir": str(self.tmp / "orca-ws")},
            "platform": {"python": "python3"},
        }
        path = self.tmp / f"harness-{'-'.join(engines)}-{int(orca)}.json"
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def bootstrap(self, *args, input_text=None, **env):
        return subprocess.run([sys.executable, str(BOOTSTRAP), *args], capture_output=True, text=True,
                              env=self.env(**env), input=input_text, timeout=300)

    def tool_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]


def tree_snapshot(root: Path):
    """.git 을 뺀 파일 → 내용 바이트."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts and "__pycache__" not in p.parts:
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out
