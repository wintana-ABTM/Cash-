@echo off
REM Invoice Reconciliation & Estimation System - Streamlit Launcher for Windows

echo ================================================
echo Invoice Reconciliation ^& Estimation System
echo ================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Get the directory where the script is located
cd /d "%~dp0"

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install/upgrade dependencies
echo Installing dependencies...
pip install -q -r requirements.txt

REM Install the package in development mode
pip install -q -e .

echo.
echo Starting Streamlit dashboard...
echo Open http://localhost:8501 in your browser
echo.
echo Press Ctrl+C to stop the server
echo.

REM Start Streamlit
streamlit run invoice_reconciliation/streamlit_app.py --server.port 8501

pause
