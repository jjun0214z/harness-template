<!-- harness:template -->
# 하네스 템플릿

새 프로젝트에 **에이전트가 바로 일할 수 있는 틀**을 한 줄로 깔아 주는 템플릿이다.
오케스트레이터 · 저장소별 작업자 · 규칙 스킬 틀 · 안전 훅 · 작업 추적 · 기록이 한 번에 생긴다.

- ⚡ **한 줄 설치**: 내려받기 · 압축 풀기 · GitHub 계정 · `gh` 가 필요 없다
- 🧭 **질문 2개**: 프로젝트 이름과 엔진만 묻고, 나머지는 기본값으로 간다
- 🤖 **Claude / Codex**: 하나 또는 둘 다. Orca 는 있으면 쓴다
- 🪟 **macOS · Linux · Windows**: 같은 코드(python3 표준 라이브러리)가 돈다
- 🔒 **덮지 않음**: 기존 설정 · 저장소 · 사람이 쓴 본문은 그대로 두고, 두 번 돌려도 안전하다

## 🚀 빠른 시작

프로젝트를 만들 폴더에서 아래를 실행한다.

**🍎 macOS · 🐧 Linux** (터미널)

```sh
curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash
```

**🪟 Windows** (PowerShell)

```powershell
$u = "https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1"
irm $u | iex
```

> Windows 의 Git Bash 에서는 위 macOS · Linux 의 `curl … | bash` 한 줄을 그대로 써도 된다. 알아서 PowerShell 설치로 넘긴다.

에이전트에게 맡겨도 된다. Claude 또는 Codex 에게 **「jjun0214z/harness-template 로 셋업해」**.

### 설치 뒤 3단계

| 단계 | macOS · Linux | Windows |
| --- | --- | --- |
| 1. 만들어진 하네스 폴더에서 에이전트 열기 | `claude` (또는 `codex`) | `claude` (또는 `codex`) |
| 2. 지금 할 것 한 개 안내받기 | 「시작해」 | 「시작해」 |
| 3. 코드 저장소 붙이기 | `python3 harness/scripts/bootstrap.py add-repo` | `py -3 harness\scripts\bootstrap.py add-repo` |

막히면 「점검해」, 처음부터 다시 잡으려면 「셋업해」.
같은 안내가 만들어진 `README.md` 와 `상황판.md`(남은 준비 체크리스트)에도 남는다.

<details><summary>다른 설치 방법 (gh 가 있거나, git 만 있거나, 무인으로)</summary>

```sh
# gh 가 있으면 (보조 경로)
gh repo clone jjun0214z/harness-template ~/.harness-template && bash ~/.harness-template/bootstrap.sh
# git 만 있으면
git clone https://github.com/jjun0214z/harness-template ~/.harness-template && bash ~/.harness-template/bootstrap.sh
# 무인(설치 동의 포함)
curl -fsSL https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.sh | bash -s -- --yes
```
```powershell
# 한 줄로 (붙여 넣다 줄이 끊기면 위의 두 줄로)
irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex
# gh 가 있으면
gh repo clone jjun0214z/harness-template $HOME\.harness-template; & $HOME\.harness-template\bootstrap.ps1
# 무인(설치 동의 포함)
$env:HARNESS_YES='1'; irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex
```
</details>

## 📑 목차

