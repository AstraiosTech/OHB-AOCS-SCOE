#!/bin/bash
# ============================================
# Aurora SOCC - Setup Script
# ============================================
# 
# This script sets up the SOCC environment on a new machine.
# Run with: chmod +x setup.sh && ./setup.sh
#
# ============================================

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           Aurora SOCC - Installation Script                   ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check Python version
echo "Checking Python installation..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo "  ✓ Found: $PYTHON_VERSION"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$(python --version 2>&1)
    echo "  ✓ Found: $PYTHON_VERSION"
else
    echo "  ✗ Python not found! Please install Python 3.10 or higher."
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ ! -d "venv" ]; then
    $PYTHON_CMD -m venv venv
    echo "  ✓ Virtual environment created"
else
    echo "  ✓ Virtual environment already exists"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate
echo "  ✓ Activated"

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip -q
echo "  ✓ pip upgraded"

# Install requirements
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt -q
echo "  ✓ Dependencies installed"

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p data_collection/output
mkdir -p logs
echo "  ✓ Directories created"

# Done!
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    Installation Complete!                      ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "To start the SOCC:"
echo ""
echo "  1. Activate the virtual environment:"
echo "     source venv/bin/activate"
echo ""
echo "  2. Run the application:"
echo "     python socc_app.py"
echo ""
echo "  3. Open in browser:"
echo "     http://localhost:5050"
echo ""

