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
#   6. Copies .env.example → backend/.env
#   7. Pulls required Ollama models
# ============================================================

#Requires -Version 5.1
$ErrorActionPreference = "Stop"

# ── Admin check ──────────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]"Administrator")
if (-not $isAdmin) {
    Write-Host ""
    Write-Host "Re-launching as Administrator (required for software installs)..." -ForegroundColor Yellow
    $script = $MyInvocation.MyCommand.Path
    if (-not $script) { $script = $PSCommandPath }
    Start-Process powershell.exe "-ExecutionPolicy Bypass -File `"$script`"" -Verb RunAs
    exit
}

# Allow running scripts in this session
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
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

# ─────────────────────────────────────────────────────────────────────────────
# PRE-FLIGHT: Outlook Classic desktop check
# Email and calendar ingestion uses Windows COM automation (win32com), which
# talks directly to the Outlook desktop app.  The app must be installed AND
# signed in before running any indexing.  This check warns — it does not block
# setup because Outlook may simply not have been opened yet on this machine.
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[PRE] Checking for Outlook Classic (desktop) installation..." -ForegroundColor Yellow

$outlookExe = @(
    "$env:ProgramFiles\Microsoft Office\root\Office16\OUTLOOK.EXE",
    "${env:ProgramFiles(x86)}\Microsoft Office\root\Office16\OUTLOOK.EXE",
    "$env:ProgramFiles\Microsoft Office\Office16\OUTLOOK.EXE",
    "${env:ProgramFiles(x86)}\Microsoft Office\Office16\OUTLOOK.EXE",
    "$env:LOCALAPPDATA\Microsoft\WindowsApps\OUTLOOK.EXE"   # store/new Outlook — not compatible
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($outlookExe) {
    Write-Host "      Found Outlook at: $outlookExe" -ForegroundColor Gray
    Write-Host "      IMPORTANT: Before indexing emails/calendar, open Outlook and confirm" -ForegroundColor Yellow
    Write-Host "      your inbox loads (not a login prompt). COM calls will fail if" -ForegroundColor Yellow
    Write-Host "      Outlook is closed or offline." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  WARNING: Outlook Classic (desktop) does NOT appear to be installed." -ForegroundColor Red
    Write-Host "  Email and calendar ingestion uses Windows COM automation and requires" -ForegroundColor Red
    Write-Host "  Outlook Classic for Windows (Office 365 Desktop or perpetual Office)." -ForegroundColor Red
    Write-Host "  The new Outlook web-wrapper app is NOT supported." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install options:" -ForegroundColor Yellow
    Write-Host "    - Your organisation's Software Center / IT portal" -ForegroundColor Yellow
    Write-Host "    - Microsoft 365 admin centre > Apps > Deploy" -ForegroundColor Yellow
    Write-Host "    - https://www.office.com  (sign-in → Install Office)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Setup will continue — install Outlook Classic before running start.ps1." -ForegroundColor Yellow
    Write-Host ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper: try winget, fall back to direct download installer
# ─────────────────────────────────────────────────────────────────────────────
function Install-WithWinget {
    param([string]$PackageId, [string]$DisplayName, [string[]]$ExtraArgs = @())
    Write-Host "      Installing $DisplayName via winget..." -ForegroundColor Gray
    $result = winget install --id $PackageId --exact --accept-source-agreements `
                --accept-package-agreements --silent @ExtraArgs 2>&1
    # winget exit 0 = installed, -1978335212 (0x8A150014) = already installed
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

# Refresh PATH inside this session after installs
function Refresh-EnvPath {
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machine;$user"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Python 3.12
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[1/7] Python 3.12 ..." -ForegroundColor Yellow

$python = $null
foreach ($candidate in @("python", "python3", "python3.12")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "3\.12") { $python = $candidate; break }
    } catch {}
}

if ($python) {
    Write-Host "      Found: $($python) $($ver)" -ForegroundColor Gray
} else {
    Write-Host "      Python 3.12 not found — installing..." -ForegroundColor Yellow
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $wingetOk = Install-WithWinget "Python.Python.3.12" "Python 3.12"
    }
    if (-not $wingetOk) {
        # Direct download fallback
        $pyInstaller = Join-Path $tmpDir "python-3.12-installer.exe"
        Download-File "https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe" $pyInstaller
        Write-Host "      Running Python installer (silent)..." -ForegroundColor Gray
        Start-Process -FilePath $pyInstaller `
            -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1" `
            -Wait -NoNewWindow
    }
    Refresh-EnvPath
    # Locate freshly installed python
    foreach ($candidate in @("python", "python3", "python3.12")) {
        try {
            $ver = & $candidate --version 2>&1
            if ($ver -match "3\.12") { $python = $candidate; break }
        } catch {}
    }
    if (-not $python) {
        # Try common install locations
        $guesses = @(
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "C:\Python312\python.exe",
            "C:\Program Files\Python312\python.exe"
        )
        foreach ($g in $guesses) {
            if (Test-Path $g) { $python = $g; break }
        }
    }
    if (-not $python) {
        Write-Host "      ERROR: Python 3.12 install failed or python not on PATH." -ForegroundColor Red
        Write-Host "      Install manually from https://python.org then re-run this script." -ForegroundColor Red
        exit 1
    }
    Write-Host "      Python 3.12 installed." -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Visual Studio C++ Build Tools