| 절 | 한 줄 |
| --- | --- |
| [🧭 설치하면 일어나는 일](#-설치하면-일어나는-일) | 질문 · 기본값 · 단계 |
| [📦 무엇이 생기나](#-무엇이-생기나) | 생성물과 규칙 스킬 |
| [🔧 설정](#-설정) | `harness.json` · 설정 바꾸기 · 이름 바꾸면 남는 자리 |
| [🔄 템플릿 갱신](#-템플릿-갱신) | `update` 와 백업 파일 |
| [🧹 작업 정리](#-작업-정리) | 끝난 작업자 · 세션 닫기 |
| [💻 운영체제별 차이](#-운영체제별-차이) | macOS · Linux · Windows · Orca 없을 때 |
| [🧪 테스트](#-테스트) | 검사 명령 |

## 🧭 설치하면 일어나는 일

**질문 2개와 확인 한 번이면 끝난다.**

1. **프로젝트 이름** (Enter = 지금 폴더 이름)
2. **엔진** Claude · Codex · 둘 다 (Enter = 설치돼 있는 것)
3. 요약 「여기에 만듭니다: <경로> · 엔진 · 저장소는 나중에(add-repo) · Orca」 → Enter (다른 경로를 쳐도 된다)
4. 도구 표 → 설치 동의 한 번 (Orca 도 없으면 여기서 같이 설치, `--no-orca` 로 뺀다)

막 산 컴퓨터에서도 돈다. 먼저 무엇이 있고 없는지 표로 보여 주고, 한 번 동의받은 뒤 필요한 것만 설치한다.

### 묻지 않고 쓰는 기본값

| 무엇 | 기본값 |
| --- | --- |
| 만드는 자리 | 지금 폴더가 비어 있거나 이미 이 하네스가 있으면 **그 폴더**, 아니면 `<지금 폴더>/<슬러그>` |
| 자리가 차 있으면 | 이름이 같아도 비어 있지 않은 폴더에는 섞지 않는다. 그 자리도 차 있으면 멈추고 다른 경로를 묻는다 |
| 다른 자리에 만들기 | 요약에서 다른 경로를 치거나 `--root <경로>` |
| 저장소 | 0개. 나중에 `add-repo` 로 더한다 |
| GitHub | 없음(로컬만) |
| 호칭 | 「대표님」 |
| 규칙 스킬 | 채울 자리 틀 전부 |
| Orca | 있으면 쓰고, 없으면 도구 표에 「설치(선택)」으로 나온다 |

전부 직접 고르려면 `--detail` 을 붙인다. 질문이 네 묶음(프로젝트 · 저장소 · 규칙 스킬 · 엔진/Orca)으로 늘어난다.

| 한 줄 설치에 옵션 주기 | 방법 |
| --- | --- |
| macOS · Linux | `curl … \| bash -s -- --detail` |
| Windows | `$env:HARNESS_ARGS='--detail'` 을 먼저 실행한 뒤 설치 명령 |

### 단계별로 누가 무엇을 하나

| 단계 | 누가 | 무엇 |
| --- | --- | --- |
| 1 | `install.sh` · `install.ps1` (파이썬보다 먼저 도는 셸) | 감지 표 → 동의 → 도구 설치 → 템플릿 원본 받기 |
| 2 | `bootstrap.py run` | 질문 → 도구 표 · 동의(pnpm · 고른 엔진 CLI) → 로그인 확인 |
| 3 | 〃 | 하네스 저장소와 코드 저장소를 **새로 만든다**(`git init -b <기준 브랜치>` · 최소 뼈대 · 첫 커밋) |
| 4 | 〃 | 생성기 → 고른 엔진만 플러그인 설치 → (gh 로그인이 있고 원하면) 비공개 원격 저장소 · 라벨 → Orca 를 골랐으면 등록 |
| 5 | 〃 | doctor 점검 표 + **나중에 할 일**(사람 손이 필요한 것. 같은 원인은 한 줄로 모은다) |

<details><summary>단계별 자세히</summary>

**1단계가 챙기는 것**

- 패키지 관리자 · git · python 3.9+ · node 22. 운영체제별로 무엇을 쓰는지는 [운영체제별 차이](#-운영체제별-차이).
- 템플릿 원본을 `~/.harness-template` 에 받는다. git 이 없으면 압축으로, 있으면 갱신한다.
- 예전 자리 `~/harness-template` 이 있으면 그것을 그대로 쓴다.

**3단계**

- 기존 원격이 있으면 새로 만들기 대신 clone 을 고를 수 있다.
- git 이름 · 메일이 없으면 첫 커밋만 미룬다. 신원을 넣은 뒤 같은 `run` 을 다시 돌리면 생성한 파일만 담아 커밋한다.

**5단계 「나중에 할 일」**

- 로그인 · 원격 연결 · git 이름 · 메일 설정처럼 사람 손이 필요한 것을 모은다.

**이미 있는 것은 건드리지 않는다**

- 이미 있는 도구 · 로그인 · 플러그인 · 등록은 건너뛴다.
- 모자란 버전은 쓰던 관리자(nvm · fnm · nvm-windows)로 올리고 기존 설치는 지우지 않는다.
- `~/.codex/config.toml` 같은 사용자 설정은 덮지 않고 필요한 항목만 백업 뒤 덧붙인다. 두 번 돌려도 안전하다.

**GitHub 는 선택**

- gh · GitHub 로그인이 없으면 로컬 하네스만 만들고 「나중에 할 일」에 원격 연결 명령을 적는다.
- 원격이 없는 동안은 이슈를 쓸 수 없어 `상황판.md` 가 작업 추적의 주인이다. 원격을 만들면 목록이 이슈 · 라벨로 넘어간다.

</details>

## 📦 무엇이 생기나

| 생성물 | 성격 | 다시 생성하면 |
| --- | --- | --- |
| `CLAUDE.md` | 오케스트레이터 지침 · 저장소 지도 · 맡기는 방법 | 관리 블록만 바뀌고 「프로젝트 메모」는 그대로 |
| `AGENTS.md` | Codex 진입점(CLAUDE.md 를 읽게만) | 설정대로 |
| `plugins/<슬러그>/` | 공통 플러그인: push 가드 훅 · 핵심 요약 · 작업자(`<키>-worker`) · 조사원 · 검토원 | 설정대로 (`core.md` 는 관리 블록만) |
| `plugins/<슬러그>/skills/` | 규칙 스킬(아래 표) | **없을 때만 만든다.** 단 관리 블록은 다시 맞춘다 |
| `harness/` | 셋업 엔진 · 생성기 · 저장소 훅(규칙 보호 · 하네스 동기화) · 작업 공간 정리 | `update` 로 템플릿에서 |
| `README.md` · `상황판.md` | 설치한 사람용 안내 한 장 · 지금 집중과 남은 준비 체크리스트 | 없을 때만 |

<details><summary>생성물 전체 표</summary>

| 생성물 | 성격 | 다시 생성하면 |
| --- | --- | --- |
| `CLAUDE.md` | 오케스트레이터 지침 · 저장소 지도 · 맡기는 방법 | 관리 블록만 바뀌고 「프로젝트 메모」는 그대로 |
| `AGENTS.md` | Codex 진입점(CLAUDE.md 를 읽게만) | 설정대로 |
| `plugins/<슬러그>/` | 공통 플러그인: push 가드 훅 · 핵심 요약 · 작업자(`<키>-worker`) · 조사원 · 검토원 | 설정대로 (`core.md` 는 관리 블록만) |
| `plugins/<슬러그>/skills/` | 규칙 스킬(아래 목록) | **없을 때만 만든다.** 단 관리 블록은 다시 맞춘다 |
| `.claude/settings.json` · `.claude-plugin/marketplace.json` | Claude 를 골랐을 때 | 설정대로 |
| `.codex/hooks.json` · `.codex/agents/*.toml` · `.agents/plugins/marketplace.json` | Codex 를 골랐을 때 | 설정대로 |
| `scripts/orca-*.sh` | Orca 를 골랐을 때(bash) | 설정대로 |
| `harness/` | 셋업 엔진 · 생성기 · 저장소 훅(규칙 보호 · 하네스 동기화) · 작업 공간 정리 | `update` 로 템플릿에서 |
| `README.md` | 설치한 사람용 안내 한 장(「하고 싶은 것 → 이렇게 말한다」) | 없을 때만 |
| `상황판.md` | 지금 집중과 남은 준비 체크리스트 | 없을 때만 |
| `docs/기록/README.md` | 근거 기록 규칙 | 없을 때만 |
| 코드 저장소마다 | `README.md` · `.gitignore`(하네스와 같이 `.env` · `.env.*` 무시, `.env.example` 은 커밋) · `CLAUDE.md` · `AGENTS.md` · 플러그인을 켜는 `.claude/settings.json` + 첫 커밋 | 이미 있으면 건드리지 않는다 |

</details>

규칙 스킬은 세 종류다.

| 종류 | 무엇 | 다시 생성하면 |
| --- | --- | --- |
| 생성기가 주인 | `setup` · `start` (내용이 상태 판정뿐이라 채울 자리가 없다) | 설정대로 |
| 절차 뼈대 5개 | absolute-rules · work-method · git-rules · deploy · task-brief | 없을 때만 (관리 블록은 맞춘다) |
| 채울 자리 틀 | code-convention · security-privacy · operations · design 중 고른 것 | 없을 때만 (관리 블록은 맞춘다) |

「없을 때만」인 스킬도 설정에서 만든 표(저장소 지도 · 배포 · 검사 명령 · 맡기는 방법)는 관리 블록이라 다시 생성할 때 맞춘다.
블록 밖(사람이 채운 본문)은 그대로 둔다.

## 🔧 설정

`harness.json` 하나가 설정 원본이다. 여기를 고치고 생성기를 돌리면 생성물이 그 설정대로 다시 맞춰진다.

| 하고 싶은 것 | 하네스 폴더에서 (Windows 는 `python3` 대신 `py -3`) |
| --- | --- |
| 저장소 더하기(새로 만들기 · 원격 받기 · 이 컴퓨터 폴더 연결) | `python3 harness/scripts/bootstrap.py add-repo` (자세히: `--detail`) |
| 엔진 · Orca · 호칭 · GitHub 조직 바꾸기 | `harness.json` 을 고친 뒤 `python3 harness/scripts/generate.py` |
| 템플릿 갱신 받기 | `python3 harness/scripts/bootstrap.py update` |
| 점검 | `python3 harness/scripts/bootstrap.py doctor` |

**저장소는 셋업 때 넣어도 되고, 0개로 셋업한 뒤 나중에 더해도 된다.**
`add-repo` 는 대화형이고, `--repo <조각.json>` 으로 설정 조각을 줄 수도 있다.
세 길 중 하나를 고르고, 그 저장소에만 작업자 · 지도 · 설치 · 등록을 더한다.

| 길 | 무엇을 한다 |
| --- | --- |
| 새로 만들기 | 저장소를 새로 만든다(`git init` · 최소 뼈대 · 첫 커밋). GitHub 원격은 원할 때만 |
| 원격에서 받기 | 원격 주소를 주면 clone 한다 |
| 이 컴퓨터의 폴더 연결 | 있는 폴더를 경로로 고른다(다른 위치여도 된다. 옮기지 않는다) |

이미 있는 저장소를 연결할 때는 코드 · 이력 · 원격 · 브랜치를 그대로 두고 하네스 쪽 지도 · 작업자에만 넣는다.
한 프로젝트 안에서 섞어도 된다. 하네스 저장소도 새로 만들거나 기존 폴더를 연결한다.

<details><summary>harness.json 예시와 빈 칸 기본값</summary>

```json
{
  "project": {"name": "Acme 여행", "slug": "acme", "github_org": "acme-inc", "owner_title": "대표님"},
  "harness_repo": {"dir": "orchestrator", "base_branch": "main"},
  "repos": [
    {"key": "web", "base_branch": "develop", "stack": "node", "description": "서비스 본체",
     "deploy": {"develop": "dev 배포", "main": "상용 배포"}, "ask_on_push": ["main"]},
    {"key": "legacy", "source": "clone", "url": "https://github.com/acme-inc/legacy.git"},
    {"key": "admin", "source": "local", "path": "~/code/admin", "connect_files": true}
  ],
  "engines": ["claude", "codex"],
  "orca": {"enabled": false}
}
```

빈 칸은 기본값으로 채운다.

| 칸 | 안 적으면 |
| --- | --- |
| `dir` | `key` 와 같은 이름 |
| `remote` | `<github_org>/<key>` (조직이 없으면 로컬만) |
| `source` | `new`. `url` 을 적으면 `clone`, 이 컴퓨터의 폴더는 `local` + `path` |
| `stack` | `none`. `node` · `python` · `none` 중 하나이고, 필요한 런타임과 작업자 검사 명령 기본값이 따라온다 |

- `source: local` 은 폴더 이름 · 기본 브랜치 · 원격을 그 저장소에서 읽는다.
- `connect_files: true` 일 때만 없는 `CLAUDE.md` · `AGENTS.md` · `.claude/settings.json` 을 더한다.
- 빈 저장소라 의존성(pnpm install 등)은 설치하지 않는다.
- 전체 예시는 `examples/harness.example.json`.
- 설정만 있으면 `./bootstrap.sh run --config harness.json --yes --non-interactive`.

</details>

<details><summary>⚠️ 이름 · 호칭을 바꾸면 낡은 채 남는 자리</summary>

프로젝트 이름(`project.name`)과 호칭(`project.owner_title`)은 규칙 스킬의 머리말(YAML)과 본문에도 들어간다.
그 자리는 관리 블록으로 감쌀 수 없다(머리말 안에는 주석을 넣을 수 없다).
**그래서 이름이나 호칭을 바꾸고 생성기를 돌려도 아래는 낡은 채 남는다. 직접 고친다.**

| 무엇을 바꿨나 | 낡는 자리 (하네스 폴더 안) |
| --- | --- |
| 프로젝트 이름 | `README.md` 제목, 규칙 스킬 5개의 `description:` (absolute-rules · code-convention · design · operations · security-privacy) |
| 호칭 | 규칙 스킬 6개 본문 11줄 (absolute-rules 3 · deploy 3 · task-brief 2 · code-convention 1 · security-privacy 1 · work-method 1) |

**`doctor` 는 이것을 알리지 않는다.** 관리 블록만 보고 「설정에서 만드는 표가 모두 최신이다」라고 한다.
옛 이름 · 옛 호칭으로 `grep -rn` 해서 남은 자리를 직접 찾아 고친다.

</details>

<details><summary>템플릿 원본과 프로젝트 루트</summary>

| 무엇 | 어디 | 역할 |
| --- | --- | --- |
| 템플릿 | `~/.harness-template` (이 저장소, 숨김 폴더) | 원본. 여기에 하네스를 만들지 않는다. 여러 프로젝트가 같이 쓴다 |
| 프로젝트 루트 | 설치 명령을 실행한 폴더, 또는 그 아래 `<프로젝트 슬러그>/` | 그 안에 하네스(`orchestrator/`)와 새 저장소들 |

- 어느 쪽이 되는지는 [묻지 않고 쓰는 기본값](#묻지-않고-쓰는-기본값) 표의 「만드는 자리」와 같다.
- 예: `~/dev` 가 비어 있지 않으면 `~/dev/acme/` 에 만든다.
- 질문 첫머리에 「여기에 만듭니다: <경로>」를 보여 주고 Enter 면 그대로 간다.
- 연결한 저장소는 루트 밖 경로 그대로 둔다.

</details>

## 🔄 템플릿 갱신

**이미 만든 하네스가 있으면 새로 설치하지 말고 갱신만 받는다.**

| OS | 하네스 폴더에서 |
| --- | --- |
| macOS · Linux | `python3 harness/scripts/bootstrap.py update` |
| Windows | `py -3 harness\scripts\bootstrap.py update` |

| 갱신하면 | 무엇 |
| --- | --- |
| 새로 가져와 다시 생성 | 엔진(`harness/`) · 설정에서 만든 표(관리 블록) · 생성기가 주인인 `setup` · `start` |
| 덮지 않음 | 사람이 채운 규칙 스킬 본문 · 상황판 · 「프로젝트 메모」 |
| 직접 옮김 | 템플릿의 규칙 뼈대가 바뀐 것. `harness/skeleton/plugin/skills/` 와 직접 비교한다 |

<details><summary>.gitignore 관리 블록과 백업 파일</summary>

**`.gitignore`**

- 하네스 `.gitignore` 는 `# >>> harness managed >>>` ~ `# <<< harness managed <<<` 블록만 생성기 몫이다.
- 블록 밖에 더한 줄은 update 가 건드리지 않는다.
- 블록이 없던 옛 파일은 한 번 블록을 앞에 심고 원래 줄은 그 아래에 둔다.

**백업 파일이 생긴다**

- 관리 블록이 없던 옛 파일에 블록을 처음 심을 때, 바꾸기 전 원본을 그 파일 옆에 `<파일 이름>.harness-bak-<시각 14자리>` 로 남긴다.
- 심은 파일 하나에 하나씩이라 여러 개가 된다(저장소 0개 · 원격 없는 옛 설치본에서 8개, 블록 없는 `.gitignore` 까지면 9개).
- 경로는 화면에 `(백업) <경로>` 로 전부 찍힌다.
- 생성본 `.gitignore` 의 `*.harness-bak-*` 가 걸러 커밋에는 섞이지 않는다.
- 바뀐 내용을 확인했으면 지워도 된다. 두 번째부터는 심을 것이 없어 백업도 더 생기지 않는다.

</details>

## 🧹 작업 정리

> 오프라인 = 끝난 작업.

| 작업자 | 정리 명령 |
| --- | --- |
| 보통 | `python3 harness/scripts/finish_worker.py <워크트리>` |
| Orca 작업자 | `scripts/orca-finish-worker.sh <dispatch> <워크트리>` |

아래를 전부 확인하고, 하나라도 모르면 멈춘다(`--dry-run` 으로 미리 보기).

- 변경 없음 · 원격에 없는 커밋 0
- 그 작업자가 작업 중 · 응답 대기가 아님
- 부른 세션 자신의 워크트리가 아님

세션은 종류마다 다르게 닫는다.

| 어디 | 어떻게 |
| --- | --- |
| Claude 세션 | 정상 종료만 한다(안 끝나면 멈추고 보고) |
| Codex 세션 | `codex archive` 로 보관한다 |
| Claude 앱 · 웹 · 폰의 원격 세션 목록 | 스크립트로 보관할 공개 방법이 없다. 아래 방법으로 사람이 정리한다 |

<details><summary>원격 세션 목록 정리 방법</summary>

- 오케스트레이터가 브라우저 도구로 claude.ai/code 를 열어, 링크가 `/code/<bridgeSessionId>` 인 오프라인 세션만 보관한다.
- `<bridgeSessionId>` 는 정리 스크립트가 끝에 출력한다. 목록 제목은 자동 생성이라 이름으로는 못 찾는다.
- 이 절차는 생성되는 `deploy` 스킬 「작업 공간 · 앱 세션 정리」에 그대로 들어간다.

</details>

## 💻 운영체제별 차이

| 무엇 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 한 줄 설치 | `install.sh` | `install.sh` | `install.ps1` (Git Bash 에서 부른 `install.sh` 는 자동으로 이것으로 넘긴다) |
| 패키지 관리자 | 개발자 도구(`xcode-select --install`) + brew | apt · dnf (sudo) | winget (없으면 App Installer 안내) |
| node 22 | 쓰던 nvm · fnm, 없으면 brew `node@22` | 쓰던 nvm · fnm, 없으면 nvm 설치 | 쓰던 nvm-windows · fnm, 없으면 winget |
| 설치 뒤 PATH | 같은 셸에서 brew shellenv · node 경로를 올린다 | 같음 | 레지스트리에서 PATH 를 다시 읽는다 |
| 훅 파이썬 | `python3` | `python3` | 찾은 것(`py -3` · `python`), 스토어 가짜 실행 파일은 건너뜀 |
| 심링크 · 줄바꿈 | 쓰지 않는다(복사) · LF | 같음 | 같음(`.ps1` 만 CRLF) |
| Orca | 앱 · CLI | 앱(AppImage) | 앱은 공식 지원. `orca-*.sh` 는 Git Bash 필요 |

<details><summary>Windows 에서 Git Bash 를 쓸 때</summary>

Git Bash 에서 `curl … | bash` 한 줄을 그대로 써도 된다.

- 스스로 Windows 임을 알아채 PowerShell 설치로 넘기고, 실행한 폴더와 옵션도 같이 넘긴다.
- 공백 든 경로 · `/c/...` 경로도 그대로 간다.
- Git Bash 창에서는 질문 입력이 막힐 수 있어 `winpty` 로 감싸 잇는다.
- `winpty` 가 없으면 새 PowerShell 창을 열어 거기서 잇는다.

</details>

### 🧩 Orca 가 없을 때

작업자는 서브에이전트(`<키>-worker`, 워크트리 격리) · `claude --worktree` · Codex 앱 저장 프로젝트 워크트리로 띄운다.
`CLAUDE.md` 「맡기는 방법」 표가 고른 조합대로 만들어진다.

## 🧪 테스트

```sh
python3 -m unittest discover -s tests
```

빈 임시 폴더 · 임시 HOME · 가짜 도구로 다음을 본다.

| 무엇을 보나 | 내용 |
| --- | --- |
| 조합 | 엔진 3 × Orca 2 조합을 끝까지 두 번 돌리기(두 번 돌려도 결과가 같은지) |
| 도구 없는 PATH | 도구가 하나도 없는 PATH · 일부만 있는 PATH 에서 install 이 고르는 순서 |
| 저장소 분기 | 저장소 새로 만들기 · 원격 유무 · gh 없음 · gh 로그인 안 됨 |
| 갱신 | `update` |
| 온보딩 | 설치 직후 마지막 안내 · 루트 `README.md` · `상황판.md` 체크리스트 · `start` 스킬 |
| 공개 전 검사 | 개인 경로 · 메일 · 토큰 모양 문자열 |

## 📄 라이선스

MIT (`LICENSE`)
