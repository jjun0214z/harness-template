#!/usr/bin/env bash
# 실행 중인 Orca 작업자에게 중간 지시(범위 추가 · 변경 · 취소)를 보낸다. 오케스트레이터용.
# 보낸 것은 큐 적재만 증명한다. 작업자는 체크포인트마다 check 로 읽고, 반영은 worker_done 보고로 확인한다.
# 쉬고 있는 작업자에게는 큐가 닿지 않을 수 있다: 먼저 worker-read 로 상태를 보고, 쉬면 terminal send 로 직접 입력한다.
# 사용: bash scripts/orca-send-worker.sh <dispatch_id | task:<task_id>> <제목> <본문 | @본문파일>
set -uo pipefail
. "$(dirname "$0")/orca-common.sh"
[ $# -eq 3 ] || { sed -n 5,5p "$0"; exit 2; }
target="$1"; subject="$2"; body="$3"
if [ "${body:0:1}" = "@" ]; then
  [ -f "${body:1}" ] || { echo "본문 파일 없음: ${body:1}"; exit 2; }
  body="$(cat "${body:1}")"
fi
[ -n "$body" ] || { echo "본문이 비었다"; exit 2; }
dispatch="$target"
case "$target" in
  task:*) dispatch="$("$ORCA" orchestration dispatch-show --task "${target#task:}" --json | j "(r.get('dispatch') or {}).get('id') or ''")"
          [ -n "$dispatch" ] || { echo "task 의 dispatch 를 찾지 못했다"; exit 1; } ;;
esac
dispatch="${dispatch#dispatch:}"
body="$body

---
[오케스트레이터 중간 지시] 이 지시를 지금 작업 범위에 반영한다. 반영했으면 worker_done 보고 본문에 「중간 지시: ${subject} 반영」을 적는다."
out="$("$ORCA" orchestration send --to "dispatch:$dispatch" --type "${ORCA_SEND_TYPE:-status}" --subject "$subject" --body "$body" --json 2>&1)"
status=$?
echo "$out" | head -c 1500; echo
[ "$status" -eq 0 ] || { echo "보내기 실패(자동 재시도하지 않는다)"; exit "$status"; }
echo "보냄: dispatch:$dispatch · 「${subject}」 (큐 적재만 증명한다)"
echo "  확인: $ORCA orchestration worker-read --dispatch $dispatch --source auto --limit 20 --json"
