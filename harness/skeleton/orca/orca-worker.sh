#!/usr/bin/env bash
# Orca 작업자 세션을 띄워 과제를 맡긴다. 저장소에 새 워크트리 + 고른 에이전트 세션 하나를 만든다.
# - 기준 브랜치는 harness.json 저장소 목록을 따른다. 하네스 자체 작업은 'harness' 또는 하네스 폴더 이름.
# - worker-start 결과가 불명확해도 자동 재실행하지 않는다. 기존 Dispatch 권한을 회수하면 일하는 작업자가 보고할 수 없다.
# - 모델은 고정하지 않는다. 필요할 때만 ORCA_CLAUDE_MODEL · ORCA_MODEL 로 Claude 에 override 한다.
#
# 사용: bash scripts/orca-worker.sh [--agent claude|codex] [--base <ref>] <저장소 키|폴더> <작업이름> <과제 글 | @파일>
#       ORCA_RUN=<run_id> 를 주면 그 실행(run)에 과제를 붙인다. 없으면 새로 만든다.
set -uo pipefail
. "$(dirname "$0")/orca-common.sh"

agent="${ORCA_AGENT:-}"; base_ref="${ORCA_BASE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --agent) [ $# -ge 2 ] || { echo "--agent 뒤에 claude 또는 codex 가 필요하다"; exit 2; }; agent="$2"; shift 2 ;;
    --base) { [ $# -ge 2 ] && [ -n "$2" ]; } || { echo "--base 뒤에 기준 ref 가 필요하다"; exit 2; }; base_ref="$2"; shift 2 ;;
    *) break ;;
  esac
done
engines="$(cfg "' '.join(c['engines'])")"
[ -n "$agent" ] || { [ -n "${CODEX_SESSION_ID:-${CODEX_THREAD_ID:-}}" ] && agent=codex || agent="${engines%% *}"; }
case " $engines " in *" $agent "*) ;; *) echo "이 하네스가 쓰지 않는 에이전트: $agent (harness.json engines: $engines)"; exit 2 ;; esac
[ $# -eq 3 ] || { sed -n 7,8p "$0"; exit 2; }
repo="$1"; name="$2"; spec="$3"
[ "${spec:0:1}" = "@" ] && spec="$(cat "${spec:1}")"
info="$(repo_info "$repo")" || { echo "모르는 저장소: $repo (harness.json 에 없다)"; exit 2; }
read -r dir base_name local_path <<< "$info"; base="origin/$base_name"
spec="$spec

---
[작업자 공통] 배정된 $dir 저장소의 이 워크트리에서만 일한다. 고쳤으면 커밋까지 하고 push 하지 않는다(오케스트레이터가 검토 뒤 올린다).
끝나면 $ORCA orchestration send --type worker_done --outcome succeeded --subject \"<한 줄 요약>\" --body \"<바꾼 파일 · 검증 명령과 결과 · 커밋 SHA>\" --task-id <task_id> --dispatch-id <dispatch_id> --from <handle> 로 보고한다(실패면 --outcome failed).
중간 지시 확인: 커밋마다, 테스트를 돌린 뒤, worker_done 직전에 $ORCA orchestration check --terminal <handle> --json 으로 새 지시를 읽고 따른다. consumer_fenced 가 나오면 멈춘다.
막히면 $ORCA orchestration send --type escalation --subject \"<무엇이 막혔나>\" --body \"<선택지>\" 로 묻는다."
if [ -n "$base_ref" ]; then
  common="$(git -C "$HARNESS_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$HARNESS_ROOT/.git")"
  projects="${ORCA_PROJECTS_DIR:-$(cd "$(dirname "$common")/.." && pwd)}"
  repo_dir="${local_path:-$projects/$dir}"   # 연결한 저장소는 그 경로 그대로
  git -C "$repo_dir" rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null || {
    echo "기준 ref 없음: $repo_dir 에 '$base_ref' 가 없다. 멈춘다."; exit 2; }
  base="$base_ref"
fi

coord="${ORCA_TERMINAL_HANDLE:-}"
[ -n "$coord" ] || coord="$("$ORCA" terminal list --json | j "next((t['handle'] for t in (r.get('terminals') or []) if t.get('title')=='coordinator' and t.get('worktreePath')=='$HARNESS_ROOT'), '')")"
[ -n "$coord" ] || coord="$("$ORCA" terminal create --worktree "path:$HARNESS_ROOT" --title coordinator --json | j "r['terminal']['handle']")"

run="${ORCA_RUN:-}"
if [ -z "$run" ]; then
  out="$("$ORCA" orchestration run-create --objective "$dir: $name" --from "$coord" --json 2>&1)"
  run="$(echo "$out" | j "r.get('runId') or (r.get('run') or {}).get('id') or ''")"
  [ -n "$run" ] || { echo "run 생성 실패: $out" | head -c 1500; exit 1; }
fi
model_args=()
claude_model="${ORCA_CLAUDE_MODEL:-${ORCA_MODEL:-}}"
[ -n "$claude_model" ] && [ "$agent" = claude ] && model_args=(--model "$claude_model")
out="$("$ORCA" orchestration worker-start --spec "$spec" --task-title "$dir: $name" --display-name "$dir: $name" \
  --worktree new-top-level --repo "name:$dir" --base-branch "$base" --name "$name" --agent "$agent" \
  ${model_args[@]+"${model_args[@]}"} --run "$run" --from "$coord" --json 2>&1)"
start_status=$?
task="$(echo "$out" | j "r.get('taskId','')")"
dispatch="$(echo "$out" | j "r.get('dispatchId','')")"
[ -n "$task" ] || { echo "작업자 시작 실패: $out" | head -c 1500; exit 1; }
term="$("$ORCA" orchestration dispatch-show --task "$task" --json | j "r['dispatch']['assignee_handle']")"
wt="$("$ORCA" terminal list --json | j "next((t['worktreePath'] for t in (r.get('terminals') or []) if t['handle']=='$term'), '')")"
cat <<OUT
작업자: $dir / $name · 에이전트 $agent · 기준 $base
  run $run · task $task · dispatch $dispatch · terminal $term
  워크트리 $wt
보고 기다리기:
  $ORCA orchestration check --wait --types worker_done,escalation,question --terminal $coord --timeout-ms 1800000 --json
중간 지시 보내기:
  bash $HARNESS_ROOT/scripts/orca-send-worker.sh $dispatch "<제목>" @본문파일
검토 · push 뒤 정리:
  bash $HARNESS_ROOT/scripts/orca-finish-worker.sh $dispatch "$wt"
OUT
if [ "$start_status" -ne 0 ]; then
  echo "worker-start 가 준비 완료를 증명하지 못했다. 자동 재실행하지 않는다. 확인:" >&2
  echo "  $ORCA orchestration worker-show --dispatch $dispatch --json" >&2
  exit "$start_status"
fi
