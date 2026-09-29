# 하네스 템플릿 한 줄 설치(Windows PowerShell). 막 산 컴퓨터에서도 돈다: 파이썬보다 먼저 도는 순수 PowerShell 이다.
#
#   irm https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1 | iex
#   (붙여 넣다 줄이 끊기면 두 줄로) $u = "https://raw.githubusercontent.com/jjun0214z/harness-template/main/install.ps1"  다음 줄  irm $u | iex
#   (무인) $env:HARNESS_YES='1'; irm .../install.ps1 | iex
#
# 순서: 감지 표(도구 · 찾은 버전 · 할 일) → 한 번 동의 → winget 확인 · git · python · node 22
#       → 환경변수 PATH 다시 읽기 → 템플릿 받기(git clone, git 이 없으면 zip) · 이미 있으면 갱신 → bootstrap.py 실행
# 이미 있는 것은 건너뛰고, node 가 모자라면 nvm-windows · fnm 이 있으면 그걸로 올린다. 기존 설치는 지우지 않는다.
# 환경변수: HARNESS_YES=1(무인 동의) · HARNESS_DRY_RUN=1(할 일만 출력) · HARNESS_NO_RUN=1(받기까지만) · HARNESS_DIR · HARNESS_TEMPLATE_URL · HARNESS_ZIP_URL
#           HARNESS_ARGS_JSON(bootstrap.py 로 넘길 인자, JSON 문자열 배열) · HARNESS_ARGS(공백으로 나누는 한 줄) · 없으면 run

# 본문 전체가 Install-Harness 함수 안에 있고 마지막 줄에서만 부른다: 끝까지 받기 전에는 아무것도 실행하지 않는다.
function Install-Harness {
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$TemplateUrl = if ($env:HARNESS_TEMPLATE_URL) { $env:HARNESS_TEMPLATE_URL } else { 'https://github.com/jjun0214z/harness-template.git' }
$ZipUrl = if ($env:HARNESS_ZIP_URL) { $env:HARNESS_ZIP_URL } else { 'https://codeload.github.com/jjun0214z/harness-template/zip/refs/heads/main' }
# 실행한 셸의 현재 폴더: 프로젝트는 여기 아래 <슬러그>\ 에 만든다
if (-not $env:HARNESS_CALLER_CWD) { $env:HARNESS_CALLER_CWD = (Get-Location).Path }
# 템플릿 원본은 숨김 폴더. 예전 자리(~\harness-template)가 있으면 그것을 그대로 쓴다
$legacy = Join-Path $HOME 'harness-template'
$Dir = if ($env:HARNESS_DIR) { $env:HARNESS_DIR }
       elseif ((Test-Path (Join-Path $legacy '.git')) -or (Test-Path (Join-Path $legacy '.harness-template'))) { $legacy }
       else { Join-Path $HOME '.harness-template' }
$NodeMajor = 22
$Yes = $env:HARNESS_YES -eq '1'
$Dry = $env:HARNESS_DRY_RUN -eq '1'
$NoRun = $env:HARNESS_NO_RUN -eq '1'
# bootstrap.py 인자: HARNESS_ARGS_JSON(JSON 문자열 배열, Git Bash 의 install.sh 가 넘긴다. 공백 든 경로도 한 인자로) >
# HARNESS_ARGS(공백으로 나누는 한 줄, 사람이 직접 줄 때) > 기본 run.
# 반드시 @() 로 감싼다: 결과가 원소 하나면 문자열로 풀려 @BootArgs 가 글자 단위(r u n)로 넘어간다
$BootArgs = @()
if ($env:HARNESS_ARGS_JSON) {
    $parsed = ConvertFrom-Json $env:HARNESS_ARGS_JSON   # 배열 하나가 통째로 온다: 변수에 받은 뒤 풀어야 원소별이 된다
    $BootArgs = @($parsed | ForEach-Object { [string]$_ })
} elseif ($env:HARNESS_ARGS) {
    $BootArgs = @($env:HARNESS_ARGS -split ' ' | Where-Object { $_ })
}
if ($BootArgs.Count -eq 0) { $BootArgs = @('run') }

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Refresh-Path {
    # 새 창 없이 이어지게 설치 뒤 PATH 를 레지스트리에서 다시 읽는다
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Find-Python {
    # 돌려주는 것: 실행 파일과 인자(예: py.exe -3). 부르는 쪽이 항상 @(Find-Python) 으로 받아 배열로 쓴다
    foreach ($line in @('py -3', 'python3', 'python')) {
        $c = @($line -split ' ')
        $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -like '*WindowsApps*') { continue }   # 스토어 연결용 가짜 실행 파일
        $extra = @($c | Select-Object -Skip 1)
        & $cmd.Source @extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @($cmd.Source) + $extra
        }
    }
}

