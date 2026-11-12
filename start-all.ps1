# Sendwise Agent OS — one-command local start (PowerShell)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "`n[1/4] Starting PostgreSQL (docker)..." -ForegroundColor Cyan
docker compose -f "$root\docker-compose.yml" up -d db
Start-Sleep -Seconds 6

Write-Host "[2/4] Preparing backend venv (if needed)..." -ForegroundColor Cyan
$venv = "$root\backend\.venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
    & "$venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    & "$venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt" --quiet
}

Write-Host "[3/4] Seeding database..." -ForegroundColor Cyan
& "$venv\Scripts\python.exe" -m app.seed
if ($LASTEXITCODE -ne 0) { throw "Seed failed" }

Write-Host "[4/4] Starting backend + frontend..." -ForegroundColor Cyan
$backend = Start-Process -FilePath "$venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--port","8000" -WorkingDirectory "$root\backend" -WindowStyle Hidden -PassThru
$frontend = Start-Process -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory "$root\frontend" -WindowStyle Hidden -PassThru

Write-Host "`nSendwise is starting:"
Write-Host "  Frontend : http://localhost:3000" -ForegroundColor Green
Write-Host "  Backend  : http://localhost:8000  (docs at /docs)" -ForegroundColor Green
Write-Host "  Login    : admin@sendwise.io / admin123" -ForegroundColor Green
Write-Host "`nBackend PID: $($backend.Id)  |  Frontend PID: $($frontend.Id)"
Write-Host "Stop: Get-Process -Id $($backend.Id),$($frontend.Id) | Stop-Process`n"
