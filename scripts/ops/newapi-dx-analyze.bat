@echo off
setlocal
rem Repo-relative entry for Task Scheduler / manual runs.
cd /d "%~dp0..\.."
if errorlevel 1 exit /b 1

set "LOG=%CD%\.tmp-newapi-dx-ops.log"
where python >nul 2>&1
if errorlevel 1 (
  echo [%DATE% %TIME%] python not found on PATH>> "%LOG%"
  echo python not found on PATH 1>&2
  exit /b 1
)

python "%CD%\scripts\ops\newapi-dx-analyze.py" %* >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
