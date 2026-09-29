#!/usr/bin/env bash
# Orca 스크립트 공통: orca 실행 파일 찾기 · harness.json 읽기. 다른 orca-*.sh 가 source 한다.
# 경로를 고정하지 않는다: ORCA_BIN → PATH 의 orca → 운영체제별 기본 설치 자리 순서.
HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${HARNESS_PYTHON:-$(command -v python3 || command -v python || echo python3)}"
find_orca() {
  if [ -n "${ORCA_BIN:-}" ]; then echo "$ORCA_BIN"; return; fi
  if command -v orca >/dev/null 2>&1; then command -v orca; return; fi
  for c in "/Applications/Orca.app/Contents/Resources/bin/orca" "${LOCALAPPDATA:-}/Programs/Orca/resources/bin/orca.cmd"; do
    [ -x "$c" ] && { echo "$c"; return; }
  done
  echo "orca"
}
ORCA="$(find_orca)"
# harness.json 조회: cfg '<파이썬 식>' (c = 설정 dict)
cfg() { "$PY" -c "import json,sys; c=json.load(open(sys.argv[1],encoding='utf-8')); print($1)" "$HARNESS_ROOT/harness.json"; }
# 저장소 키 또는 폴더 → "폴더 기준브랜치". 하네스 자신은 key 'harness' 또는 폴더 이름.
repo_info() {
  "$PY" - "$HARNESS_ROOT/harness.json" "$1" <<'PYEOF'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8")); want = sys.argv[2]
h = c["harness_repo"]
rows = [("harness", h["dir"], h.get("base_branch", "main"))] + [(r["key"], r.get("dir") or r["key"], r.get("base_branch", "main")) for r in c["repos"]]
for key, d, base in rows:
    if want in (key, d):
        print(d, base); break
else:
    sys.exit(1)
PYEOF
}
j() { "$PY" -c "import json,sys; d=json.load(sys.stdin); r=d.get('result') or d.get('error') or {}; print($1)"; }
