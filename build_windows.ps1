$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $Python) {
        throw "python was not found on PATH. Install Python 3 and rerun this script."
    }
    & $Python.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Creating virtual environment failed (exit code $LASTEXITCODE)." }
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Upgrading pip failed (exit code $LASTEXITCODE)." }
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Installing requirements failed (exit code $LASTEXITCODE)." }

& ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean screamer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)." }

$ExePath = Join-Path $ProjectRoot "dist\Screamer\Screamer.exe"
Write-Host ""
Write-Host "Built: $ExePath"
Write-Host "Run from PowerShell:"
Write-Host "  & `"$ExePath`""
