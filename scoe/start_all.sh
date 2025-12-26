#!/bin/bash
#
# AOCS SCOE - Start All Services
#
# This script starts all components of the AOCS SCOE system:
# 1. Docker containers (InfluxDB + Grafana)
# 2. Mock AOCS Server
# 3. SCOE Controller
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║              AOCS SCOE - Starting All Services                ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Start Docker containers
echo "📦 Starting Docker containers (InfluxDB + Grafana)..."
docker-compose up -d

echo "⏳ Waiting for services to be ready..."
sleep 5

# Check if Python dependencies are installed
echo "🐍 Checking Python dependencies..."
pip install -q -r requirements.txt

# Start Mock AOCS Server in background
echo "🛰️ Starting Mock AOCS Server..."
python run_mock_aocs.py &
MOCK_AOCS_PID=$!
sleep 2

# Start SCOE Controller
echo "🎮 Starting SCOE Controller..."
python run_scoe_controller.py &
SCOE_PID=$!

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    All Services Started!                      ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
echo "║  🌐 Grafana:     http://localhost:3000                        ║"
echo "║                  (admin/admin)                                ║"
echo "║                                                               ║"
echo "║  📊 InfluxDB:    http://localhost:8086                        ║"
echo "║                                                               ║"
echo "║  🔌 SCOE API:    http://localhost:8080                        ║"
echo "║                                                               ║"
echo "║  🛰️ Mock AOCS:   TCP port 10025                               ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Press Ctrl+C to stop all services..."

# Handle shutdown
cleanup() {
    echo ""
    echo "🛑 Shutting down services..."
    kill $MOCK_AOCS_PID 2>/dev/null || true
    kill $SCOE_PID 2>/dev/null || true
    docker-compose down
    echo "✅ All services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for any process to exit
wait