function Node-Major {
    if (-not (Have 'node')) { return 0 }
    $v = (& node --version 2>$null) -replace '^v', ''
    return [int]($v.Split('.')[0])
}

$plan = New-Object System.Collections.ArrayList
function Add-Plan($tool, $found, $action, $cmd) { [void]$plan.Add([pscustomobject]@{ Tool = $tool; Found = $found; Action = $action; Cmd = $cmd }) }

$hasWinget = Have 'winget'
if ($hasWinget) { Add-Plan 'winget' '있음' '건너뜀' '-' }
else { Add-Plan 'winget' '없음' '안내' 'Microsoft Store 에서 「앱 설치 관리자(App Installer)」를 설치한 뒤 다시 실행' }
function Winget-Or($id, $manual) { if ($hasWinget) { return "winget install -e --id $id --accept-source-agreements --accept-package-agreements" } else { return $manual } }

if (Have 'git') { Add-Plan 'git' ((& git --version) -replace 'git version ', '') '건너뜀' '-' }
else { Add-Plan 'git' '없음' ($(if ($hasWinget) { '설치' } else { '안내' })) (Winget-Or 'Git.Git' 'https://git-scm.com/download/win') }

$py = @(Find-Python)
if ($py.Count -gt 0) { $pyArgs = @($py | Select-Object -Skip 1); Add-Plan 'python' ((& $py[0] @pyArgs --version) -replace 'Python ', '') '건너뜀' '-' }
else { Add-Plan 'python' '없음(3.9 이상 필요)' ($(if ($hasWinget) { '설치' } else { '안내' })) (Winget-Or 'Python.Python.3.12' 'https://www.python.org/downloads/windows/') }

$nm = Node-Major
if ($nm -ge $NodeMajor) { Add-Plan 'node' (& node --version) '건너뜀' '-' }
else {
    $act = if ($nm -gt 0) { '올림' } else { '설치' }
    $found = if ($nm -gt 0) { (& node --version) } else { '없음' }
    if (Have 'nvm') { Add-Plan 'node' $found $act "nvm install $NodeMajor; nvm use $NodeMajor" }
    elseif (Have 'fnm') { Add-Plan 'node' $found $act "fnm install $NodeMajor; fnm default $NodeMajor" }
    else { Add-Plan 'node' $found ($(if ($hasWinget) { $act } else { '안내' })) (Winget-Or 'OpenJS.NodeJS.LTS' 'https://nodejs.org') }
}
$isTemplate = (Test-Path (Join-Path $Dir '.git')) -or (Test-Path (Join-Path $Dir '.harness-template'))
if ($isTemplate) { Add-Plan '템플릿' $Dir '갱신' '-' } else { Add-Plan '템플릿' '없음' '받기' $Dir }

Write-Host "하네스 템플릿 설치 (windows) · 프로젝트는 $($env:HARNESS_CALLER_CWD)\<프로젝트 슬러그> 에 만든다"
Get-ChildItem -Path $HOME -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    if (Test-Path (Join-Path $_.FullName 'harness.json')) {
        Write-Host "이미 만든 하네스가 있다: $($_.FullName). 템플릿 갱신만이면 그 폴더에서: py -3 harness\scripts\bootstrap.py update"
    }
}
Write-Host ''
Write-Host '| 도구 | 찾은 버전 | 할 일 | 명령 |'
Write-Host '| --- | --- | --- | --- |'
foreach ($r in $plan) { Write-Host "| $($r.Tool) | $($r.Found) | $($r.Action) | $($r.Cmd) |" }
Write-Host ''