# Required by: chromadb, numpy (native wheels), and some other deps.
# We install the minimal "Build Tools" workload, not the full IDE.
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[2/7] Visual Studio C++ Build Tools ..." -ForegroundColor Yellow

# Quick check: cl.exe somewhere in VS install paths
$clFound = Get-ChildItem "C:\Program Files (x86)\Microsoft Visual Studio" `
    -Filter "cl.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $clFound) {
    $clFound = Get-ChildItem "C:\Program Files\Microsoft Visual Studio" `
        -Filter "cl.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($clFound) {
    Write-Host "      C++ compiler found: $($clFound.FullName)" -ForegroundColor Gray
} else {
    Write-Host "      C++ Build Tools not found — installing (this can take 5-10 min)..." -ForegroundColor Yellow
    $wingetOk = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        # Microsoft.VisualStudio.2022.BuildTools is the winget package
        $wingetOk = Install-WithWinget `
            "Microsoft.VisualStudio.2022.BuildTools" `
            "VS 2022 Build Tools" `
            @("--override", "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended")
    }
    if (-not $wingetOk) {
        # Direct download fallback
        $vsInstaller = Join-Path $tmpDir "vs_buildtools.exe"
        Download-File "https://aka.ms/vs/17/release/vs_buildtools.exe" $vsInstaller
        Write-Host "      Running VS Build Tools installer (this is slow, please wait)..." -ForegroundColor Gray
        Start-Process -FilePath $vsInstaller `
            -ArgumentList "--quiet", "--wait", "--norestart", `
                "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended" `
            -Wait -NoNewWindow
    }
    Write-Host "      C++ Build Tools installed." -ForegroundColor Green
    Write-Host "      NOTE: A reboot may be needed before pip can use the compiler." -ForegroundColor Yellow
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Ollama
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[3/7] Ollama ..." -ForegroundColor Yellow

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    # Check default install path even if not on PATH yet
    $ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $ollamaExe) { $ollamaCmd = $ollamaExe }
}

if ($ollamaCmd) {
    Write-Host "      Ollama already installed." -ForegroundColor Gray
} else {
    Write-Host "      Ollama not found — installing..." -ForegroundColor Yellow
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

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Python virtual environment
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[4/7] Creating virtual environment in backend\.venv ..." -ForegroundColor Yellow
if (-not (Test-Path $venvDir)) {
    & $python -m venv $venvDir
    Write-Host "      Created." -ForegroundColor Gray
} else {
    Write-Host "      Already exists, skipping." -ForegroundColor Gray
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Python dependencies
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[5/7] Installing Python dependencies..." -ForegroundColor Yellow
$pip = Join-Path $venvDir "Scripts\pip.exe"
& $pip install --upgrade pip setuptools wheel --quiet
& $pip install -r (Join-Path $backendDir "requirements.txt")
Write-Host "      Done." -ForegroundColor Gray

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Configure .env
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[6/7] Configuring .env ..." -ForegroundColor Yellow
if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "      Created backend\.env from .env.example." -ForegroundColor Gray
    Write-Host ""
    Write-Host "      >>> ACTION REQUIRED <<<" -ForegroundColor Magenta
    Write-Host "      Open backend\.env and set SOURCE_SQLITE_PATH to the path of" -ForegroundColor Magenta
    Write-Host "      your Outlook data file (local_search.db exported from Dev1)." -ForegroundColor Magenta
    Write-Host ""
} else {
    Write-Host "      backend\.env already exists, not overwritten." -ForegroundColor Gray
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Pull Ollama models
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[7/7] Pulling Ollama models (nomic-embed-text + mistral) ..." -ForegroundColor Yellow
Write-Host "      This downloads ~4-5 GB and can take several minutes on first run." -ForegroundColor Gray

# Ollama serve must be running for pulls to work
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
    Write-Host "      Run manually after setup:  ollama pull nomic-embed-text  &&  ollama pull mistral" -ForegroundColor Yellow
}

# ── Cleanup temp files ───────────────────────────────────────────────────────
Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Open backend\.env and set SOURCE_SQLITE_PATH" -ForegroundColor Cyan
Write-Host "    2. Run  .\start.ps1  to launch the app" -ForegroundColor Cyan
Write-Host ""
Write-Host "  If a reboot prompt appeared during C++ Build Tools install," -ForegroundColor Yellow
Write-Host "  reboot first, then run  .\start.ps1" -ForegroundColor Yellow
Write-Host ""
