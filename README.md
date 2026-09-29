<!-- harness:template -->
# 하네스 템플릿

```sh
curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash
```
```powershell
irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex
```

위 한 줄이면 끝이다(macOS · Linux 는 터미널, Windows 는 PowerShell). 내려받기 · 압축 풀기 · GitHub 계정 · gh 는 필요 없다.
막 산 컴퓨터에서도 돈다: 먼저 무엇이 있고 없는지 표로 보여 주고, 한 번 동의받은 뒤 필요한 것만 설치한다.

에이전트로 할 때: Claude 또는 Codex 에게 **「jjun0214z/harness-template 로 셋업해」**.

<details><summary>다른 방법 (gh 가 있거나, 무인으로)</summary>

```sh
# gh 가 있으면 (보조 경로)
gh repo clone jjun0214z/harness-template ~/harness-template && bash ~/harness-template/bootstrap.sh
# git 만 있으면
git clone https://github.com/jjun0214z/harness-template ~/harness-template && bash ~/harness-template/bootstrap.sh
# 무인(설치 동의 포함)
curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash -s -- --yes
```
```powershell
gh repo clone jjun0214z/harness-template $HOME\harness-template; & $HOME\harness-template\bootstrap.ps1
$env:HARNESS_YES='1'; irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex
```
</details>

## 무엇을 하나

새 프로젝트의 **오케스트레이터 · 작업자 · 규칙 스킬 틀 · 안전 훅 · 이슈 추적 · 기록**을 한 번에 세팅한다.
Claude · Codex(또는 둘 다), Orca 사용 여부를 고를 수 있고 macOS · Linux · Windows 에서 같은 코드(python3 표준 라이브러리)가 돈다.

| 단계 | 누가 | 무엇 |
| --- | --- | --- |
| 1 | `install.sh` · `install.ps1` (파이썬보다 먼저 도는 셸) | 감지 표 → 동의 → 패키지 관리자(mac: 개발자 도구 · brew, Windows: winget, Linux: apt · dnf) · git · python 3.9+ · node 22 → 템플릿을 `~/harness-template` 에 받기(git 이 없으면 압축으로, 있으면 갱신) |
| 2 | `bootstrap.py run` | 네 묶음 질문(프로젝트 · 저장소 · 규칙 스킬 · 엔진/Orca) → 도구 표 · 동의(pnpm · 고른 엔진 CLI) → 로그인 확인 |
| 3 | 〃 | 하네스 저장소와 코드 저장소를 **새로 만든다**(`git init -b <기준 브랜치>` · 최소 뼈대 · 첫 커밋). 기존 원격이 있으면 clone 을 고를 수 있다 |
| 4 | 〃 | 생성기 → 고른 엔진만 플러그인 설치 → (gh 로그인이 있고 원하면) 비공개 원격 저장소 · 라벨 → Orca 를 골랐으면 등록 |
| 5 | 〃 | doctor 점검 표 + **나중에 할 일**(로그인 · 원격 연결 · 첫 커밋처럼 사람 손이 필요한 것) |

이미 있는 도구 · 로그인 · 플러그인 · 등록은 건너뛴다. 모자란 버전은 쓰던 관리자(nvm · fnm · nvm-windows)로 올리고 기존 설치는 지우지 않는다.
`~/.codex/config.toml` 같은 사용자 설정은 덮지 않고 필요한 항목만 백업 뒤 덧붙인다. 두 번 돌려도 안전하다.

gh · GitHub 로그인은 **선택**이다. 없으면 로컬 하네스만 만들고 「나중에 할 일」에 원격 연결 명령을 적는다.

## 템플릿과 새 하네스

| | 어디 | 역할 |
| --- | --- | --- |
| 템플릿 | `~/harness-template` (이 저장소) | 원본. 여기에 하네스를 만들지 않는다 |
| 새 프로젝트 하네스 | 질문에서 고른 폴더(기본: 템플릿 옆 `orchestrator`) | 생성물. 코드 저장소는 그 옆 폴더에 |

템플릿이 갱신되면 하네스 폴더에서 `python3 harness/scripts/bootstrap.py update` (Windows 는 `py -3 ...`). 엔진(`harness/`)만 새로 가져와 다시 생성하고, 사람이 채운 스킬 본문 · 상황판 · 「프로젝트 메모」는 덮지 않는다.
**주의:** 사람이 고친 규칙 스킬(absolute-rules 등)은 update 가 덮지 않으므로 템플릿의 규칙 뼈대가 바뀌어도 자동으로 들어오지 않는다. 템플릿 규칙 갱신은 `harness/skeleton/plugin/skills/` 와 직접 비교해 옮긴다.

## 설정 한 장 (`harness.json`)

```json
{
  "project": {"name": "Acme 여행", "slug": "acme", "github_org": "acme-inc", "owner_title": "대표님"},
  "harness_repo": {"dir": "orchestrator", "base_branch": "main"},
  "repos": [
    {"key": "web", "base_branch": "develop", "stack": "node", "description": "서비스 본체",
     "deploy": {"develop": "dev 배포", "main": "상용 배포"}, "ask_on_push": ["main"]},
    {"key": "legacy", "source": "clone", "url": "https://github.com/acme-inc/legacy.git"}
  ],
  "engines": ["claude", "codex"],
  "orca": {"enabled": false}
}
```

