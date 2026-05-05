@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"
title Silicon to Software (S2S) -- One-Click Install

echo.
echo  ============================================================
echo   Silicon to Software (S2S)  ^|  One-Click Install ^& Launch
echo  ============================================================
echo.

REM ── 1. Check Python ──────────────────────────────────────────────────────────
set PYTHON_CMD=
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel!==0 (
            set PYTHON_CMD=py -%%V
            echo  [OK]  Python %%V found
        )
    )
)
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel!==0 ( set PYTHON_CMD=python && echo  [OK]  Python found )
)
if not defined PYTHON_CMD (
    echo.
    echo  [ERROR] Python 3.10+ not found.
    echo          Download: https://python.org/downloads/
    echo          Check "Add Python to PATH" during install.
    echo.
    pause & exit /b 1
)

REM ── 2. Check / create .env ───────────────────────────────────────────────────
if not exist .env (
    echo.
    echo  [WARN]  .env file not found.
    echo          Creating template -- EDIT it and add your API keys before running.
    echo.
    (
        echo # ── API Keys (set at least one LLM key) ────────────────────────
        echo GLM_API_KEY=
        echo DEEPSEEK_API_KEY=
        echo ANTHROPIC_API_KEY=
        echo.
        echo # ── GLM / Z.AI endpoint ─────────────────────────────────────────
        echo GLM_BASE_URL=https://api.z.ai/api/anthropic
        echo GLM_MODEL=glm-4.7
        echo GLM_FAST_MODEL=glm-4.5-air
        echo.
        echo # ── Model selection ─────────────────────────────────────────────
        echo PRIMARY_MODEL=glm-4.7
        echo FAST_MODEL=glm-4.5-air
        echo.
        echo # ── Database (leave as-is for SQLite) ───────────────────────────
        echo DATABASE_URL=sqlite:///hardware_pipeline.db
    ) > .env
    echo  [OK]  .env created. Open it and fill in your API keys, then re-run.
    notepad .env
    echo.
    echo  Press any key after saving .env to continue...
    pause >nul
)

REM ── 3. Install Python dependencies ───────────────────────────────────────────
echo.
echo  [1/4] Installing Python dependencies (first run takes a few minutes)...
echo        Includes: FastAPI, pydantic, anthropic, chromadb,
echo                  pypdf + pdfplumber + pytesseract (datasheet extractor),
echo                  python-docx + reportlab (DOCX/PDF export),
echo                  langchain-* (component search), opentelemetry (tracing).
%PYTHON_CMD% -m pip install -r requirements.txt -q --no-warn-script-location
if errorlevel 1 (
    echo  [WARN] Some packages may have warned. Continuing...
) else (
    echo  [OK]  All dependencies installed.
)

REM ── 3a. Pandoc + Tesseract presence check (warn but don't block) ───────────
echo.
echo  [*]  Checking optional native binaries...
where pandoc >nul 2>&1
if errorlevel 1 (
    echo  [WARN] pandoc not found on PATH.
    echo         DOCX/PDF export will use python-docx + reportlab fallback (works,
    echo         but plainer formatting). To install: https://pandoc.org/installing.html
) else (
    echo  [OK]  pandoc found - high-quality DOCX/PDF export available.
)
where tesseract >nul 2>&1
if errorlevel 1 (
    echo  [WARN] tesseract not found on PATH.
    echo         OCR fallback for scanned datasheets will be skipped (only matters
    echo         for image-only PDFs). To install Tesseract on Windows:
    echo         https://github.com/UB-Mannheim/tesseract/wiki
) else (
    echo  [OK]  tesseract found - OCR available for scanned datasheets.
)

REM ── 3b. Curated-spec library smoke test ──────────────────────────────────────
echo.
echo  [2/4] Verifying curated component-spec library + diff detection...
if exist smoke_test_curated.py (
    %PYTHON_CMD% smoke_test_curated.py
    if errorlevel 1 (
        echo  [WARN] Smoke test reported failures. Pipeline will still start
        echo         but RTL emission may not use curated specs. See output above.
    ) else (
        echo  [OK]  Curated spec library is healthy.
    )
) else (
    echo  [SKIP] smoke_test_curated.py not found - skipping library check.
)

REM ── 4. Kill stale process on port 8000 ───────────────────────────────────────
echo.
echo  [3/4] Clearing port 8000...
for /f "tokens=5 delims= " %%P in ('netstat -ano 2^>nul ^| findstr /R " :8000 "') do (
    if not "%%P"=="" taskkill /PID %%P /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── 5. Start FastAPI backend ──────────────────────────────────────────────────
echo.
echo  [4/4] Starting Silicon to Software (S2S) backend...
start "S2S -- FastAPI Backend" cmd /k "title S2S — FastAPI Backend && cd /d "%~dp0" && %PYTHON_CMD% -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info"

REM ── 6. Wait for health ───────────────────────────────────────────────────────
echo  [*]  Waiting for server to start...
set TRIES=0
:healthloop
timeout /t 2 /nobreak >nul
%PYTHON_CMD% -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health',timeout=3); sys.exit(0)" >nul 2>&1
if %errorlevel%==0 goto :ready
set /a TRIES=TRIES+1
if %TRIES% lss 20 ( echo  [*]  Still starting... (%TRIES%/20) & goto :healthloop )
echo  [WARN] Timeout — opening browser anyway...

:ready
echo  [OK]  Server is ready!
REM ── 7. Open browser ──────────────────────────────────────────────────────────
timeout /t 1 /nobreak >nul
start "" "http://localhost:8000/app"

REM ── 8. Print curated-spec count so user knows the library is loaded ─────────
set CURATED_COUNT=0
for %%F in (data\component_specs\*.json) do (
    set "FNAME=%%~nF"
    if not "!FNAME:~0,1!"=="_" set /a CURATED_COUNT=CURATED_COUNT+1
)

echo.
echo  ============================================================
echo   Silicon to Software (S2S) is RUNNING
echo.
echo   App             ->  http://localhost:8000/app
echo   API docs        ->  http://localhost:8000/docs
echo   Curated specs   ->  !CURATED_COUNT! parts loaded
echo  ============================================================
echo.
echo   Keep the "S2S -- FastAPI Backend" window open.
echo   Close this window when finished.
echo.
pause
endlocal
