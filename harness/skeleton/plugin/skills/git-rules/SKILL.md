---
name: git-rules
description: Git · 커밋 · 브랜치 규칙(저장소별 기준 브랜치 · 배포 승인 · 워크트리 작업 · 공유 체크아웃 금지 명령 · 커밋 메시지 형식 · 브랜치 수명). 커밋 · push · 머지 · 배포 · 브랜치 작업 전에 읽는다.
---

# Git · 커밋 규칙

## 1. 불변식
- 강제 push 금지(플러그인 push 가드가 막는다). 이력 수술(rebase · reset --hard · --amend)은 공유 체크아웃에서 하지 않는다.
- push 는 오케스트레이터만 한다. 작업자는 커밋까지.

## 2. 저장소별 기준 브랜치
{{repo_table}}

## 3. 작업 공간
- 과제마다 워크트리: `git -C <저장소> worktree add <프로젝트 폴더>/.work/<저장소>-<슬러그> -b <슬러그> origin/<기준>`.
- 공유 체크아웃에서 `git add -A` · `git commit -a` · `switch` 하지 않는다. add 는 경로를 명시한다.

## 4. 커밋 메시지
- `type(scope): 한글 요약`. type = feat · fix · docs · chore · refactor. 보안 수정은 단독 커밋.
<!-- 채울 자리: scope 목록, 이슈 번호 붙이는 방식 -->

## 5. push 전
- `git log --oneline origin/<기준>..<밀 SHA>` 로 내가 미는 커밋만 있는지 잰다. 없거나 · 많거나 · 비면 멈춘다.

## 6. 브랜치 수명
- 머지된 원격 브랜치는 그 자리에서 지운다. 작업 공간은 `{{py}} harness/scripts/cleanup_worktrees.py` 로 치운다.

## 확인 필요
<!-- 채울 자리: 릴리스 · 핫픽스 · 버전 규칙 -->
