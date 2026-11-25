@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================================
:: SmoovPlay + Native Xbox One-Click Launcher
:: Double-click to build (if needed), sign in (first run), start Xbox Remote
:: Play, and launch SmoovPlay in low-delay DXGI fullscreen.
::
:: Optional overrides (edit here or set in environment before running):
::   XBOX_RES      = 1080p | 1440p | 720p
::   XBOX_BITRATE  = max video bitrate in kbps (default 20000)
::   XBOX_MODE     = --home  (or --home "Console Name", or --cloud <titleId>)
:: ============================================================================
if not defined XBOX_RES set "XBOX_RES=1080p"
if not defined XBOX_BITRATE set "XBOX_BITRATE=20000"
if not defined XBOX_MODE set "XBOX_MODE=--home"
if not defined SMOOVPLAY_ARGS set "SMOOVPLAY_ARGS=--source tap --display --present dxgi --fullscreen"

set "XBOX_DIR=%~dp0xbox_tap"
set "XBOX_EXE=%XBOX_DIR%\smoovplay-xbox.exe"
set "TOKEN_FILE=%LOCALAPPDATA%\SmoovPlay\xbox_tokens.json"

:: 1. Locate Python (prefer repo venv)
set "PY_CMD="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD="%~dp0.venv\Scripts\python.exe""
) else if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_CMD="%~dp0venv\Scripts\python.exe""
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_CMD=python"
    ) else (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_CMD=py -3"
        )
    )
)
if not defined PY_CMD (
    echo [error] Python not found. Activate your venv or install Python 3.
    pause
    exit /b 1
)

:: 2. Build/update smoovplay-xbox.exe automatically (Go caches unchanged builds)
where go >nul 2>&1
if %errorlevel% equ 0 (
    echo [xbox] Building smoovplay-xbox.exe...
    pushd "%XBOX_DIR%"
    go build -o smoovplay-xbox.exe ./cmd/smoovplay-xbox
    set "BUILD_ERR=!errorlevel!"
    popd
    if !BUILD_ERR! neq 0 (
        echo [error] go build failed.
        pause
        exit /b !BUILD_ERR!
    )
) else if not exist "%XBOX_EXE%" (
    echo [error] "%XBOX_EXE%" not found and Go is not installed on PATH.
    pause
    exit /b 1
)

:: 3. First-time Microsoft Device Code sign-in if no cached tokens exist
if not exist "%TOKEN_FILE%" (
    echo [xbox] No saved Xbox login found. Starting one-time sign-in...
    "%XBOX_EXE%" login
    if !errorlevel! neq 0 (
        echo [error] Xbox sign-in failed.
        pause
        exit /b 1
    )
)

:: 4. Stop any leftover smoovplay-xbox instance before starting fresh
taskkill /F /IM smoovplay-xbox.exe >nul 2>&1

:: 5. Start native Xbox WebRTC stream in a minimized companion console
echo [xbox] Starting native Xbox stream (%XBOX_MODE%, %XBOX_RES%, %XBOX_BITRATE% kbps)...
start "SmoovPlay Xbox Stream" /min "%XBOX_EXE%" play %XBOX_MODE% --res %XBOX_RES% --bitrate %XBOX_BITRATE%

:: 6. Launch SmoovPlay in the foreground (waits for stream on Local\smoovplay-tap)
echo [smoovplay] Launching SmoovPlay (%SMOOVPLAY_ARGS% %*)...
%PY_CMD% "%~dp0run.py" %SMOOVPLAY_ARGS% %*
set "RUN_ERR=%errorlevel%"

:: 7. Cleanly stop the Xbox stream when SmoovPlay closes
echo [xbox] Stopping Xbox stream...
taskkill /IM smoovplay-xbox.exe /T >nul 2>&1
timeout /t 1 /nobreak >nul
taskkill /F /IM smoovplay-xbox.exe /T >nul 2>&1

if %RUN_ERR% neq 0 (
    echo [smoovplay] Exited with code %RUN_ERR%.
    pause
    exit /b %RUN_ERR%
)
exit /b 0
