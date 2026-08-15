@echo off
rem Entry for Task Scheduler (conhost --headless). Supervisor loop keeps
rem local AI gateway proxies 8787/8788/3003 alive; see proxies-supervisor.py.
"C:\Users\zhugu\scoop\apps\python313\current\python.exe" "C:\Users\zhugu\.omp\guardian\proxies-supervisor.py"
exit /b %ERRORLEVEL%
