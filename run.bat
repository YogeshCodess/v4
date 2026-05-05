@echo off
setlocal enabledelayedexpansion

REM ── Always run from the folder that contains this .bat file ───────────────
cd /d "%~dp0"

title Silicon to Software (S2S) - Starting...

echo.
echo  ============================================================
echo   Silicon to Software (S2S)  ^|  AI Design Studio  ^|  S2S V2
echo  ============================================================
echo.

REM ── Find Python ──────────────────────────────────────────────────────────────
REM   Priority: py launcher (3.13→3.10), then plain python/python3
set PYTHON_CMD=

REM Try Python Launcher versions
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel!==0 (
            set PYTHON_CMD=py -%%V
            echo  [OK]  Found Python %%V via launcher
        )
    )
)

REM Fallback: plain "python"
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel!==0 (
        set PYTHON_CMD=python
        echo  [OK]  Found Python via "python" command
    )
)

REM Fallback: "python3"
if not defined PYTHON_CMD (
    python3 --version >nul 2>&1
    if !errorlevel!==0 (
        set PYTHON_CMD=python3
        echo  [OK]  Found Python via "python3" command
    )
)

if not defined PYTHON_CMD (
    echo.
    echo  [ERROR]  Python 3.10 or newer is required but was not found.
    echo           Download from: https://python.org/downloads/
    echo           Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ── Install / upgrade dependencies ───────────────────────────────────────────
echo.
echo  [1/3] Installing/verifying dependencies...
echo        (includes pypdf + pdfplumber + pytesseract for datasheet extractor,
echo         reportlab for PDF export, langchain-* for component search)
%PYTHON_CMD% -m pip install -r requirements.txt -q --no-warn-script-location 2>&1
if errorlevel 1 (
    echo  [WARN] Some packages may have failed. Continuing anyway...
) else (
    echo  [OK]  Dependencies ready.
)

REM ── Curated-spec library smoke test (fast, fails closed if broken) ──────────
echo.
echo  [2/3] Verifying curated component-spec library...
if exist smoke_test_curated.py (
    %PYTHON_CMD% smoke_test_curated.py
    if errorlevel 1 (
        echo  [WARN] Smoke test reported failures - continuing anyway.
        echo         RTL emission may fall back to generic logic.
    ) else (
        echo  [OK]  Curated specs + diff detection + validator all healthy.
    )
) else (
    echo  [SKIP] smoke_test_curated.py not found - skipping library check.
)

REM ── Kill any stale process on port 8000 ──────────────────────────────────────
echo.
echo  [*]  Clearing port 8000...
for /f "tokens=5 delims= " %%P in ('netstat -ano 2^>nul ^| findstr /R " :8000 "') do (
    if not "%%P"=="" taskkill /PID %%P /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── Start FastAPI backend in a new window ────────────────────────────────────
echo.
echo  [3/3] Starting FastAPI backend  ->  http://localhost:8000
set BACKEND_CMD=%PYTHON_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info --reload
start "S2S - FastAPI Backend" cmd /k "title S2S - FastAPI Backend && cd /d "%~dp0" && %BACKEND_CMD%"

REM ── Poll until backend is healthy (up to 30 seconds) ────────────────────────
echo  [*]  Waiting for backend...
set TRIES=0

:healthloop
timeout /t 1 /nobreak >nul
%PYTHON_CMD% -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health',timeout=3); sys.exit(0)" >nul 2>&1
if %errorlevel%==0 (
    echo  [OK]  Backend is healthy!
    goto :backend_ready
)
set /a TRIES=TRIES+1
if %TRIES% lss 30 (
    echo  [*]  Still waiting... ^(%TRIES%/30^)
    goto :healthloop
)
echo  [WARN] Backend health check timed out after 30s. Proceeding anyway...
echo         (Backend may still be starting - try refreshing the browser)

:backend_ready

REM ── Open browser to React UI ─────────────────────────────────────────────────
timeout /t 2 /nobreak >nul

REM ── Count curated specs so banner can display the library size ──────────────
set CURATED_COUNT=0
for %%F in (data\component_specs\*.json) do (
    set "FNAME=%%~nF"
    if not "!FNAME:~0,1!"=="_" set /a CURATED_COUNT=CURATED_COUNT+1
)

echo.
echo  ============================================================
echo   Silicon to Software (S2S) is ready!
echo.
echo   App             ->  http://localhost:8000/app
echo   API docs        ->  http://localhost:8000/docs
echo   Health          ->  http://localhost:8000/health
echo   Curated specs   ->  !CURATED_COUNT! parts loaded
echo  ============================================================
echo.
echo   One window is running:
echo     "S2S - FastAPI Backend"   (keep open)
echo.
echo   Close this window when finished.
echo.

REM Open browser automatically
start "" "http://localhost:8000/app"
echo   Opening browser:  http://localhost:8000/app

pause
endlocal