빈 칸은 기본값으로 채운다: `dir` = `key`, `remote` = `<github_org>/<key>`(조직이 없으면 로컬만), `source` = `new`(url 을 적으면 `clone`),
`stack`(`node` · `python` · `none`)에 따라 필요한 런타임과 작업자 검사 명령 기본값. 빈 저장소라 의존성(pnpm install 등)은 설치하지 않는다.
전체 예시: `examples/harness.example.json`. 설정만 있으면 `./bootstrap.sh run --config harness.json --yes --non-interactive`.

## 무엇이 생기나

| 생성물 | 성격 | 다시 생성하면 |
| --- | --- | --- |
| `CLAUDE.md` | 오케스트레이터 지침 · 저장소 지도 · 맡기는 방법 | 관리 블록만 바뀌고 「프로젝트 메모」는 그대로 |
| `AGENTS.md` | Codex 진입점(CLAUDE.md 를 읽게만) | 설정대로 |
| `plugins/<슬러그>/` | 공통 플러그인: push 가드 훅 · 핵심 요약 · 작업자(`<키>-worker`) · 조사원 · 검토원 · `setup` 스킬 | 설정대로 (`core.md` 는 관리 블록만) |
| `plugins/<슬러그>/skills/` | 절차 뼈대 5개(absolute-rules · work-method · git-rules · deploy · task-brief) + 채울 자리 틀(code-convention · security-privacy · operations · design 중 고른 것) | **없을 때만 만든다** |
| `.claude/settings.json` · `.claude-plugin/marketplace.json` | Claude 를 골랐을 때 | 설정대로 |
| `.codex/hooks.json` · `.codex/agents/*.toml` · `.agents/plugins/marketplace.json` | Codex 를 골랐을 때 | 설정대로 |
| `scripts/orca-*.sh` | Orca 를 골랐을 때(bash) | 설정대로 |
| `harness/` | 셋업 엔진 · 생성기 · 저장소 훅(규칙 보호 · 하네스 동기화) · 작업 공간 정리 | `update` 로 템플릿에서 |
| `상황판.md` · `docs/기록/README.md` | 지금 집중 한 장 · 근거 기록 규칙 | 없을 때만 |
| 코드 저장소마다 | `README.md` · `.gitignore` · `CLAUDE.md` · `AGENTS.md` · 플러그인을 켜는 `.claude/settings.json` + 첫 커밋 | 이미 있으면 건드리지 않는다 |

## 작업 정리 (오프라인 = 끝난 작업)

작업자가 끝나면 `python3 harness/scripts/finish_worker.py <워크트리>` (Orca 작업자는 `scripts/orca-finish-worker.sh <dispatch> <워크트리>`).
변경 없음 · 원격에 없는 커밋 0 · 그 작업자가 작업 중/응답 대기가 아님 · 부른 세션 자신의 워크트리가 아님을 전부 확인하고, 하나라도 모르면 멈춘다(`--dry-run` 으로 미리 보기).
Claude 세션은 정상 종료만 하고(안 끝나면 멈추고 보고), Codex 세션은 `codex archive` 로 보관한다. Claude 앱 · 웹 · 폰의 원격 세션 목록은 스크립트로 보관할 공개 방법이 없어,
오케스트레이터가 브라우저 도구로 claude.ai/code 를 열어 작업 이름이 맞고 오프라인인 세션만 보관한다(생성되는 `deploy` 스킬 「작업 공간 · 앱 세션 정리」).

## Orca 가 없을 때

작업자는 서브에이전트(`<키>-worker`, 워크트리 격리) · `claude --worktree` · Codex 앱 저장 프로젝트 워크트리로 띄운다. `CLAUDE.md` 「맡기는 방법」 표가 고른 조합대로 만들어진다.

## 운영체제별 차이

| 무엇 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 한 줄 설치 | `install.sh` | `install.sh` | `install.ps1` |
| 패키지 관리자 | 개발자 도구(`xcode-select --install`) + brew | apt · dnf (sudo) | winget (없으면 App Installer 안내) |
| node 22 | 쓰던 nvm · fnm, 없으면 brew `node@22` | 쓰던 nvm · fnm, 없으면 nvm 설치 | 쓰던 nvm-windows · fnm, 없으면 winget |
| 설치 뒤 PATH | 같은 셸에서 brew shellenv · node 경로를 올린다 | 같음 | 레지스트리에서 PATH 를 다시 읽는다 |
| 훅 파이썬 | `python3` | `python3` | 찾은 것(`py -3` · `python`), 스토어 가짜 실행 파일은 건너뜀 |
| 심링크 · 줄바꿈 | 쓰지 않는다(복사) · LF | 같음 | 같음(`.ps1` 만 CRLF) |
| Orca | 앱 · CLI | 앱(AppImage) | 앱은 공식 지원. `orca-*.sh` 는 Git Bash 필요 |

## 테스트

```sh
python3 -m unittest discover -s tests
```
빈 임시 폴더 · 임시 HOME · 가짜 도구로 엔진 3 × Orca 2 조합을 끝까지 두 번 돌리고, 도구가 하나도 없는 PATH · 일부만 있는 PATH 에서 install 이 고르는 순서, 저장소 새로 만들기 · 원격 유무 · gh 없음/로그인 안 됨 분기, `update`, 공개 전 검사(개인 경로 · 메일 · 토큰 모양 문자열)를 본다.

## 라이선스

MIT (`LICENSE`)
