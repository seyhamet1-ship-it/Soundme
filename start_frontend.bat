@echo off
set "SOUNDME_DIR=%~dp0"
if "%SOUNDME_DIR:~-1%"=="\" set "SOUNDME_DIR=%SOUNDME_DIR:~0,-1%"
cd /d "%SOUNDME_DIR%\frontend"

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm is not found in PATH. Please install Node.js.
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo [WARNING] node_modules not found. Running npm install...
    call npm install
)

npm run dev
