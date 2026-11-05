@echo off
setlocal enabledelayedexpansion

set "SOUNDME_DIR=%~dp0"
if "%SOUNDME_DIR:~-1%"=="\" set "SOUNDME_DIR=%SOUNDME_DIR:~0,-1%"
cd /d "%SOUNDME_DIR%"
set "DESKTOP_LOCAL_MODE=1"
set "BACKEND_PORT=8000"

:: --- Locate Python Interpreter ---
set "PYTHON_EXE="
if exist "%SOUNDME_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%SOUNDME_DIR%\.venv\Scripts\python.exe"
) else if exist "%SOUNDME_DIR%\.venv-recovery\Scripts\python.exe" (
    set "PYTHON_EXE=%SOUNDME_DIR%\.venv-recovery\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] No valid Python environment found.
    echo Please create a virtual environment and install requirements:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:: Do not start a duplicate Uvicorn if a healthy backend already owns port 8000.
"%PYTHON_EXE%" -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:%BACKEND_PORT%/api/health', timeout=3)" >nul 2>&1
if %errorlevel% equ 0 (
    echo Backend already running on port %BACKEND_PORT% - skipping startup.
    exit /b 0
)

echo Starting backend on 127.0.0.1:%BACKEND_PORT%...
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%
