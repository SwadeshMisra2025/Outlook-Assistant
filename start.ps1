# ============================================================
# Outlook Assistant - General Edition : DAILY START
# Run from the repo root each time you want to use the app.
# ============================================================

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$venv = Join-Path $backendDir ".venv\Scripts"
$python = Join-Path $venv "python.exe"
$port = 8010
$url = "http://127.0.0.1:$port"

Write-Host ""
Write-Host "=== Outlook Assistant - General Edition ===" -ForegroundColor Cyan

# ── Guard: venv must exist ───────────────────────────────────────────────────
if (-not (Test-Path $python)) {
    Write-Host "ERROR: Virtual environment not found. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

# ── Release port if already in use ──────────────────────────────────────────
$existing = netstat -ano | findstr ":$port" | findstr "LISTENING"
if ($existing) {
    $existingPid = ($existing -split '\s+')[-1]
    Write-Host "Releasing port $port (PID $existingPid)..." -ForegroundColor Yellow
    taskkill /PID $existingPid /F | Out-Null
    Start-Sleep -Seconds 1
}

# ── Activate and start backend (also serves frontend) ───────────────────────
Write-Host "Starting backend on $url ..." -ForegroundColor Green
Set-Location $backendDir

# Load .env if present so uvicorn picks it up
$envFile = Join-Path $backendDir ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[A-Z_]+=\S' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

# Start server in background so we can open the browser
$job = Start-Job -ScriptBlock {
    param($python, $backendDir)
    Set-Location $backendDir
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --log-level warning
} -ArgumentList $python, $backendDir

Write-Host "Waiting for backend to be ready..."
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
}

if ($ready) {
    Write-Host "Backend ready. Opening browser..." -ForegroundColor Green
    Start-Process $url
} else {
    Write-Host "WARNING: Backend did not respond in time. Check for errors." -ForegroundColor Yellow
    Write-Host "         Try opening $url manually once the server starts." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "App running at $url" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

# Keep console alive and forward server output
try {
    while ($true) {
        Receive-Job $job
        if ($job.State -ne 'Running') {
            Write-Host "Server stopped unexpectedly." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 2
    }
} finally {
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}
