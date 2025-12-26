#!/usr/bin/env python3
"""
Mock AOCS Server Entry Point

This script starts the mock AOCS server that simulates the OHB AOCS system.
It receives telecommands via EDEN/PUS over TCP/IP and generates telemetry responses.

Usage:
    python run_mock_aocs.py [--host HOST] [--port PORT]

Default: Listens on 0.0.0.0:10025
"""

import argparse
import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from mock_aocs_server import MockAOCSServer


def main():
    parser = argparse.ArgumentParser(description='Mock AOCS Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=10025, help='Port to listen on')
    args = parser.parse_args()
    
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                    MOCK AOCS SERVER                           ║
║                                                               ║
║  Simulates OHB AOCS for SCOE testing                          ║
║                                                               ║
║  Protocol: EDEN/PUS over TCP/IP                               ║
║  Listening on: {args.host}:{args.port:<5}                              ║
║                                                               ║
║  Supported PUS Services:                                      ║
║    - Service 1:  Request Verification                         ║
║    - Service 3:  Housekeeping                                 ║
║    - Service 8:  Function Management                          ║
║    - Service 17: Connection Test                              ║
║    - Service 20: Parameter Management                         ║
║                                                               ║
║  Press Ctrl+C to stop                                         ║
╚═══════════════════════════════════════════════════════════════╝
""")
    
    server = MockAOCSServer(host=args.host, port=args.port)
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nShutting down...")
        asyncio.run(server.stop())


if __name__ == '__main__':
    main()


