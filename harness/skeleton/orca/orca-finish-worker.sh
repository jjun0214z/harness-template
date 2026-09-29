#!/usr/bin/env bash
# 완료된 Orca 작업자(Claude · Codex) 하나를 정리한다. 본체는 harness/scripts/finish_worker.py(Orca 없을 때도 같은 것을 쓴다).
# 안전 조건(변경 없음 · 원격에 없는 커밋 0 · Dispatch 끝남 · 그 워크트리의 Claude 세션이 모두 idle · 부른 세션 자신의 워크트리 아님)이
# 하나라도 어긋나거나 모르면 멈춘다. 작업 중 · 응답 대기 에이전트는 절대 끝내지 않는다.
# 사용: bash scripts/orca-finish-worker.sh <dispatch_id> <worktree_path> [--dry-run] [--name <작업 이름>]
set -uo pipefail
. "$(dirname "$0")/orca-common.sh"
[ $# -ge 2 ] || { sed -n 5,5p "$0"; exit 2; }
dispatch="$1"; worktree="$2"; shift 2
ORCA_BIN="$ORCA" exec "$PY" "$HARNESS_ROOT/harness/scripts/finish_worker.py" "$worktree" --dispatch "$dispatch" "$@"
