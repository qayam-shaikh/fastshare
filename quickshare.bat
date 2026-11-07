@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python >nul 2>nul
        if errorlevel 1 goto missing_python
        python -m venv .venv
    )
    if errorlevel 1 goto setup_failed
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto unsupported_python

if not exist ".venv\quickshare-dependencies-installed" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
    type nul > ".venv\quickshare-dependencies-installed"
)

".venv\Scripts\python.exe" quickshare.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

:missing_python
echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/windows/
echo Make sure the Python launcher or python.exe is available on PATH.
pause
exit /b 1

:unsupported_python
echo The Python in .venv is older than 3.10. Remove the .venv folder and install a newer Python.
pause
exit /b 1

:setup_failed
echo QuickShare setup did not complete. Check your internet connection and Python installation.
pause
exit /b 1
