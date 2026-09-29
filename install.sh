#!/usr/bin/env bash
# 하네스 템플릿 한 줄 설치(macOS · Linux). 막 산 컴퓨터에서도 돈다: 파이썬보다 먼저 도는 순수 셸이다.
#
#   curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash
#   (옵션을 줄 때) curl -fsSL .../install.sh | bash -s -- --yes
#
# 순서: OS 판정 → 감지 표(도구 · 찾은 버전 · 할 일) → 한 번 동의 → 패키지 관리자 · git · python3 · node 22
#       → 템플릿 받기(git clone, git 이 없으면 tar.gz) · 이미 있으면 갱신 → bootstrap.py 실행
# 이미 있는 도구는 건너뛰고, 모자란 버전은 쓰던 관리자(nvm · fnm)로 올린다. 기존 설치는 지우지 않는다.
#
# 옵션: --yes(무인 동의) · --dry-run(할 일만 출력) · --no-run(받기까지만) · --dir <폴더>(템플릿 원본, 기본 ~/.harness-template)
# 프로젝트는 이 명령을 실행한 폴더 아래 <프로젝트 슬러그>/ 에 만든다(bootstrap 의 --root 로 바꿀 수 있다).
#       -- 뒤는 bootstrap.py 로 넘긴다(기본 run).
# 시험용 환경변수: HARNESS_OS(mac|linux) · HARNESS_TEMPLATE_URL · HARNESS_TARBALL_URL · HARNESS_DIR · HARNESS_NO_TTY · HARNESS_PATH_ONLY
#
# 본문 전체가 main 함수 안에 있고 마지막 줄에서만 부른다: curl | bash 로 받을 때 끝까지 받기 전에는 아무것도 실행하지 않는다.
# 자식 명령이 표준 입력(= 파이프로 들어오는 이 스크립트)을 먹지 않도록, 시작하자마자 표준 입력을 터미널(없으면 /dev/null)로 바꾼다.
set -uo pipefail

main() {

TEMPLATE_URL="${HARNESS_TEMPLATE_URL:-https://github.com/jjun0214z/harness-template.git}"
TARBALL_URL="${HARNESS_TARBALL_URL:-https://codeload.github.com/jjun0214z/harness-template/tar.gz/refs/heads/main}"
# 실행한 셸의 현재 폴더: 프로젝트는 여기 아래 <슬러그>/ 에 만든다. 아무것도 하기 전에 적어 둔다
export HARNESS_CALLER_CWD="${HARNESS_CALLER_CWD:-$PWD}"
# 템플릿 원본은 숨김 폴더. 예전 자리(~/harness-template)가 있으면 그것을 그대로 쓴다
if [ -n "${HARNESS_DIR:-}" ]; then DIR="$HARNESS_DIR"
elif [ -d "$HOME/harness-template/.git" ] || [ -f "$HOME/harness-template/.harness-template" ]; then DIR="$HOME/harness-template"; LEGACY=1
else DIR="$HOME/.harness-template"; fi
NODE_MAJOR=22
YES=0; DRY=0; NORUN=0
BOOT_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --no-run) NORUN=1; shift ;;
    --dir) DIR="$2"; shift 2 ;;
    --) shift; BOOT_ARGS=("$@"); break ;;
    *) BOOT_ARGS+=("$1"); shift ;;
  esac
