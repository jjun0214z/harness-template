# 하네스 셋업 진입(Windows PowerShell). python 3.9 이상을 찾아 harness\scripts\bootstrap.py 를 부른다.
# 사용: .\bootstrap.ps1 [run|check|tools|generate|doctor] [옵션]   (인자 없이 부르면 대화형 run)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:HARNESS_CALLER_CWD) { $env:HARNESS_CALLER_CWD = (Get-Location).Path }   # 프로젝트는 실행한 폴더 아래 <슬러그>\ 에
$script = Join-Path $here 'harness\scripts\bootstrap.py'
$candidates = @(@('py', '-3'), @('python3'), @('python'))
foreach ($c in $candidates) {
    $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # 스토어 연결용 가짜 python(WindowsApps)은 건너뛴다
    if ($cmd.Source -like '*WindowsApps*') { continue }
    $extra = @()
    if ($c.Length -gt 1) { $extra = $c[1..($c.Length - 1)] }
    & $cmd.Source @extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $env:PYTHONUTF8 = '1'
        & $cmd.Source @extra $script @args
        exit $LASTEXITCODE
    }
}
Write-Host 'python 3.9 이상이 없다. 설치한 뒤 새 터미널에서 다시 실행한다:'
Write-Host '  winget install -e --id Python.Python.3.12'
Write-Host '  (winget 이 없으면 https://www.python.org/downloads/windows/ 에서 받고 「Add python.exe to PATH」를 켠다)'
exit 2
