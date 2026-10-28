---
name: setup
description: 하네스 셋업 · 점검 · 갱신. 사용자가 「셋업해」「jjun0214z/harness-template 로 셋업해」「하네스 만들어」「새 기기 세팅」「doctor」「템플릿 갱신」이라고 하면 쓴다. 질문을 대화로 묻고 install · bootstrap 을 단계별로 돌린다. Claude · Codex 공통.
---

# 하네스 셋업 (에이전트 진입)

출발점은 막 산 컴퓨터일 수도, 일부 갖춘 컴퓨터일 수도 있다. **질문은 대화로 묻고, 실행은 스크립트를 단계별로 부른다.**
python 명령은 이 기기에 있는 것을 쓴다(mac · Linux `python3`, Windows `py -3` 또는 `python`). 아래에서는 `PY` 로 적는다.

## 0. 어디서 시작하나
- 템플릿 원본(`~/.harness-template`, 예전 자리 `~/harness-template`. 루트에 `.harness-template` 파일)이 없으면 먼저 받는다. 할 일 표만 보려면:
  - mac · Linux: `curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash -s -- --dry-run`
  - Windows: `$env:HARNESS_DRY_RUN='1'; irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex`
  표를 사용자에게 보여 주고 **동의를 받은 뒤** `--yes --no-run`(Windows 는 `HARNESS_YES=1` · `HARNESS_NO_RUN=1`)으로 설치 · 받기만 한다. 질문은 1번부터 대화로 한다.
  관리자 암호 · 개발자 도구 설치 창처럼 사람 손이 필요한 것은 `! <명령>` 으로 사용자가 직접 치게 안내한다.
- 이미 만든 하네스 폴더(루트에 `harness.json`)면 새로 묻지 않는다. 새 기기면 5번, 템플릿 갱신이면 7번.

## 1. 질문은 4개만 (기본)
1. 프로젝트 이름(기본값 = 사용자가 있는 폴더 이름)
2. 엔진: Claude · Codex · 둘 다(기본값 = 설치 · 로그인돼 있는 것, 둘 다면 Claude)
3. 작업자 실행 방식: 서브에이전트 · 워크트리 세션 · Orca(기본값 = 서브에이전트. `run` 은 `--runner <subagent|worktree-session|orca>`). **Orca 는 사용자가 고를 때만** 설치 · 등록한다. 묻지 않고 켜지 않는다
4. 작업 흐름: 작업 번호 · 저장소별 워크트리 · git 으로 계산하는 상태(`work.py`)를 쓸지(기본값 = 쓴다. `run` 은 `--workflow` · `--no-workflow`)
호칭은 묻지 않고 기본값(주임님)을 쓴다. 사용자가 이미 말했으면 `run` 에 `--title <호칭>` 을 준다.
그다음 요약을 보여 주고 확인받는다: 「여기에 만듭니다: <경로> · 엔진 … · 저장소는 나중에(add-repo) · 작업자 … · 작업 흐름 … · 호칭 …」.
경로 규칙: 폴더가 비어 있거나 이미 이 하네스(`<하네스 폴더>/harness.json`)가 있을 때만 그 폴더 자체, 아니면 `<폴더>/<슬러그>`(이름이 같아도 비어 있지 않으면 섞지 않는다). `<슬러그>` 도 차 있으면 멈추고 다른 경로를 묻는다. `--force` 를 권하지 않는다.
나머지는 기본값(슬러그 = 이름에서, GitHub 없음, 하네스 새로 만들기, 저장소 0개, 스킬 틀 전부). 사용자가 원할 때만 `--detail` 의 전체 질문(GitHub · 호칭 · 하네스 연결 · 저장소 · 스킬 · 작업자 실행 방식)을 한다.

## 2. 설정 파일을 쓴다
답을 `harness.json` 으로 적는다(형식: 템플릿 `examples/harness.example.json`). 사용자에게 보여 주고 확인받는다.

## 3. 검증
`PY harness/scripts/bootstrap.py check --config harness.json` 이 오류 0 이어야 한다.

## 4. 도구 표
`PY harness/scripts/bootstrap.py tools --config harness.json` 표(도구 · 찾은 버전 · 할 일)를 보여 준다. 동의를 받으면 5번에 `--yes` 를 붙인다.

