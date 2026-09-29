#!/usr/bin/env sh
# 하네스 셋업 진입(mac · Linux). python 3.9 이상을 찾아 harness/scripts/bootstrap.py 를 부른다.
# 사용: ./bootstrap.sh [run|check|tools|generate|doctor] [옵션]   (인자 없이 부르면 대화형 run)
here="$(cd "$(dirname "$0")" && pwd)"
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    exec "$py" "$here/harness/scripts/bootstrap.py" "$@"
  fi
done
echo "python 3.9 이상이 없다. 설치한 뒤 다시 실행한다:"
case "$(uname -s)" in
  Darwin) echo "  brew install python@3.12   (Homebrew 가 없으면 https://brew.sh, 또는 xcode-select --install)" ;;
  *) echo "  sudo apt install python3   (또는 sudo dnf install python3)" ;;
esac
exit 2
