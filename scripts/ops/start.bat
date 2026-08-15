@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=C:\Users\zhugu\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe"

:start
echo [%date% %time%] Starting Guardian...
"%PYTHON%" guardian.py
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="75" exit /b 0
echo [%date% %time%] Guardian exited (code %EXIT_CODE%), restarting in 10s...
timeout /t 10 /nobreak >nul
goto start