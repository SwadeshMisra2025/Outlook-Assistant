@echo off
set "ROOT=%~dp0..\"
if exist "%ROOT%start.ps1" (
  powershell.exe -ExecutionPolicy Bypass -File "%ROOT%start.ps1"
  pause
  exit /b %errorlevel%
)

echo.
echo ERROR: start.ps1 not found in repository root.
echo Expected: %ROOT%start.ps1
echo.
echo Download or extract the full Outlook-Assistant repository, then try again.
pause
exit /b 1
