#!/bin/bash

echo "=================================="
echo "Invoice Reconciliation Tool"
echo "=================================="
echo ""

cd "$(dirname "$0")"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Please install Python from https://www.python.org/downloads/"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Setting up for first time use..."
    echo "This may take a minute..."
    echo ""
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies if needed
if [ ! -f "venv/.installed" ]; then
    echo "Installing required packages..."
    pip install -q -r requirements.txt
    touch venv/.installed
    echo "Setup complete!"
    echo ""
fi

echo "Starting web server..."
echo ""
echo "=================================="
echo "Open your browser and go to:"
echo ""
echo "    http://localhost:5000"
echo ""
echo "=================================="
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

python -m invoice_reconciliation.web