done
[ ${#BOOT_ARGS[@]} -gt 0 ] || BOOT_ARGS=(run)

have() { command -v "$1" >/dev/null 2>&1; }
say() { printf '%s\n' "$*"; }

# Windows 의 Git Bash · MSYS · Cygwin 에서 불렸을 때: 도구 설치(winget)와 경로가 Windows 방식이라 PowerShell 설치(install.ps1)로 넘긴다.
# 실행한 폴더(Windows 경로로 바꿔서) · HARNESS_* 환경변수 · 받은 인자(HARNESS_ARGS)를 그대로 넘긴다.
handoff_windows() {
  PS1_URL="${HARNESS_PS1_URL:-https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1}"
  winpath() { if have cygpath; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
  export HARNESS_CALLER_CWD="$(winpath "$HARNESS_CALLER_CWD")"
  [ -n "${HARNESS_DIR:-}" ] && export HARNESS_DIR="$(winpath "$HARNESS_DIR")"
  [ "$YES" = 1 ] && export HARNESS_YES=1
  [ "$DRY" = 1 ] && export HARNESS_DRY_RUN=1
  [ "$NORUN" = 1 ] && export HARNESS_NO_RUN=1
  export HARNESS_ARGS="${BOOT_ARGS[*]}"
  PS=""
  for c in pwsh.exe pwsh powershell.exe powershell; do have "$c" && { PS="$c"; break; }; done
  if [ -z "$PS" ]; then
    say "Windows 로 보인다($(uname -s 2>/dev/null)). 그런데 PowerShell 을 찾지 못했다. PowerShell 창을 열어 이 한 줄을 실행한다:"
    say "  irm $PS1_URL | iex"
    exit 2
  fi
  say "Windows(Git Bash) 로 보인다. PowerShell 설치로 넘긴다: $PS · 프로젝트 위치 $HARNESS_CALLER_CWD"
  exec "$PS" -NoProfile -ExecutionPolicy Bypass -Command "irm $PS1_URL | iex"
}

TTY=0; ( exec </dev/tty ) 2>/dev/null && TTY=1
[ "${HARNESS_NO_TTY:-0}" = 1 ] && TTY=0   # 시험: 사람 입력 창을 쓰지 않는다
if [ "$TTY" = 1 ]; then exec </dev/tty; else exec </dev/null; fi

case "${HARNESS_OS:-$(uname -s 2>/dev/null)}" in
  mac|Darwin) OS=mac ;;
  linux|Linux) OS=linux ;;
  MINGW*|MSYS*|CYGWIN*|windows) handoff_windows ;;   # Git Bash 등: PowerShell 설치로 넘긴다
  *) say "이 스크립트는 macOS · Linux 용이다. Windows 는 PowerShell 에서:"
     say "  irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex"; exit 2 ;;
esac

# ---------------------------------------------------------------- 감지
git_ok() { have git && git --version >/dev/null 2>&1; }   # mac 의 /usr/bin/git 은 개발자 도구가 없으면 실패한다
PY=""
for c in python3 python; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
done
py_ver() { [ -n "$PY" ] && "$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'; }
node_ver() { have node && node --version 2>/dev/null; }
node_major() { v="$(node_ver)"; v="${v#v}"; printf '%s' "${v%%.*}"; }
NVM_SH="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
BREW=""
BREW_CANDIDATES="brew /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew"
[ "${HARNESS_PATH_ONLY:-0}" = 1 ] && BREW_CANDIDATES="brew"   # 시험: PATH 밖의 실제 brew 를 보지 않는다
for b in $BREW_CANDIDATES; do
  if have "$b" || [ -x "$b" ]; then BREW="$(command -v "$b" 2>/dev/null || echo "$b")"; break; fi
done
PKG=""
if [ "$OS" = linux ]; then
  if have apt-get; then PKG=apt; elif have dnf; then PKG=dnf; fi
fi
SUDO=""
[ "$OS" = linux ] && [ "$(id -u 2>/dev/null || echo 0)" != 0 ] && have sudo && SUDO="sudo "

# 할 일 목록: "도구|찾은 버전|할 일|명령"
PLAN=()
add() { PLAN+=("$1|$2|$3|$4"); }
if [ "$OS" = mac ]; then
  if have xcode-select && xcode-select -p >/dev/null 2>&1; then add "개발자 도구(CLT)" "있음" "건너뜀" "-"
  else add "개발자 도구(CLT)" "없음" "설치" "xcode-select --install"; fi
  if [ -n "$BREW" ]; then add "brew" "있음" "건너뜀" "-"
  else add "brew" "없음" "설치" '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'; fi
else
  if [ -n "$PKG" ]; then add "패키지 관리자" "$PKG" "건너뜀" "-"
  else add "패키지 관리자" "없음" "안내" "apt-get 이나 dnf 가 없다. git · python3 · curl 을 직접 설치한다"; fi
fi
brew_or_pkg() {  # $1 = brew 이름, $2 = apt/dnf 이름
  if [ "$OS" = mac ]; then printf 'brew install %s' "$1"
  elif [ "$PKG" = apt ]; then printf '%sapt-get install -y %s' "$SUDO" "$2"
  elif [ "$PKG" = dnf ]; then printf '%sdnf install -y %s' "$SUDO" "$2"
  else printf ''; fi
}
if git_ok; then add "git" "$(git --version | sed 's/git version //')" "건너뜀" "-"
elif [ "$OS" = mac ]; then add "git" "없음" "함께" "개발자 도구(CLT)와 함께 들어온다"
else c="$(brew_or_pkg git git)"; add "git" "없음" "$([ -n "$c" ] && echo 설치 || echo 안내)" "${c:-git 을 직접 설치한다}"; fi
if [ -n "$PY" ]; then add "python" "$(py_ver)" "건너뜀" "-"
else c="$(brew_or_pkg python@3.12 python3)"; add "python" "없음(3.9 이상 필요)" "$([ -n "$c" ] && echo 설치 || echo 안내)" "${c:-python 3.9 이상을 직접 설치한다}"; fi
if [ "$OS" = linux ] && ! have curl && ! have wget; then
  c="$(brew_or_pkg curl curl)"; add "curl" "없음" "$([ -n "$c" ] && echo 설치 || echo 안내)" "${c:-curl 을 직접 설치한다}"
fi
nm="$(node_major)"
if [ -n "$nm" ] && [ "$nm" -ge "$NODE_MAJOR" ] 2>/dev/null; then add "node" "$(node_ver)" "건너뜀" "-"
else
  act=설치; [ -n "$nm" ] && act=올림
  found="$( [ -n "$nm" ] && node_ver || echo 없음)"
  if [ -f "$NVM_SH" ]; then add "node" "$found" "$act" "nvm install $NODE_MAJOR"
  elif have fnm; then add "node" "$found" "$act" "fnm install $NODE_MAJOR && fnm default $NODE_MAJOR"
  elif [ "$OS" = mac ]; then add "node" "$found" "$act" "brew install node@$NODE_MAJOR"
  else add "node" "$found" "$act" "nvm 설치(https://github.com/nvm-sh/nvm) 뒤 nvm install $NODE_MAJOR"; fi
fi
if [ -d "$DIR/.git" ] || [ -f "$DIR/.harness-template" ]; then add "템플릿" "$DIR" "갱신" "-"
else add "템플릿" "없음" "받기" "$DIR"; fi

if [ "$PKG" = apt ]; then
  for row in "${PLAN[@]}"; do
    case "$row" in *"apt-get install"*) PLAN=("apt 목록|-|갱신|${SUDO}apt-get update -y" "${PLAN[@]}"); break ;; esac
  done