## 5. 끝까지 실행
`PY harness/scripts/bootstrap.py run --config harness.json --root <프로젝트 루트> --non-interactive [--yes] [--create-github]`
- 프로젝트 루트 기본값은 **사용자가 명령을 실행한 폴더 아래 `<슬러그>/`** 다. 사용자에게 「여기에 만듭니다: <경로>」를 보여 주고 확인받은 뒤 `--root` 로 넘긴다. 그 안에 하네스(`<하네스 폴더>/`)와 새 저장소를 만든다.
- 저장소는 새로 만든다(`git init` · 뼈대 · 첫 커밋). 이미 있는 폴더는 덮지 않는다.
- `--create-github` 은 gh 로그인이 있을 때만 비공개 원격 · 라벨을 만든다. 사용자에게 먼저 묻는다. 없으면 「나중에 할 일」에 연결 명령이 남는다.

## 6. 점검 표
`PY harness/scripts/bootstrap.py doctor --target <프로젝트 루트>/<하네스 폴더>` 표와 「나중에 할 일」을 그대로 보여 준다. 로그인(`claude` 첫 실행 · `! codex login` · `! gh auth login`)은 사용자가 직접 한다.

## 7. 템플릿 갱신
하네스 폴더에서 `PY harness/scripts/bootstrap.py update`. 바뀐 파일을 `git diff` 로 보여 주고 경로를 명시해 커밋한다.

## 7-1. 저장소 추가 (「저장소 추가해」「레포 연결해」)
하네스 폴더에서 저장소 하나를 대화로 물어(키 · 새로 만들기 / 원격 받기 / 이 컴퓨터 폴더 연결 · 폴더 · 브랜치 · 스택 …) JSON 조각으로 적고:
`PY harness/scripts/bootstrap.py add-repo --repo <조각.json> [--create-github]`
harness.json 에 더하고, 그 저장소만 준비(새로 만들기 · 받기 · 연결) → 작업자 · 저장소 지도 생성 → 그 저장소에만 플러그인 · 신뢰 · (Orca 를 골랐으면) Orca 등록을 한다. 같은 키로 다시 불러도 안전하다.
사용자가 터미널에서 직접 하려면 `PY harness/scripts/bootstrap.py add-repo` (대화형). 끝나면 바뀐 하네스 파일을 경로를 명시해 커밋한다.
끝나고 반드시 알린다: **새 작업자 `<키>-worker` 는 지금 열려 있는 세션에서는 보이지 않는다.** 에이전트 목록은 세션이 시작할 때 읽히므로 세션(claude · codex)을 닫고 다시 열어야 쓸 수 있다.

## 7-2. 정리 명령 허용 규칙 (Claude `/permissions`, 선택)
작업 정리(`deploy` 스킬 「작업 공간 · 앱 세션 정리」)는 워크트리 삭제 · 남은 프로세스 끄기 · 임시 폴더 지우기를 한다. 자동 승인 모드가 이것을 파괴적 명령으로 보고 막으면 정리가 사용자 터미널로 넘어간다.
아래 문자열을 사용자가 `/permissions` → Allow 에 하나씩 넣는다(에이전트는 설정 파일을 직접 고치지 않는다). 하네스 폴더에서 부르는 꼴이고, `python3` 은 이 기기의 파이썬 명령으로 바꾼다.
```
Bash(python3 harness/scripts/finish_worker.py:*)
Bash(python3 harness/scripts/cleanup_worktrees.py:*)
Bash(python3 harness/scripts/cleanup_temp.py:*)
Bash(bash scripts/orca-finish-worker.sh:*)
Bash(orca orchestration worker-release:*)
Bash(orca worktree rm:*)
```
- 프로세스 끄기는 정리 스크립트 안에서 정해진 것만 끈다. `Bash(kill:*)` · `Bash(pkill:*)` 처럼 넓게 허용하지 않는다. 휴지통 비우기 · `rm -rf` 일반 허용도 넣지 않는다.
- Claude 샌드박스가 켜져 있으면 `codex archive` 가 파일 시각 설정에서 실패할 수 있다. 허용 규칙으로는 풀리지 않으니 정리 출력의 `다시 보관:` 명령을 사용자 터미널에서 돌린다.

## 하지 말 것
- 사용자 동의 없이 도구 설치 · GitHub 저장소 생성 · 전역 설정 변경을 하지 않는다.
- 템플릿 폴더 자체에 하네스를 만들지 않는다.
- 확인하지 않은 것을 됐다고 말하지 않는다. doctor 표가 근거다.
