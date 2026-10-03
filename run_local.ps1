# Runs the app on this PC.   pwsh .\run_local.ps1   then open http://127.0.0.1:8000
# With a .env file in this folder the app uses the database named there (Supabase).
# Without one it uses a SQLite file and local storage under .\data, and the first sign-in is
# admin@local / change-me-now (change the password after signing in).
param(
    [int] $Port = 8000,
    [string] $Venv = "$env:USERPROFILE\.venvs\dev_factor_extension"
)

try {
    $null = Invoke-WebRequest "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    Write-Host "The app is already running. Open http://127.0.0.1:$Port in a browser."
    Write-Host "To stop it, run stop_app.cmd."
    exit 0
} catch { }   # not running yet: start it below

$python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating the virtual environment in $Venv"
    py -3.12 -m venv $Venv
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r "$PSScriptRoot\requirements.txt"
}

if (-not (Test-Path "$PSScriptRoot\.env")) {
    if (-not $env:ADMIN_EMAIL) { $env:ADMIN_EMAIL = "admin@local" }
    if (-not $env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD = "change-me-now" }
}

Set-Location $PSScriptRoot
& $python -m uvicorn hazard_ext.web.main:create_app --factory --host 127.0.0.1 --port $Port
