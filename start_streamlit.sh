#!/bin/bash

# Invoice Reconciliation & Estimation System - Streamlit Launcher
# This script starts the Streamlit web dashboard

echo "================================================"
echo "Invoice Reconciliation & Estimation System"
echo "================================================"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed or not in PATH"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "Checking dependencies..."
pip install -q -r requirements.txt

# Install the package in development mode
pip install -q -e .

echo ""
echo "Starting Streamlit dashboard..."
echo "Open http://localhost:8501 in your browser"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Start Streamlit
streamlit run invoice_reconciliation/streamlit_app.py --server.port 8501
