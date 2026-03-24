@echo off
:: Double-click this file every time you want to use Outlook Assistant.
set "SCRIPT=%~dp0start.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0Outlook-Assistant-main\start.ps1"

if not exist "%SCRIPT%" (
	echo.
	echo ERROR: start.ps1 not found.
	echo Expected one of:
	echo   %~dp0start.ps1
	echo   %~dp0Outlook-Assistant-main\start.ps1
	echo.
	echo Please extract the full Outlook-Assistant ZIP, then run this file again.
	pause
	exit /b 1
)

powershell.exe -ExecutionPolicy Bypass -File "%SCRIPT%"
pause
