# ============================================================
# Outlook Assistant - General Edition : ONE-TIME SETUP
# Run this once from the repo root on a fresh Windows machine.
#
# What this script does (fully automated):
#   1. Installs Python 3.12 via winget (if missing)
#   2. Installs VS C++ Build Tools (if missing, needed for chromadb/numpy)
#   3. Installs Ollama (if missing)
#   4. Creates Python virtual environment
#   5. Installs all Python dependencies
#   6. Copies .env.example -> backend/.env and configures SOURCE_SQLITE_PATH
#   7. Pulls required Ollama models
#   8. Runs the initial SQLite load into the packaged local database
# ============================================================

#Requires -Version 5.1
param(
    [string]$DefaultSourceSqlitePath = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
if (-not $scriptPath) {
    throw "Unable to resolve setup script path."
}

$repoRoot = Split-Path -Parent $scriptPath
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (-not $LogPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $LogPath = Join-Path $logDir "setup-$stamp.log"
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]"Administrator"
)
if (-not $isAdmin) {
    Write-Host ""
    Write-Host "Re-launching as Administrator (required for software installs)..." -ForegroundColor Yellow
    $argList = "-ExecutionPolicy Bypass -File `"$scriptPath`""
    if ($DefaultSourceSqlitePath) {
        $argList += " -DefaultSourceSqlitePath `"$DefaultSourceSqlitePath`""
    }
    $argList += " -LogPath `"$LogPath`""
    Write-Host "Log file: $LogPath" -ForegroundColor Gray
    Start-Process powershell.exe $argList -Verb RunAs
    exit
}

Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force

$transcriptStarted = $false
try {
Start-Transcript -Path $LogPath -Force | Out-Null
$transcriptStarted = $true
Write-Host "Installation log: $LogPath" -ForegroundColor Gray

$backendDir = Join-Path $repoRoot "backend"
$venvDir = Join-Path $backendDir ".venv"
$envFile = Join-Path $backendDir ".env"
$envExample = Join-Path $backendDir ".env.example"
$tmpDir = Join-Path $env:TEMP "outlook-assistant-setup"
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

Write-Host ""
Write-Host "=== Outlook Assistant - General Edition Setup ===" -ForegroundColor Cyan
Write-Host "    Running as Administrator. All steps are automated." -ForegroundColor Gray
Write-Host ""

# Pre-flight: Outlook desktop check
Write-Host "[PRE] Checking for Outlook Classic (desktop) installation..." -ForegroundColor Yellow
$outlookExe = @(
    "$env:ProgramFiles\Microsoft Office\root\Office16\OUTLOOK.EXE",
    "${env:ProgramFiles(x86)}\Microsoft Office\root\Office16\OUTLOOK.EXE",
    "$env:ProgramFiles\Microsoft Office\Office16\OUTLOOK.EXE",
    "${env:ProgramFiles(x86)}\Microsoft Office\Office16\OUTLOOK.EXE",
    "$env:LOCALAPPDATA\Microsoft\WindowsApps\OUTLOOK.EXE"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($outlookExe) {
    Write-Host "      Found Outlook at: $outlookExe" -ForegroundColor Gray
    Write-Host "      IMPORTANT: Before indexing emails/calendar, open Outlook and confirm inbox loads." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  WARNING: Outlook Classic desktop does NOT appear to be installed." -ForegroundColor Red
    Write-Host "  Email and calendar ingestion requires Outlook Classic for Windows." -ForegroundColor Red
    Write-Host "  Setup will continue. Install Outlook Classic before running start.ps1." -ForegroundColor Yellow
    Write-Host ""
}

function Install-WithWinget {
    param([string]$PackageId, [string]$DisplayName, [string[]]$ExtraArgs = @())
    Write-Host "      Installing $DisplayName via winget..." -ForegroundColor Gray
    $args = @(
        "install",
        "--id", $PackageId,
        "--exact",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent"
    ) + $ExtraArgs

    & winget @args 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335212) {
        Write-Host "      $DisplayName ready." -ForegroundColor Gray
        return $true
    }

    Write-Host "      winget returned exit code $LASTEXITCODE." -ForegroundColor Yellow
    return $false
}

function Download-File {
    param([string]$Url, [string]$Dest)
    Write-Host "      Downloading $(Split-Path $Dest -Leaf)..." -ForegroundColor Gray
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

function Refresh-EnvPath {
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machine;$user"
}

function Find-Python312 {
    foreach ($candidate in @("python", "python3", "python3.12")) {
        try {
            $ver = & $candidate --version 2>&1
            if ($ver -match "3\.12") {
                return $candidate
            }
        } catch {}
    }

    foreach ($path in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe",
        "C:\Program Files\Python312\python.exe"
    )) {
        if (Test-Path $path) { return $path }
    }

    return $null
}

function Get-EnvValue {
    param([string]$FilePath, [string]$Key)

    if (-not (Test-Path $FilePath)) {
        return $null
    }

    $line = Get-Content $FilePath | Where-Object { $_ -match "^\s*$Key=" } | Select-Object -First 1
    if (-not $line) {
        return $null
    }

    return (($line -split "=", 2)[1]).Trim()
}

function Set-EnvValue {
    param([string]$FilePath, [string]$Key, [string]$Value)

    $content = @()
    if (Test-Path $FilePath) {
        $content = Get-Content $FilePath
    }

    $updated = $false
    for ($i = 0; $i -lt $content.Count; $i++) {
        if ($content[$i] -match "^\s*$Key=") {
            $content[$i] = "$Key=$Value"
            $updated = $true
            break
        }
    }

    if (-not $updated) {
        $content += "$Key=$Value"
    }

    Set-Content -Path $FilePath -Value $content -Encoding ASCII
}

function Find-SourceSqlitePath {
    param(
        [string]$ExistingPath,
        [string]$PreferredPath,
        [string]$TargetPath
    )

    $targetAbs = $null
    if ($TargetPath) {
        if ([System.IO.Path]::IsPathRooted($TargetPath)) {
            $targetAbs = [System.IO.Path]::GetFullPath($TargetPath)
        } else {
            $targetAbs = [System.IO.Path]::GetFullPath((Join-Path $backendDir $TargetPath))
        }
    }

    if ($ExistingPath -and (Test-Path $ExistingPath)) {
        $existingAbs = [System.IO.Path]::GetFullPath($ExistingPath)
        if (-not $targetAbs -or $existingAbs -ne $targetAbs) {
            return $ExistingPath
        }
    }

    if ($PreferredPath -and (Test-Path $PreferredPath)) {
        $preferredAbs = (Resolve-Path $PreferredPath).Path
        if (-not $targetAbs -or ([System.IO.Path]::GetFullPath($preferredAbs) -ne $targetAbs)) {
            return $preferredAbs
        }
    }

    $candidates = @(
        (Join-Path $backendDir "local_search.db"),
        (Join-Path $repoRoot "..\Dev1\backend\data\local_search.db"),
        (Join-Path $repoRoot "..\Dev1\backend\local_search.db"),
        (Join-Path $repoRoot "..\..\Dev1\backend\data\local_search.db"),
        (Join-Path $repoRoot "..\..\Dev1\backend\local_search.db")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $resolved = (Resolve-Path $candidate).Path
            if (-not $targetAbs -or ([System.IO.Path]::GetFullPath($resolved) -ne $targetAbs)) {
                return $resolved
            }
        }
    }

    # Last-resort auto-detect: search nearby tree for a Dev1 backend source DB.
    $searchRoots = @(
        (Split-Path $repoRoot -Parent),
        $repoRoot
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique

    foreach ($root in $searchRoots) {
        $matches = Get-ChildItem -Path $root -Filter "local_search.db" -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\Dev1\\backend\\local_search\.db$" }

        foreach ($m in $matches) {
            $resolved = $m.FullName
            if (-not $targetAbs -or ([System.IO.Path]::GetFullPath($resolved) -ne $targetAbs)) {
                return $resolved
            }
        }
    }

    return $null
}

function Ensure-LocalDatabaseFiles {
    param(
        [string]$PythonExe,
        [string]$BackendPath,
        [string]$SqlitePath,
        [string]$TrackingPath
    )

    Write-Host "      Ensuring local DB files exist under backend\\data ..." -ForegroundColor Gray

    $env:SQLITE_PATH = $SqlitePath
    $env:TRACKING_DB_PATH = $TrackingPath
    Set-Location $BackendPath

    & $PythonExe -c "import os, sqlite3; from pathlib import Path; paths=[os.getenv('SQLITE_PATH','./data/local_search.db'), os.getenv('TRACKING_DB_PATH','./data/query_tracking.db')]; [Path(p).parent.mkdir(parents=True, exist_ok=True) or sqlite3.connect(p).close() for p in paths]; print('DB_FILES_READY')"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      Warning: Could not pre-create local DB files." -ForegroundColor Yellow
    }
}

function Get-BackendAbsolutePath {
    param([string]$BackendPath, [string]$DbPath)

    if (-not $DbPath) {
        return ""
    }

    if ([System.IO.Path]::IsPathRooted($DbPath)) {
        return $DbPath
    }

    return (Join-Path $BackendPath $DbPath)
}

function Test-LocalSearchDataReady {
    param(
        [string]$PythonExe,
        [string]$BackendPath,
        [string]$SqlitePath
    )

    if (-not $SqlitePath) {
        return $false
    }

    Set-Location $BackendPath
    $env:SQLITE_PATH = $SqlitePath

    $checkScript = @'
import os
import sqlite3
import sys

db_path = os.getenv("SQLITE_PATH", "./data/local_search.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
ok = ("emails" in tables) or ("meetings" in tables)
conn.close()
sys.exit(0 if ok else 1)
'@

    $checkScript | & $PythonExe -
    return ($LASTEXITCODE -eq 0)
}

function Invoke-InitialSqliteLoad {
    param(
        [string]$SourceSqlitePath,
        [string]$LoadMode = "full"
    )

    Write-Host "[8/8] Running initial SQLite load ($LoadMode) ..." -ForegroundColor Yellow

    if (-not $SourceSqlitePath -or -not (Test-Path $SourceSqlitePath)) {
        Write-Host "      No source SQLite found. Attempting direct Outlook ingest for first load..." -ForegroundColor Yellow

        # Setup runs elevated for installer steps. Outlook COM automation is commonly unavailable
        # from elevated context, even when it works later from the app/Admin tab as normal user.
        if ($isAdmin -and -not ($env:FORCE_SETUP_OUTLOOK_COM -eq "1")) {
            Write-Host "      Skipping direct Outlook ingest during elevated setup to avoid COM session mismatch." -ForegroundColor Yellow
            Write-Host "      Next step: run .\\start.ps1 as your normal user, then use Admin -> Full Load." -ForegroundColor Yellow
            Write-Host "      (Set FORCE_SETUP_OUTLOOK_COM=1 only if you explicitly want to force this attempt.)" -ForegroundColor DarkYellow
            return
        }

        $directRunner = @'
import json
import traceback
from app.services.outlook_com_ingest import ingest_from_outlook_com

try:
    result = ingest_from_outlook_com(max_items=800)
    print(json.dumps({"status": "ok", "mode": "outlook_com", "result": result}))
except Exception:
    print(json.dumps({
        "status": "error",
        "mode": "outlook_com",
        "message": "direct_outlook_ingest_failed",
        "traceback": traceback.format_exc()
    }))
'@

        Set-Location $backendDir
        $directPayload = $directRunner | & $pythonVenv -

        try {
            $directResult = $directPayload | ConvertFrom-Json
        } catch {
            Write-Host "      Direct Outlook ingest returned non-JSON output." -ForegroundColor Yellow
            if ($directPayload) {
                $preview = [string]$directPayload
                if ($preview.Length -gt 600) { $preview = $preview.Substring(0, 600) + " ..." }
                Write-Host "      Raw output: $preview" -ForegroundColor DarkYellow
            }
            Write-Host "      You can run Full Load later from Admin after setting SOURCE_SQLITE_PATH." -ForegroundColor Yellow
            return
        }

        if ($directResult.status -eq "ok") {
            $emailRows = [int]($directResult.result.emails_upserted)
            $meetingRows = [int]($directResult.result.meetings_upserted)
            Write-Host "      Initial load completed via Outlook COM." -ForegroundColor Green
            Write-Host "      Emails upserted: $emailRows" -ForegroundColor Gray
            Write-Host "      Meetings upserted: $meetingRows" -ForegroundColor Gray
        } else {
            Write-Host "      Direct Outlook ingest failed during setup." -ForegroundColor Yellow
            if ($directResult.traceback) {
                Write-Host "      Details: $($directResult.traceback)" -ForegroundColor DarkYellow
            }
            Write-Host "      You can run Full Load later from Admin after setting SOURCE_SQLITE_PATH." -ForegroundColor Yellow
        }
        return
    }

    $env:SOURCE_SQLITE_PATH = $SourceSqlitePath
    Set-Location $backendDir

    $runner = @'
import json
import traceback
from app.main import _run_admin_load

mode = "__LOAD_MODE__"
try:
    result = _run_admin_load(mode)
    print(json.dumps(result))
except Exception:
    print(json.dumps({
        "status": "error",
        "message": "initial_load_exception",
        "traceback": traceback.format_exc()
    }))
'@

    $runner = $runner.Replace("__LOAD_MODE__", $LoadMode)
    $payload = $runner | & $pythonVenv -
    if ($LASTEXITCODE -ne 0 -and -not $payload) {
        Write-Host "      Initial SQLite load failed to execute (python runtime error)." -ForegroundColor Yellow
        Write-Host "      You can run it later from the Admin tab after setup." -ForegroundColor Yellow
        return
    }

    try {
        $result = $payload | ConvertFrom-Json
    } catch {
        Write-Host "      Initial load returned non-JSON output." -ForegroundColor Yellow
        if ($payload) {
            $preview = [string]$payload
            if ($preview.Length -gt 600) { $preview = $preview.Substring(0, 600) + " ..." }
            Write-Host "      Raw output: $preview" -ForegroundColor DarkYellow
        }
        Write-Host "      You can run it later from the Admin tab after setup." -ForegroundColor Yellow
        return
    }

    if ($result.status -eq "ok") {
        $insertedTotal = 0
        if ($result.tables) {
            foreach ($table in $result.tables) {
                $insertedTotal += [int]$table.inserted
            }
        }
        Write-Host "      Initial load completed ($LoadMode)." -ForegroundColor Green
        Write-Host "      Source DB: $($result.source_db)" -ForegroundColor Gray
        Write-Host "      Target DB: $($result.target_db)" -ForegroundColor Gray
        Write-Host "      Rows copied this run: $insertedTotal" -ForegroundColor Gray
    } else {
        Write-Host "      Initial load skipped or failed: $($result.message)" -ForegroundColor Yellow
        if ($result.traceback) {
            Write-Host "      Details: $($result.traceback)" -ForegroundColor DarkYellow
        }
        Write-Host "      You can run it later from the Admin tab." -ForegroundColor Yellow
    }
}

Write-Host "[1/7] Python 3.12 ..." -ForegroundColor Yellow
$python = Find-Python312
if ($python) {
    Write-Host "      Found Python 3.12: $python" -ForegroundColor Gray
} else {
    Write-Host "      Python 3.12 not found - installing..." -ForegroundColor Yellow
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $wingetOk = Install-WithWinget "Python.Python.3.12" "Python 3.12"
    }
    if (-not $wingetOk) {
        $pyInstaller = Join-Path $tmpDir "python-3.12-installer.exe"
        Download-File "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe" $pyInstaller
        Write-Host "      Running Python installer (silent)..." -ForegroundColor Gray
        Start-Process -FilePath $pyInstaller -ArgumentList @(
            "/quiet",
            "InstallAllUsers=1",
            "PrependPath=1",
            "Include_pip=1"
        ) -Wait -NoNewWindow
    }

    Refresh-EnvPath
    $python = Find-Python312
    if (-not $python) {
        Write-Host "      ERROR: Python 3.12 install failed or python is not on PATH." -ForegroundColor Red
        Write-Host "      Install manually from https://python.org then re-run this script." -ForegroundColor Red
        exit 1
    }
    Write-Host "      Python 3.12 installed." -ForegroundColor Green
}

Write-Host "[2/7] Visual Studio C++ Build Tools ..." -ForegroundColor Yellow
$clFound = Get-ChildItem "C:\Program Files (x86)\Microsoft Visual Studio" -Filter "cl.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $clFound) {
    $clFound = Get-ChildItem "C:\Program Files\Microsoft Visual Studio" -Filter "cl.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($clFound) {
    Write-Host "      C++ compiler found: $($clFound.FullName)" -ForegroundColor Gray
} else {
    Write-Host "      C++ Build Tools not found - installing (this can take 5-10 min)..." -ForegroundColor Yellow
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $wingetOk = Install-WithWinget "Microsoft.VisualStudio.2022.BuildTools" "VS 2022 Build Tools" @(
            "--override",
            "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        )
    }
    if (-not $wingetOk) {
        $vsInstaller = Join-Path $tmpDir "vs_buildtools.exe"
        Download-File "https://aka.ms/vs/17/release/vs_buildtools.exe" $vsInstaller
        Write-Host "      Running VS Build Tools installer (this is slow, please wait)..." -ForegroundColor Gray
        Start-Process -FilePath $vsInstaller -ArgumentList @(
            "--quiet",
            "--wait",
            "--norestart",
            "--add", "Microsoft.VisualStudio.Workload.VCTools",
            "--includeRecommended"
        ) -Wait -NoNewWindow
    }
    Write-Host "      C++ Build Tools installed." -ForegroundColor Green
    Write-Host "      NOTE: A reboot may be needed before pip can use the compiler." -ForegroundColor Yellow
}

Write-Host "[3/7] Ollama ..." -ForegroundColor Yellow
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $ollamaExe) { $ollamaCmd = $ollamaExe }
}

if ($ollamaCmd) {
    Write-Host "      Ollama already installed." -ForegroundColor Gray
} else {
    Write-Host "      Ollama not found - installing..." -ForegroundColor Yellow
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $wingetOk = Install-WithWinget "Ollama.Ollama" "Ollama"
    }
    if (-not $wingetOk) {
        $ollamaInstaller = Join-Path $tmpDir "OllamaSetup.exe"
        Download-File "https://ollama.com/download/OllamaSetup.exe" $ollamaInstaller
        Write-Host "      Running Ollama installer (silent)..." -ForegroundColor Gray
        Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -Wait -NoNewWindow
    }

    Refresh-EnvPath
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) { $ollamaCmd = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
    Write-Host "      Ollama installed." -ForegroundColor Green
}

Write-Host "[4/7] Creating virtual environment in backend\\.venv ..." -ForegroundColor Yellow
if (-not (Test-Path $venvDir)) {
    & $python -m venv $venvDir
    Write-Host "      Created." -ForegroundColor Gray
} else {
    Write-Host "      Already exists, skipping." -ForegroundColor Gray
}

Write-Host "[5/7] Installing Python dependencies..." -ForegroundColor Yellow
$pip = Join-Path $venvDir "Scripts\pip.exe"
$pythonVenv = Join-Path $venvDir "Scripts\python.exe"

# Always upgrade pip first to avoid legacy installer issues on some machines.
& $pythonVenv -m pip install --upgrade pip
& $pythonVenv -m pip --version

& $pythonVenv -m pip install --upgrade setuptools wheel
& $pip install -r (Join-Path $backendDir "requirements.txt")
Write-Host "      Done." -ForegroundColor Gray

Write-Host "[6/8] Configuring .env ..." -ForegroundColor Yellow
if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "      Created backend\\.env from .env.example." -ForegroundColor Gray
} else {
    Write-Host "      backend\\.env already exists, not overwritten." -ForegroundColor Gray
}

$sqlitePath = Get-EnvValue -FilePath $envFile -Key "SQLITE_PATH"
if (-not $sqlitePath) {
    $sqlitePath = "./data/local_search.db"
    Set-EnvValue -FilePath $envFile -Key "SQLITE_PATH" -Value $sqlitePath
}

$trackingPath = Get-EnvValue -FilePath $envFile -Key "TRACKING_DB_PATH"
if (-not $trackingPath) {
    $trackingPath = "./data/query_tracking.db"
    Set-EnvValue -FilePath $envFile -Key "TRACKING_DB_PATH" -Value $trackingPath
}

Write-Host "      SQLITE_PATH: $sqlitePath" -ForegroundColor Gray
Write-Host "      TRACKING_DB_PATH: $trackingPath" -ForegroundColor Gray
Ensure-LocalDatabaseFiles -PythonExe $pythonVenv -BackendPath $backendDir -SqlitePath $sqlitePath -TrackingPath $trackingPath

$sqlitePathAbs = Get-BackendAbsolutePath -BackendPath $backendDir -DbPath $sqlitePath
$trackingPathAbs = Get-BackendAbsolutePath -BackendPath $backendDir -DbPath $trackingPath
Write-Host "      Local search DB file: $sqlitePathAbs" -ForegroundColor Gray
Write-Host "      Tracking DB file: $trackingPathAbs" -ForegroundColor Gray

$existingSourceSqlite = Get-EnvValue -FilePath $envFile -Key "SOURCE_SQLITE_PATH"
$resolvedSourceSqlite = Find-SourceSqlitePath -ExistingPath $existingSourceSqlite -PreferredPath $DefaultSourceSqlitePath -TargetPath $sqlitePath

$localSearchReady = Test-LocalSearchDataReady -PythonExe $pythonVenv -BackendPath $backendDir -SqlitePath $sqlitePath

if ($resolvedSourceSqlite) {
    Set-EnvValue -FilePath $envFile -Key "SOURCE_SQLITE_PATH" -Value $resolvedSourceSqlite
    Write-Host "      SOURCE_SQLITE_PATH configured: $resolvedSourceSqlite" -ForegroundColor Gray
} else {
    if ($localSearchReady) {
        Write-Host "      Local search DB already contains data. Skipping SOURCE_SQLITE_PATH prompt and initial load." -ForegroundColor Gray
        $resolvedSourceSqlite = ""
    } else {
    Write-Host "      No source SQLite auto-detected." -ForegroundColor Yellow
    Write-Host "      Initial load will be skipped for now (non-interactive setup)." -ForegroundColor Yellow
    Write-Host "      To enable Admin Full Load later, set SOURCE_SQLITE_PATH in backend\\.env to a valid local_search.db source path." -ForegroundColor Yellow
    }
}

Write-Host "[7/8] Pulling Ollama models (nomic-embed-text + mistral) ..." -ForegroundColor Yellow
Write-Host "      This downloads ~4-5 GB and can take several minutes on first run." -ForegroundColor Gray

$ollamaProcess = Get-Process ollama -ErrorAction SilentlyContinue
if (-not $ollamaProcess) {
    Write-Host "      Starting Ollama service in background..." -ForegroundColor Gray
    Start-Process -FilePath "$ollamaCmd" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

try {
    & $ollamaCmd pull nomic-embed-text
    & $ollamaCmd pull mistral
    Write-Host "      Models ready." -ForegroundColor Green
} catch {
    Write-Host "      Model pull failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "      Run manually after setup: ollama pull nomic-embed-text ; ollama pull mistral" -ForegroundColor Yellow
}

if ($localSearchReady) {
    Write-Host "[8/8] Skipping initial SQLite load (local DB already has data)." -ForegroundColor Yellow
} else {
    Invoke-InitialSqliteLoad -SourceSqlitePath $resolvedSourceSqlite -LoadMode "full"
}

Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Run .\\start.ps1 to launch the app" -ForegroundColor Cyan
Write-Host "    2. If initial load was skipped, set SOURCE_SQLITE_PATH in backend\\.env and run Admin Load later" -ForegroundColor Cyan
Write-Host ""
Write-Host "  If a reboot prompt appeared during C++ Build Tools install," -ForegroundColor Yellow
Write-Host "  reboot first, then run .\\start.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Installation log saved at: $LogPath" -ForegroundColor Gray
} catch {
    Write-Host "" 
    Write-Host "=== Setup failed ===" -ForegroundColor Red
    Write-Host "Reason: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Review log: $LogPath" -ForegroundColor Yellow
    throw
} finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
