@echo off
setlocal
cd /d "%~dp0"

call :find_bash
if not defined BASH (
  echo Git Bash was not found.
  echo.
  echo Install Git for Windows ^(it includes Git Bash^), or point GIT_BASH at
  echo bash.exe, for example:
  echo     set GIT_BASH=C:\Program Files\Git\bin\bash.exe
  echo.
  pause
  exit /b 1
)

echo Using Git Bash: %BASH%
"%BASH%" "%~dp0scripts\start-game.sh"
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" echo HEX launcher exited with code %EXITCODE%.
pause
exit /b %EXITCODE%

rem ---------------------------------------------------------------------------
rem Locate bash.exe.  `where bash` alone is not enough: a standard Git for
rem Windows install puts only ...\Git\cmd on PATH, and that folder holds git.exe
rem but no bash.exe -- bash lives in ...\Git\bin.
rem ---------------------------------------------------------------------------
:find_bash
if defined GIT_BASH (
  if exist "%GIT_BASH%" (
    set "BASH=%GIT_BASH%"
    goto :eof
  )
)

for %%B in (
  "%ProgramFiles%\Git\bin\bash.exe"
  "%ProgramFiles(x86)%\Git\bin\bash.exe"
  "%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
) do (
  if not defined BASH if exist "%%~B" set "BASH=%%~B"
)
if defined BASH goto :eof

rem Git\bin may already be on PATH.
for /f "delims=" %%G in ('where bash 2^>nul') do (
  if not defined BASH set "BASH=%%~G"
)
if defined BASH goto :eof

rem Last resort: derive it from wherever git.exe was found (...\Git\cmd\git.exe).
for /f "delims=" %%G in ('where git 2^>nul') do call :derive_bash "%%~G"
goto :eof

:derive_bash
if defined BASH goto :eof
set "CAND=%~dp1..\bin\bash.exe"
if exist "%CAND%" set "BASH=%CAND%"
goto :eof