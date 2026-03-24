@echo off
set "ROOT=%~dp0..\"
if exist "%ROOT%setup.ps1" (
  powershell.exe -ExecutionPolicy Bypass -File "%ROOT%setup.ps1" -DefaultSourceSqlitePath "%ROOT%backend\local_search.db"
  pause
  exit /b %errorlevel%
)

echo.
echo ERROR: setup.ps1 not found in repository root.
echo Expected: %ROOT%setup.ps1
echo.
echo Download or extract the full Outlook-Assistant repository, then try again.
pause
exit /b 1
