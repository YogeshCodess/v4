@echo off
REM Silicon to Software (S2S) - Deployment Script for Windows
REM Usage: deploy.bat [dev|prod] [port]

setlocal enabledelayedexpansion

set ENV=%1
if "%ENV%"=="" set ENV=dev

set PORT=%2
if "%PORT%"=="" set PORT=8501

echo ==========================================
echo Silicon to Software (S2S) Deployment Script
echo ==========================================
echo Environment: %ENV%
echo Port: %PORT%
echo.

REM Check Python
echo Checking Python version...
python --version >nul 2>&1
if errorlevel 1 (
    echo WARNING: Some tests failed, but continuing...
    echo ERROR: Python not found
    exit /b 1
)

REM Install dependencies
echo.
echo Installing dependencies...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo WARNING: Some tests failed, but continuing...
    echo ERROR: Failed to install dependencies
    exit /b 1
)

REM Run tests
echo.
echo Running tests...
python -m pytest tests/ -q --tb=no --ignore=tests/test_ui_playwright.py

REM Check if .env exists
if not exist .env (
    echo.
    echo WARNING: .env file not found
    echo Creating from template...
    (
        echo # Silicon to Software (S2S) Environment Variables
        echo.
        echo # API Keys - set at least one
        echo ANTHROPIC_API_KEY=
        echo.
        echo # Optional API Keys
        echo # OPENAI_API_KEY=
        echo # GLM_API_KEY=
        echo # OLLAMA_BASE_URL=http://localhost:11434
        echo.
        echo # Database
        echo DATABASE_URL=sqlite:///hardware_pipeline.db
        echo.
        echo # Mode
        echo MODE=online
    ) > .env
    echo Created .env file
    echo WARNING: Please edit .env and add your API keys
)

REM Create directories
echo.
echo Creating directories...
if not exist outputs mkdir outputs
if not exist outputs\projects mkdir outputs\projects
if not exist outputs\documents mkdir outputs\documents
if not exist logs mkdir logs
echo Directories created

REM Start Streamlit
echo.
echo ==========================================
echo Starting Silicon to Software (S2S)...
echo ==========================================
echo UI will be available at: http://localhost:%PORT%
echo.

if "%ENV%"=="prod" (
    streamlit run app.py --server.port %PORT% --server.address 0.0.0.0 --server.headless true
) else (
    streamlit run app.py --server.port %PORT%
)
