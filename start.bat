@echo off
setlocal
cd /d "%~dp0"

where bash >nul 2>&1
if errorlevel 1 (
  echo Git Bash was not found.
  echo Please install Git for Windows and run this file again.
  pause
  exit /b 1
)

bash "%~dp0start.sh"
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" echo HEX Private Server stopped with error code %EXITCODE%.
pause
exit /b %EXITCODE%
