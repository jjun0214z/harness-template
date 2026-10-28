"""작업자 실행 방식(runner) 어댑터: harness.json 의 `runner` 하나가 정한다.

생성기 · 셋업은 Orca 같은 도구를 직접 묻지 않고 여기에 묻는다. 방식마다 들고 있는 것:
- 「맡기는 방법」 표 줄 · 역할 경계 문장 · 핵심 요약 문장 · 정리 명령 문장
- 필요한 도구(셋업 도구 표에 올라간다)
- 더 만들 파일(Orca 면 scripts/orca-*.sh)

| runner | 작업자를 띄우는 곳 | 더 필요한 것 |
| --- | --- | --- |
| subagent (기본) | 서브에이전트 `<키>-worker` (워크트리 격리) | 없음 |
| worktree-session | 해당 저장소의 별도 세션(`claude --worktree` · Codex 앱 워크트리) | 없음 |
| orca | Orca 작업자(`scripts/orca-worker.sh`) | orca CLI · 앱 (Windows 는 Git Bash) |
"""
from typing import Dict, List

RUNNERS = ("subagent", "worktree-session", "orca")
DEFAULT_RUNNER = "subagent"
LABELS = {"subagent": "서브에이전트", "worktree-session": "워크트리 세션", "orca": "Orca"}
CHOICES = {"1": "subagent", "2": "worktree-session", "3": "orca"}  # 셋업 질문의 번호
QUESTION = "작업자 실행 방식 (1) 서브에이전트 (2) 워크트리 세션 (3) Orca"


def of(cfg: dict) -> str:
    return cfg.get("runner") or DEFAULT_RUNNER


def is_orca(cfg: dict) -> bool:
    return of(cfg) == "orca"


def label(cfg: dict) -> str:
    return LABELS[of(cfg)]


def from_choice(answer: str) -> str:
    """질문 답(번호 · 이름) → runner. 모르는 답은 기본값."""
    a = (answer or "").strip().lower()
    return CHOICES.get(a) or (a if a in RUNNERS else DEFAULT_RUNNER)


def tools(cfg: dict) -> List[str]:
    return ["orca"] if is_orca(cfg) else []


def _subagent_parts(engines: List[str]) -> List[str]:
    parts = []
    if "claude" in engines:
        parts.append("Claude: 서브에이전트 `<키>-worker`(워크트리 격리)")
    if "codex" in engines:
        parts.append("Codex: `.codex/agents/<키>-worker` 서브에이전트")
    return parts


def _session_parts(engines: List[str]) -> List[str]:
    parts = []
    if "claude" in engines:
        parts.append("Claude: 해당 저장소에서 `claude --worktree <작업이름>` 새 세션")
    if "codex" in engines:
        parts.append("Codex: 앱에서 해당 저장 프로젝트를 골라 새 작업을 워크트리로")
    return parts


def dispatch_rows(cfg: dict) -> List[str]:
    engines = cfg["engines"]
    review = "서브에이전트 (`researcher` · `reviewer`)"
    r = of(cfg)
    if r == "orca":
        agents = " · ".join(f"`bash scripts/orca-worker.sh --agent {e} <키> <작업이름> @브리프파일`" for e in engines)
        extra = " 또는 Codex 앱에서 저장 프로젝트를 골라 워크트리로 새 작업" if "codex" in engines else ""
        return [f"| Orca 가 켜진 기기 | {agents}{extra} | {review} |",
                f"| Orca 가 없는 곳(클라우드 · 폰) | {' / '.join(_subagent_parts(engines))} | 서브에이전트 |"]
    parts = _session_parts(engines) if r == "worktree-session" else _subagent_parts(engines)
    return [f"| 어디서나 | {' / '.join(parts)} | {review} |"]


def _session_where(engines: List[str]) -> str:
    return " · ".join(w for e, w in (("claude", "`claude --worktree`"), ("codex", "Codex 앱 워크트리")) if e in engines)


def worker_rule(cfg: dict) -> str:
    """CLAUDE.md 「역할과 작업 공간 경계」 첫 줄."""
    r = of(cfg)
    if r == "orca":
        where = "Orca `scripts/orca-worker.sh`" + (" 또는 Codex 앱 저장 프로젝트 워크트리" if "codex" in cfg["engines"] else "")
        return f"- 코드를 고치는 작업자는 해당 저장소의 **격리 워크트리 세션**에서만 일한다({where}).\n"
    if r == "worktree-session":
        return f"- 코드를 고치는 작업자는 해당 저장소의 **격리 워크트리 세션**에서만 일한다({_session_where(cfg['engines'])}).\n"
    return "- 코드를 고치는 작업자는 해당 저장소의 **격리 워크트리**에서만 일한다(서브에이전트 `<키>-worker`, 워크트리 격리).\n"


def core_line(cfg: dict) -> str:
    """core.md(세션마다 들어가는 핵심 요약)의 작업자 줄."""
    r = of(cfg)
    if r == "orca":
        head = "- 작업자는 Orca 로 해당 저장소 워크트리에 띄운다(`scripts/orca-worker.sh`)."
    elif r == "worktree-session":
        head = f"- 작업자는 해당 저장소의 별도 워크트리 세션({_session_where(cfg['engines'])})으로 띄운다."
    else:
        head = "- 작업자는 서브에이전트 `<키>-worker` 로 해당 저장소의 격리 워크트리에 띄운다."
    return head + " 띄우는 도구가 실패하면 조용히 다른 방식으로 바꾸지 않고 알린다."


def cleanup_line(cfg: dict, py: str) -> str:
    tail = f"오래 남은 작업 공간은 `{py} harness/scripts/cleanup_worktrees.py` 로 미리 보고 `--apply`(원격에 다 들어간 것만 지운다)."
    if is_orca(cfg):
        return ("Orca 작업자는 작업자 출력의 `bash scripts/orca-finish-worker.sh <dispatch> <워크트리>`, "
                f"Orca 밖 작업자는 `{py} harness/scripts/finish_worker.py <워크트리>`. " + tail)
    return f"`{py} harness/scripts/finish_worker.py <워크트리>`. " + tail


def layout_rows(cfg: dict) -> str:
    """CLAUDE.md 「하네스 구성」 표에 더할 줄(끝 줄바꿈 포함). 없으면 빈 문자열."""
    return "| `scripts/orca-*.sh` | Orca 작업자 띄우기 · 중간 지시 · 정리 |\n" if is_orca(cfg) else ""


def extra_files(cfg: dict, skeleton) -> Dict[str, object]:
    """{생성할 상대 경로: 원본 경로}."""
    if not is_orca(cfg):
        return {}
    return {f"scripts/{src.name}": src for src in sorted((skeleton / "orca").glob("*.sh"))}


def migrate(raw: dict) -> dict:
    """옛 설정(orca.enabled)을 runner 로 옮긴 새 dict. 이미 runner 가 있으면 orca.enabled 만 걷는다."""
    out = dict(raw)
    orca = out.get("orca")
    enabled = orca if isinstance(orca, bool) else (orca or {}).get("enabled") if isinstance(orca, dict) else None
    if "runner" not in out:
        out["runner"] = "orca" if enabled else DEFAULT_RUNNER
    if isinstance(orca, bool):
        out["orca"] = {}
    elif isinstance(orca, dict) and "enabled" in orca:
        out["orca"] = {k: v for k, v in orca.items() if k != "enabled"}
    return out
