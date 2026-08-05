@echo off
setlocal
rem Repo-relative entry for Task Scheduler / manual runs.
rem 2026-08-05: VPS NewAPI retired -> local smoke instead of SSH analyzer.
cd /d "%~dp0..\.."
if errorlevel 1 exit /b 1

set "LOG=%CD%\.tmp-newapi-dx-ops.log"
set "PYTHON=C:\Users\zhugu\scoop\apps\python313\current\python.exe"
if not exist "%PYTHON%" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [%DATE% %TIME%] python not found>> "%LOG%"
    echo python not found 1>&2
    exit /b 1
  )
  set "PYTHON=python"
)

"%PYTHON%" "%CD%\scripts\ops\newapi-local-smoke.py" %* >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
