#!/usr/bin/env python3
"""
SCOE Controller Entry Point

This script starts the SCOE Controller that interfaces between Grafana 
and the AOCS via EDEN/PUS over TCP/IP.

Usage:
    python run_scoe_controller.py [options]

Options:
    --aocs-host     AOCS server host (default: localhost)
    --aocs-port     AOCS server port (default: 10025)
    --api-host      HTTP API host (default: 0.0.0.0)
    --api-port      HTTP API port (default: 8080)
    --influx-url    InfluxDB URL (default: http://localhost:8086)
"""

import argparse
import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from scoe_controller import SCOEController, SCOEConfig


def main():
    parser = argparse.ArgumentParser(description='SCOE Controller')
    parser.add_argument('--aocs-host', default='localhost', help='AOCS server host')
    parser.add_argument('--aocs-port', type=int, default=10025, help='AOCS server port')
    parser.add_argument('--api-host', default='0.0.0.0', help='HTTP API host')
    parser.add_argument('--api-port', type=int, default=8080, help='HTTP API port')
    parser.add_argument('--influx-url', default='http://localhost:8086', help='InfluxDB URL')
    parser.add_argument('--influx-token', default='my-super-secret-token', help='InfluxDB token')
    args = parser.parse_args()
    
    config = SCOEConfig(
        aocs_host=args.aocs_host,
        aocs_port=args.aocs_port,
        api_host=args.api_host,
        api_port=args.api_port,
        influxdb_url=args.influx_url,
        influxdb_token=args.influx_token,
    )
    
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                    SCOE CONTROLLER                            ║
║                                                               ║
║  Interfaces Grafana with AOCS via EDEN/PUS                    ║
║                                                               ║
║  AOCS Connection: {args.aocs_host}:{args.aocs_port:<5}                          ║
║  HTTP API:        http://{args.api_host}:{args.api_port:<5}                     ║
║  InfluxDB:        {args.influx_url:<25}            ║
║                                                               ║
║  API Endpoints:                                               ║
║    GET  /api/status          - Get connection status          ║
║    GET  /api/telemetry       - Get current telemetry          ║
║    POST /api/simulation/start - Start simulation              ║
║    POST /api/simulation/stop  - Stop simulation               ║
║    POST /api/simulation/reset - Reset simulation              ║
║    POST /api/rw/{{id}}/torque  - Set RW torque                  ║
║    POST /api/thruster/{{id}}/fire - Control thruster            ║
║    POST /api/sada/{{id}}/angle - Set SADA angle                 ║
║    GET  /api/ws              - WebSocket for real-time data   ║
║                                                               ║
║  Press Ctrl+C to stop                                         ║
╚═══════════════════════════════════════════════════════════════╝
""")
    
    controller = SCOEController(config)
    
    try:
        asyncio.run(controller.start())
    except KeyboardInterrupt:
        print("\nShutting down...")
        asyncio.run(controller.stop())


if __name__ == '__main__':
    main()