$todo = @($plan | Where-Object { $_.Action -in @('설치', '올림') })
if ($Dry) {
    foreach ($r in $todo) { Write-Host "PLAN $($r.Tool): $($r.Cmd)" }
    Write-Host "PLAN 템플릿: $Dir"
    Write-Host "PLAN bootstrap: $($BootArgs -join ' ')"
    return
}
$go = $true
if ($todo.Count -gt 0 -and -not $Yes) {
    $ans = Read-Host '위 표의 설치 · 올림을 진행할까요? (Y/n)'
    $go = ($ans -eq '' -or $ans -match '^(y|yes|예|네)$')
}
if ($todo.Count -gt 0 -and $go) {
    foreach ($r in $todo) {
        Write-Host "[실행] $($r.Cmd)"
        Invoke-Expression $r.Cmd
        if ($LASTEXITCODE -ne 0 -and $r.Tool -in @('git', 'python')) { Write-Host "$($r.Tool) 설치 실패: $($r.Cmd)"; return }
        Refresh-Path
    }
    $py = @(Find-Python)
} elseif ($todo.Count -gt 0) {
    Write-Host '설치 · 올림을 건너뛴다(무인 실행은 HARNESS_YES=1). 위 표의 명령을 직접 실행한 뒤 다시 부른다.'
}
if ($py.Count -eq 0) { Write-Host 'python 3.9 이상이 없어 멈춘다. 위 표의 python 명령을 실행한 뒤 새 PowerShell 에서 다시 부른다.'; return }

# ---------------------------------------------------------------- 템플릿 받기 · 갱신
if ((Test-Path (Join-Path $Dir '.git')) -and (Have 'git')) {
    Write-Host "[템플릿] 갱신 $Dir"
    & git -C $Dir pull --ff-only --quiet
} elseif ((Have 'git') -and -not (Test-Path $Dir)) {
    Write-Host "[템플릿] 받기(git clone) $Dir"
    & git clone --quiet --depth 1 $TemplateUrl $Dir
    if ($LASTEXITCODE -ne 0) { Write-Host "템플릿 clone 실패: $TemplateUrl"; return }
} elseif (-not (Test-Path $Dir) -or (Test-Path (Join-Path $Dir '.harness-template'))) {
    Write-Host "[템플릿] 받기(압축) $Dir"
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-template-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp 't.zip'
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing -ErrorAction Stop
        Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force -ErrorAction Stop
    } catch {
        Write-Host "템플릿 내려받기 · 풀기 실패: $ZipUrl ($($_.Exception.Message))"
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return
    }
    $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
    if (-not (Test-Path (Join-Path $src.FullName '.harness-template'))) { Write-Host '받은 것이 템플릿이 아니다'; return }
    if (Test-Path $Dir) { Remove-Item -Recurse -Force $Dir }
    Move-Item $src.FullName $Dir
    Remove-Item -Recurse -Force $tmp
} else {
    Write-Host "$Dir 가 이미 있는데 템플릿이 아니다. HARNESS_DIR 로 다른 폴더를 준다."; return
}

if ($NoRun) { Write-Host "[끝] 템플릿 $Dir (bootstrap 은 부르지 않음)"; return }
$boot = Join-Path $Dir 'harness\scripts\bootstrap.py'
$env:PYTHONUTF8 = '1'
Write-Host "[bootstrap] $($py -join ' ') $boot $($BootArgs -join ' ')"
$pyArgs = @($py | Select-Object -Skip 1)   # py.exe 만이면 빈 배열
& $py[0] @pyArgs $boot @BootArgs
}

Install-Harness
