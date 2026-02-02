@echo off
echo ==================================
echo Invoice Reconciliation Tool
echo ==================================
echo.

cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Setting up for first time use...
    echo This may take a minute...
    echo.
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install dependencies if needed
if not exist "venv\.installed" (
    echo Installing required packages...
    pip install -q -r requirements.txt
    echo. > venv\.installed
    echo Setup complete!
    echo.
)

echo Starting web server...
echo.
echo ==================================
echo Open your browser and go to:
echo.
echo     http://localhost:5000
echo.
echo ==================================
echo.
echo Press Ctrl+C to stop the server
echo.

python -m invoice_reconciliation.web
