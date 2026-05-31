$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt

& ".venv\Scripts\pyinstaller.exe" --noconfirm --clean screamer.spec

$ExePath = Join-Path $ProjectRoot "dist\Screamer\Screamer.exe"
Write-Host ""
Write-Host "Built: $ExePath"
Write-Host "Run from PowerShell:"
Write-Host "  & `"$ExePath`""
