@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set LOG=diagnose_log.txt
echo Silicon to Software (S2S) — Diagnostics > %LOG%
echo Run at: %DATE% %TIME% >> %LOG%
echo. >> %LOG%

echo.
echo  ============================================================
echo   Silicon to Software (S2S) — Diagnostics
echo   Results saved to: diagnose_log.txt
echo  ============================================================
echo.

REM ── 1. Python ────────────────────────────────────────────────────────────────
echo [1/6] Checking Python...
echo === PYTHON === >> %LOG%
python --version >> %LOG% 2>&1
py --version >> %LOG% 2>&1
py -3 --version >> %LOG% 2>&1

set PYTHON_CMD=
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYTHON_CMD (
        py -%%V --version >nul 2>&1
        if !errorlevel!==0 set PYTHON_CMD=py -%%V
    )
)
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel!==0 set PYTHON_CMD=python
)
if not defined PYTHON_CMD (
    echo  [FAIL] Python not found >> %LOG%
    echo  [FAIL] Python not found — install from https://python.org
    echo         CRITICAL: check "Add Python to PATH" during install
    goto :save_and_open
)
echo  [OK]  Python found: %PYTHON_CMD% >> %LOG%
echo  [OK]  Python: %PYTHON_CMD%

REM ── 2. Python version number ─────────────────────────────────────────────────
echo. >> %LOG%
echo === PYTHON VERSION === >> %LOG%
%PYTHON_CMD% --version >> %LOG% 2>&1

REM ── 3. Key package imports ────────────────────────────────────────────────────
echo.
echo [2/6] Testing key package imports...
echo. >> %LOG%
echo === PACKAGE IMPORTS === >> %LOG%

for %%P in (fastapi uvicorn sqlalchemy pydantic anthropic openai dotenv httpx rich tenacity networkx numpy) do (
    %PYTHON_CMD% -c "import %%P; print('  [OK]  %%P', getattr(%%P, '__version__', 'ok'))" >> %LOG% 2>&1
    if !errorlevel! neq 0 echo  [FAIL] %%P not installed >> %LOG%
)

REM ── 4. .env file ─────────────────────────────────────────────────────────────
echo.
echo [3/6] Checking .env file...
echo. >> %LOG%
echo === .ENV FILE === >> %LOG%
if exist .env (
    echo  [OK]  .env file found >> %LOG%
    REM Show keys that are SET (not their values)
    echo  Keys present in .env: >> %LOG%
    for /f "tokens=1 delims==" %%K in (.env) do (
        if not "%%K"=="" (
            echo    %%K >> %LOG%
        )
    )
    REM Check if GLM or ANTHROPIC key has a value
    %PYTHON_CMD% -c "
import os, pathlib
text = pathlib.Path('.env').read_text()
has_glm = any(line.startswith('GLM_API_KEY=') and len(line.split('=',1)[1].strip()) > 5 for line in text.splitlines())
has_ant = any(line.startswith('ANTHROPIC_API_KEY=') and len(line.split('=',1)[1].strip()) > 5 for line in text.splitlines())
has_ds  = any(line.startswith('DEEPSEEK_API_KEY=') and len(line.split('=',1)[1].strip()) > 5 for line in text.splitlines())
if has_glm or has_ant or has_ds:
    print('  [OK]  At least one LLM API key is filled in')
else:
    print('  [FAIL] No LLM API key found — open .env and fill in GLM_API_KEY')
" >> %LOG% 2>&1
) else (
    echo  [FAIL] .env file NOT found >> %LOG%
    echo  [FAIL] .env file missing — run INSTALL.bat to create it
)

REM ── 5. Port 8000 ─────────────────────────────────────────────────────────────
echo.
echo [4/6] Checking port 8000...
echo. >> %LOG%
echo === PORT 8000 === >> %LOG%
netstat -ano 2>nul | findstr ":8000" >> %LOG%
if !errorlevel! neq 0 echo  (nothing on port 8000 — server not running) >> %LOG%

%PYTHON_CMD% -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health',timeout=3); print('  [OK]  Server is UP at http://localhost:8000/health')" >> %LOG% 2>&1
if !errorlevel! neq 0 echo  [INFO] Server not responding on port 8000 >> %LOG%

REM ── 6. Quick server test ──────────────────────────────────────────────────────
echo.
echo [5/6] Testing uvicorn import...
echo. >> %LOG%
echo === UVICORN TEST === >> %LOG%
%PYTHON_CMD% -c "import uvicorn; print('  [OK]  uvicorn', uvicorn.__version__)" >> %LOG% 2>&1
if !errorlevel! neq 0 (
    echo  [FAIL] uvicorn not installed >> %LOG%
    echo  Run: pip install -r requirements.txt
)

REM ── 7. Try importing main.py ─────────────────────────────────────────────────
echo.
echo [6/6] Testing main.py import (catches missing packages)...
echo. >> %LOG%
echo === MAIN.PY IMPORT TEST === >> %LOG%
%PYTHON_CMD% -c "
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('main', 'main.py')
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
    print('  [OK]  main.py imports cleanly')
except Exception as e:
    print(f'  [FAIL] main.py import error: {e}')
" >> %LOG% 2>&1

REM ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo === DIRECTORY CONTENTS === >> %LOG%
dir /b >> %LOG% 2>&1

:save_and_open
echo. >> %LOG%
echo ============================================================ >> %LOG%
echo DIAGNOSIS COMPLETE — share diagnose_log.txt to get help >> %LOG%
echo ============================================================ >> %LOG%

echo.
echo  ============================================================
echo   Done! Opening diagnose_log.txt...
echo   Share this file if you need help.
echo  ============================================================
echo.

notepad diagnose_log.txt

pause
endlocal
