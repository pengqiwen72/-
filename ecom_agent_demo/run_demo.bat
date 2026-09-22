@echo off
cd /d "%~dp0"

echo ============================================================
echo   E-commerce CS Agent Demo (Project 1)
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] No venv found. Creating virtualenv and installing deps...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    echo        Dependencies installed.
) else (
    echo [1/2] Virtualenv detected. Skip dependency install.
)

echo [2/2] Running demo...
echo.
".venv\Scripts\python.exe" demo.py

echo.
echo ============================================================
echo   Done.
echo ============================================================
pause
