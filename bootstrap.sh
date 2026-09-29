#!/usr/bin/env sh
# 하네스 셋업 진입(mac · Linux). python 3.9 이상을 찾아 harness/scripts/bootstrap.py 를 부른다.
# 사용: ./bootstrap.sh [run|check|tools|generate|doctor] [옵션]   (인자 없이 부르면 대화형 run)
here="$(cd "$(dirname "$0")" && pwd)"
export HARNESS_CALLER_CWD="${HARNESS_CALLER_CWD:-$PWD}"   # 프로젝트는 실행한 폴더 아래 <슬러그>/ 에 만든다
# Windows 의 Git Bash 에서는 python3 이 스토어 가짜 실행 파일일 수 있어 py -3 을 먼저 본다(그대로 이 파일에서 돈다)
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) candidates="py3 python python3" ;; *) candidates="python3 python" ;; esac
for py in $candidates; do
  cmd="$py"; extra=""
  [ "$py" = py3 ] && { cmd=py; extra=-3; }
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" $extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    exec "$cmd" $extra "$here/harness/scripts/bootstrap.py" "$@"
  fi
done
echo "python 3.9 이상이 없다. 설치한 뒤 다시 실행한다:"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) echo "  PowerShell 에서: winget install -e --id Python.Python.3.12  (설치 뒤 Git Bash 를 새로 연다)" ;;
  Darwin) echo "  brew install python@3.12   (Homebrew 가 없으면 https://brew.sh, 또는 xcode-select --install)" ;;
  *) echo "  sudo apt install python3   (또는 sudo dnf install python3)" ;;
esac
exit 2
