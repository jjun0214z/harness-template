# Harness setup entry for Windows PowerShell. Finds Python 3.9+ and runs harness\scripts\bootstrap.py.
# Usage: .\bootstrap.ps1 [run|check|tools|generate|doctor|update] [options]   (no arguments = interactive run)
# This file is ASCII only on purpose: Windows PowerShell 5.1 reads a script file without BOM in the ANSI code page,
# so non-ASCII text could break parsing. Korean messages live in bootstrap.py (UTF-8, PYTHONUTF8=1).
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# The project is created under the folder this was started from: <cwd>\<slug>
if (-not $env:HARNESS_CALLER_CWD) { $env:HARNESS_CALLER_CWD = (Get-Location).Path }
$script = Join-Path $here 'harness\scripts\bootstrap.py'
# Candidates are one-line strings split into arrays with @() so a single element never collapses into a string.
foreach ($line in @('py -3', 'python3', 'python')) {
    $c = @($line -split ' ')
    $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # Skip the Microsoft Store stub (WindowsApps)
    if ($cmd.Source -like '*WindowsApps*') { continue }
    $extra = @($c | Select-Object -Skip 1)
    & $cmd.Source @extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    if ($LASTEXITCODE -eq 0) {
        $env:PYTHONUTF8 = '1'
        $passArgs = @($args)
        & $cmd.Source @extra $script @passArgs
        exit $LASTEXITCODE
    }
}
Write-Host 'Python 3.9 or newer was not found. Install it, open a new terminal, and run this again:'
Write-Host '  winget install -e --id Python.Python.3.12'
Write-Host '  (without winget: https://www.python.org/downloads/windows/ and enable "Add python.exe to PATH")'
exit 2
