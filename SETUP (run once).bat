@echo off
:: Double-click this file once to install everything.
:: You only need to run this ONE TIME on a new machine.
set "SCRIPT=%~dp0setup.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0Outlook-Assistant-main\setup.ps1"

if not exist "%SCRIPT%" (
	echo.
	echo ERROR: setup.ps1 not found.
	echo Expected one of:
	echo   %~dp0setup.ps1
	echo   %~dp0Outlook-Assistant-main\setup.ps1
	echo.
	echo Please extract the full Outlook-Assistant ZIP, then run this file again.
	pause
	exit /b 1
)

powershell.exe -ExecutionPolicy Bypass -File "%SCRIPT%"
pause
