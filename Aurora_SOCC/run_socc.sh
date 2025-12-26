#!/bin/bash
# ============================================
# Aurora SOCC - Quick Run Script
# ============================================

cd "$(dirname "$0")"

# Check if virtual environment exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo ""
echo "🚀 Starting Aurora SOCC..."
echo "   Open http://localhost:5050 in your browser"
echo "   Press Ctrl+C to stop"
echo ""

python3 socc_app.py

