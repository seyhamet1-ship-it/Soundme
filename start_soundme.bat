@echo off
:: Soundme Local Startup Script
:: Double-click to start both backend (FastAPI) and frontend (Vite) locally

setlocal enabledelayedexpansion

set "SOUNDME_DIR=%~dp0"
if "%SOUNDME_DIR:~-1%"=="\" set "SOUNDME_DIR=%SOUNDME_DIR:~0,-1%"
set "DESKTOP_LOCAL_MODE=1"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "BROWSER_URL=http://127.0.0.1:5173"

echo ============================================
echo Soundme Local Startup
echo ============================================
echo.

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

:: --- Check if backend is already running ---
echo Checking backend status...
"%PYTHON_EXE%" -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:%BACKEND_PORT%/api/health', timeout=3)" >nul 2>&1
if %errorlevel% equ 0 (
    echo Backend already running on port %BACKEND_PORT% - skipping backend startup.
) else (
    echo Starting backend on port %BACKEND_PORT%...
    start "Soundme Backend" cmd /k "cd /d "%SOUNDME_DIR%" && "%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"
    :: Wait for backend to become healthy
    timeout /t 5 >nul
)

echo.
echo Starting frontend on port %FRONTEND_PORT%...

:: Check if frontend is already running
"%PYTHON_EXE%" -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:%FRONTEND_PORT%', timeout=3)" >nul 2>&1
if %errorlevel% equ 0 (
    echo Frontend already running on port %FRONTEND_PORT% - skipping frontend startup.
) else (
    if not exist "%SOUNDME_DIR%\frontend\node_modules" (
        echo [WARNING] frontend\node_modules not found. You may need to run 'npm install' in frontend\.
    )
    echo Starting frontend...
    start "Soundme Frontend" cmd /k "cd /d "%SOUNDME_DIR%\frontend" && npm run dev -- --port %FRONTEND_PORT%"
)

echo.
echo Opening browser to %BROWSER_URL%...
start "" "%BROWSER_URL%"

echo.
echo Soundme is now running!
echo - Backend API: http://127.0.0.1:%BACKEND_PORT%
echo - Frontend:   http://127.0.0.1:%FRONTEND_PORT%
echo.
echo To stop the application:
echo   - Close the backend CMD window
echo   - Close the frontend CMD window
echo.

timeout /t 5 >nul
exit /b 0
