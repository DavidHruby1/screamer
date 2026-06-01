$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $Python) {
        throw "python was not found on PATH. Install Python 3 and rerun this script."
    }
    & $Python.Source -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt

& ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean screamer.spec

$ExePath = Join-Path $ProjectRoot "dist\Screamer\Screamer.exe"
Write-Host ""
Write-Host "Built: $ExePath"
Write-Host "Run from PowerShell:"
Write-Host "  & `"$ExePath`""
