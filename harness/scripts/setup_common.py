"""셋업 공통: 보고 표 · 명령 실행 · 출력 · 질문 입력."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # 형제 모듈 import 가 __pycache__ 를 하네스 저장소에 남기지 않게 한다
sys.path.insert(0, str(HERE))
import harnesslib as hl  # noqa: E402
import generate as gen  # noqa: E402

TEMPLATE_ROOT = gen.TEMPLATE_ROOT


class Report:
    """doctor 표 줄 모음과 「나중에 할 일」. 상태는 통과 · 실패 · 없음 · 건너뜀 · 안내."""

    def __init__(self):
        self.rows: List[Tuple[str, str, str]] = []
        self.todos: List[str] = []
        self.no_identity: List[str] = []  # git 이름 · 메일이 없어 첫 커밋을 못 한 저장소
        self._identity_at: Optional[int] = None

    def add(self, item: str, state: str, detail: str = "") -> None:
        self.rows.append((item, state, detail))

    def todo(self, text: str) -> None:
        if text not in self.todos:
            self.todos.append(text)

    def identity_todo(self, label: str) -> None:
        """이름 · 메일이 없어 첫 커밋을 미룬 저장소. 원인이 하나라 할 일도 하나로 모은다(저장소 이름만 늘어난다).
        파일 목록 · add 명령은 넣지 않는다: 신원을 넣고 run 을 다시 돌리면 하네스가 생성한 파일만 골라 커밋한다."""
        if label not in self.no_identity:
            self.no_identity.append(label)
        text = "\n".join([
            f"첫 커밋(대상 저장소: {', '.join(self.no_identity)}): git 이름 · 메일이 없어 만들지 않았다. 아래 두 줄로 신원을 넣는다",
            '   git config --global user.name "<이름>"',
            '   git config --global user.email "<메일>"',
            f"   그 뒤 하네스 폴더에서 같은 명령({hl.boot_cmd('run')})을 다시 실행하면 하네스가 생성한 파일만 담아 스스로 첫 커밋을 만든다",
        ])
        if self._identity_at is None:
            self.todos.append(text)
            self._identity_at = len(self.todos) - 1
        else:
            self.todos[self._identity_at] = text

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