fi

say "하네스 템플릿 설치 ($OS) · 프로젝트는 $HARNESS_CALLER_CWD/<프로젝트 슬러그> 에 만든다"
# 예전 방식(홈 바로 아래)으로 만든 하네스가 있으면 알려 준다: 새로 만들지 말고 그 폴더에서 update
for h in "$HOME"/*/harness.json; do
  [ -f "$h" ] || continue
  say "이미 만든 하네스가 있다: $(dirname "$h"). 템플릿 갱신만이면 그 폴더에서: python3 harness/scripts/bootstrap.py update"
done
[ "${LEGACY:-0}" = 1 ] && say "(예전 자리 $DIR 의 템플릿을 그대로 쓴다. 새로 받으면 ~/.harness-template 에 둔다)"
say ""
say "| 도구 | 찾은 버전 | 할 일 | 명령 |"
say "| --- | --- | --- | --- |"
for row in "${PLAN[@]}"; do
  IFS='|' read -r t f a c <<EOF
$row
EOF
  say "| $t | $f | $a | $c |"
done
say ""

needs=0
for row in "${PLAN[@]}"; do case "$row" in *"|설치|"*|*"|올림|"*|*"|갱신|"*) needs=1 ;; esac; done
if [ "$DRY" = 1 ]; then
  for row in "${PLAN[@]}"; do
    IFS='|' read -r t f a c <<EOF
$row
EOF
    case "$a" in 설치|올림|갱신) say "PLAN $t: $c" ;; esac
  done
  say "PLAN 템플릿: $DIR"
  say "PLAN bootstrap: ${BOOT_ARGS[*]}"
  exit 0
fi
GO=0
if [ "$needs" = 0 ]; then GO=1
elif [ "$YES" = 1 ]; then GO=1
elif [ "$TTY" = 1 ]; then
  printf '위 표의 설치 · 올림을 진행할까요? (Y/n) '
  read -r ans || ans=n
  case "$ans" in ""|y|Y|yes|예|네) GO=1 ;; esac
fi

run() { say "[실행] $*"; bash -c "$*"; }
if [ "$needs" = 1 ] && [ "$GO" = 1 ]; then
  if [ "$OS" = mac ] && ! { have xcode-select && xcode-select -p >/dev/null 2>&1; }; then
    run "xcode-select --install" || true   # 이미 설치 창이 떠 있으면 실패로 끝난다. 아래에서 설치 완료를 기다린다
    say "개발자 도구 설치 창에서 「설치」를 누른다. 끝날 때까지 기다린다(최대 30분)."
    i=0; until xcode-select -p >/dev/null 2>&1; do i=$((i+1)); [ $i -gt 180 ] && { say "개발자 도구 설치를 확인하지 못했다. 설치 뒤 다시 실행한다."; exit 3; }; sleep 10; done
  fi
  if [ "$OS" = mac ] && [ -z "$BREW" ]; then
    [ "$YES" = 1 ] && export NONINTERACTIVE=1
    run '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' || { say "brew 설치 실패"; exit 3; }
    [ -n "$BREW" ] || for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
    [ -n "$BREW" ] || { say "brew 를 설치했는데 찾지 못했다. 새 터미널에서 다시 실행한다"; exit 3; }
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
  fi
  # 새 셸 없이 이어지게 brew 를 PATH 에 올린다
  [ -n "$BREW" ] && eval "$("$BREW" shellenv 2>/dev/null)"
  for row in "${PLAN[@]}"; do
    IFS='|' read -r t f a c <<EOF
$row
EOF
    case "$t|$a" in
      "apt 목록|갱신") run "$c" >/dev/null || { say "apt 목록 갱신 실패"; exit 3; } ;;
      "git|설치"|"python|설치"|"curl|설치") case "$c" in brew*|*apt-get*|*dnf*) run "$c" || { say "$t 설치 실패: $c"; exit 3; } ;; esac ;;
      "node|설치"|"node|올림")
        ok=1
        if [ -f "$NVM_SH" ]; then . "$NVM_SH" && nvm install "$NODE_MAJOR" && nvm alias default "$NODE_MAJOR" >/dev/null || ok=0
        elif have fnm; then { fnm install "$NODE_MAJOR" && fnm default "$NODE_MAJOR"; } || ok=0; eval "$(fnm env 2>/dev/null)"
        elif [ "$OS" = mac ]; then { run "brew install node@$NODE_MAJOR" && PATH="$("$BREW" --prefix "node@$NODE_MAJOR")/bin:$PATH"; } || ok=0
        else
          { run 'curl -fsSL -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash' \
            && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm install "$NODE_MAJOR"; } || ok=0
        fi
        [ "$ok" = 1 ] || say "node $NODE_MAJOR 준비 실패. 하네스 셋업은 계속하고 doctor 가 다시 알려 준다" ;;
    esac
  done
  hash -r 2>/dev/null
  for c in python3 python; do
    if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then PY="$c"; break; fi
  done
elif [ "$needs" = 1 ]; then
  say "설치 · 올림을 건너뛴다(무인 실행은 --yes). 위 표의 명령을 직접 실행한 뒤 다시 부른다."
fi

[ -n "$PY" ] || { say "python 3.9 이상이 없어 멈춘다. 위 표의 python 명령을 실행한 뒤 다시 부른다."; exit 3; }

# ---------------------------------------------------------------- 템플릿 받기 · 갱신
if [ -d "$DIR/.git" ] && git_ok; then
  say "[템플릿] 갱신 $DIR"
  git -C "$DIR" pull --ff-only --quiet || say "갱신 실패(로컬 변경이 있을 수 있다). 있는 것으로 계속한다"
elif git_ok && [ ! -e "$DIR" ]; then
  say "[템플릿] 받기(git clone) $DIR"
  git clone --quiet --depth 1 "$TEMPLATE_URL" "$DIR" || { say "템플릿 clone 실패: $TEMPLATE_URL"; exit 4; }
elif [ ! -e "$DIR" ] || [ -f "$DIR/.harness-template" ]; then
  say "[템플릿] 받기(압축) $DIR"
  tmp="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/harness-template.$$")"; mkdir -p "$tmp"
  if have curl; then curl -fsSL "$TARBALL_URL" -o "$tmp/t.tgz"; else wget -qO "$tmp/t.tgz" "$TARBALL_URL"; fi \
    || { say "템플릿 내려받기 실패: $TARBALL_URL"; exit 4; }
  mkdir -p "$tmp/x" && tar -xzf "$tmp/t.tgz" -C "$tmp/x" || { say "압축 풀기 실패"; exit 4; }
  src="$(ls -d "$tmp"/x/*/ 2>/dev/null | head -n 1)"
  [ -f "$src/.harness-template" ] || { say "받은 것이 템플릿이 아니다"; exit 4; }
  rm -rf "$DIR" && mkdir -p "$(dirname "$DIR")" && mv "$src" "$DIR"
  rm -rf "$tmp"
else
  say "$DIR 가 이미 있는데 템플릿이 아니다. --dir 로 다른 폴더를 준다."; exit 4
fi

[ "$NORUN" = 1 ] && { say "[끝] 템플릿 $DIR (bootstrap 은 부르지 않음)"; exit 0; }
say "[bootstrap] $PY $DIR/harness/scripts/bootstrap.py ${BOOT_ARGS[*]}"
# 표준 입력은 위에서 터미널로 바꿔 두었다: curl | bash 로 불려도 질문에 답할 수 있다(터미널이 없으면 bootstrap 이 --config 를 요구하고 멈춘다)
exec "$PY" "$DIR/harness/scripts/bootstrap.py" "${BOOT_ARGS[@]}"
}

main "$@"; exit $?
