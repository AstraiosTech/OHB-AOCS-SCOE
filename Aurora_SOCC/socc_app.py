#!/usr/bin/env python3
"""
Aurora SOCC - Satellite Operations Control Center
Main Flask Application

Provides:
- Web-based GUI for scenario selection and SOCC operations
- REST API for command/telemetry interface
- WebSocket for real-time telemetry streaming
- Integration with AOCS SCOE and EGSE interfaces
"""

import os
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify, request, send_from_directory

# Import SOCC modules
from aocs_scoe_interface import AOCSSCOEInterface, create_scoe_interface
from egse_interface import EGSEInterface, create_egse_interface
from test_procedures.procedures import (
    TestProcedureRunner, get_procedure, list_procedures, 
    StepStatus, ProcedureStatus
)
from data_collection.data_collector import (
    DataCollector, TelemetryFrame, CommandRecord, get_collector
)
from ccsds_receiver import CCSDSUDPReceiver, get_ccsds_receiver, create_ccsds_receiver

# Initialize Flask app
app = Flask(__name__, 
            static_folder='static',
            template_folder='templates')

# Global instances
scoe: AOCSSCOEInterface = None
egse: EGSEInterface = None
procedure_runner: TestProcedureRunner = None
data_collector: DataCollector = None
ccsds_receiver: CCSDSUDPReceiver = None

# Telemetry source: 'simulation' or 'ccsds'
current_telemetry_source = 'simulation'

# Session state
session_state = {
    "active_scenario": None,
    "simulation_running": False,
    "mode": "STANDBY",
    "mission_time": 0.0,
    "start_time": None
}

# Telemetry simulation thread
telemetry_thread = None
telemetry_running = False


def init_systems():
    """Initialize all SOCC subsystems."""
    global scoe, egse, procedure_runner, data_collector, ccsds_receiver
    
    print("Initializing SOCC subsystems...")
    
    # Initialize SCOE interface
    scoe = create_scoe_interface()
    
    # Initialize EGSE interface
    egse = create_egse_interface()
    
    # Initialize test procedure runner
    procedure_runner = TestProcedureRunner()
    
    # Initialize data collector
    data_collector = DataCollector(
        output_dir=str(Path(__file__).parent / "data_collection" / "output")
    )
    
    # Initialize CCSDS UDP receiver
    ccsds_receiver = create_ccsds_receiver(host="0.0.0.0", port=5003)
    
    print("All subsystems initialized.")


# ============================================
# Web Routes
# ============================================

@app.route('/')
def splash():
    """Render scenario selection splash page."""
    return render_template('splash.html')


@app.route('/console')
def console():
    """Render SOCC operations console."""
    return render_template('console.html')


@app.route('/constellation')
def constellation():
    """Render constellation view page."""
    return render_template('constellation.html')


@app.route('/static/<path:filename>')
def static_files(filename):
    """Serve static files."""
    return send_from_directory('static', filename)


# ============================================
# API Routes - Scenarios
# ============================================

@app.route('/api/scenarios', methods=['GET'])
def get_scenarios():
    """Get list of available scenarios."""
    if scoe:
        scenarios = scoe.get_available_scenarios()
        return jsonify(scenarios)
    return jsonify([])


@app.route('/api/scenarios/<scenario_id>', methods=['GET'])
def get_scenario_details(scenario_id):
    """Get details of a specific scenario."""
    if scoe:
        scenarios = scoe.get_available_scenarios()
        for s in scenarios:
            if s['id'] == scenario_id:
                # Load full scenario data
                with open(s['path'], 'r') as f:
                    return jsonify(json.load(f))
    return jsonify({"error": "Scenario not found"}), 404


@app.route('/api/inject', methods=['POST'])
def inject_scenario():
    """Inject a scenario into SCOE."""
    data = request.json
    scenario_id = data.get('scenario_id')
    
    if not scenario_id:
        return jsonify({"error": "No scenario_id provided"}), 400
    
    if scoe:
        scenarios = scoe.get_available_scenarios()
        for s in scenarios:
            if s['id'] == scenario_id:
                if scoe.load_scenario(s['path']):
                    if scoe.inject_scenario():
                        session_state['active_scenario'] = s
                        
                        # Start data collection session
                        if data_collector:
                            data_collector.start_session(s['name'])
                        
                        return jsonify({
                            "success": True,
                            "message": f"Scenario '{s['name']}' injected"
                        })
    
    return jsonify({"error": "Failed to inject scenario"}), 500


@app.route('/api/scoe/start', methods=['POST'])
def start_scoe():
    """Start SCOE simulation."""
    global telemetry_running, telemetry_thread
    
    if scoe:
        if scoe.start_simulation():
            session_state['simulation_running'] = True
            session_state['start_time'] = time.time()
            
            # Start telemetry simulation
            telemetry_running = True
            telemetry_thread = threading.Thread(target=telemetry_simulation_loop, daemon=True)
            telemetry_thread.start()
            
            # Start data recording
            if data_collector:
                data_collector.start_recording()
            
            return jsonify({"success": True, "message": "Simulation started"})
    
    return jsonify({"error": "Failed to start simulation"}), 500


@app.route('/api/scoe/stop', methods=['POST'])
def stop_scoe():
    """Stop SCOE simulation."""
    global telemetry_running
    
    telemetry_running = False
    
    if scoe:
        scoe.stop_simulation()
        session_state['simulation_running'] = False
        
        # Stop data collection
        if data_collector:
            data_collector.stop_session()
        
        return jsonify({"success": True, "message": "Simulation stopped"})
    
    return jsonify({"error": "Failed to stop simulation"}), 500


@app.route('/api/scoe/status', methods=['GET'])
def get_scoe_status():
    """Get SCOE status."""
    if scoe:
        return jsonify(scoe.get_current_state())
    return jsonify({"state": "DISCONNECTED"})


# ============================================
# API Routes - Commands
# ============================================

@app.route('/api/command', methods=['POST'])
def send_command():
    """Send command to satellite."""
    data = request.json
    command = data.get('command')
    parameters = data.get('parameters', {})
    
    if not command:
        return jsonify({"error": "No command provided"}), 400
    
    # Log command
    timestamp = time.time()
    cmd_record = CommandRecord(
        timestamp=timestamp,
        command_id=f"CMD_{int(timestamp)}",
        command_type=command,
        parameters=parameters,
        source="SOCC"
    )
    
    if data_collector:
        data_collector.record_command(cmd_record)
        data_collector.log_event("COMMAND", "INFO", "SOCC", 
                                 f"Command sent: {command}")
    
    # Process command (simulate effects)
    result = process_command(command, parameters)
    
    return jsonify({
        "success": True,
        "command_id": cmd_record.command_id,
        "result": result
    })


def process_command(command: str, parameters: dict) -> dict:
    """Process a command and return result."""
    result = {"acknowledged": True, "executed": True}
    
    # Mode commands
    if command == "SET_NOMINAL":
        session_state['mode'] = "NOMINAL"
        result['new_mode'] = "NOMINAL"
    elif command == "SET_SAFE":
        session_state['mode'] = "SAFE"
        result['new_mode'] = "SAFE"
    elif command == "SET_DETUMBLE":
        session_state['mode'] = "DETUMBLE"
        result['new_mode'] = "DETUMBLE"
    elif command == "SET_SUN_POINT":
        session_state['mode'] = "SUN_POINTING"
        result['new_mode'] = "SUN_POINTING"
    elif command == "SET_NADIR":
        session_state['mode'] = "NADIR_POINTING"
        result['new_mode'] = "NADIR_POINTING"
    elif command == "EMERGENCY_STOP":
        session_state['mode'] = "E_STOP"
        session_state['simulation_running'] = False
        result['new_mode'] = "E_STOP"
        
        if data_collector:
            data_collector.log_event("ESTOP", "CRITICAL", "SOCC", 
                                    "Emergency stop activated")
    
    return result


# ============================================
# API Routes - Telemetry
# ============================================

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    """Get current telemetry snapshot."""
    global ccsds_receiver, current_telemetry_source
    
    # Calculate mission time
    mission_time = 0.0
    if session_state['start_time']:
        mission_time = time.time() - session_state['start_time']
    
    # Generate simulated telemetry
    import math
    t = mission_time
    
    telemetry = {
        "timestamp": time.time(),
        "mission_time": mission_time,
        "mode": session_state['mode'],
        "telemetry_source": current_telemetry_source,
        "attitude": {
            "roll": 0.1 * math.sin(t * 0.1),
            "pitch": 0.05 * math.cos(t * 0.1),
            "yaw": 0.02 * math.sin(t * 0.05),
            "q0": 1.0,
            "q1": 0.0,
            "q2": 0.0,
            "q3": 0.0
        },
        "rates": {
            "roll": 0.001 * math.cos(t * 0.2),
            "pitch": 0.0005 * math.sin(t * 0.2),
            "yaw": 0.0002 * math.cos(t * 0.15)
        },
        "reaction_wheels": {
            "rw1": int(100 + 50 * math.sin(t * 0.1)),
            "rw2": int(200 - 30 * math.cos(t * 0.1)),
            "rw3": int(150 + 20 * math.sin(t * 0.15)),
            "rw4": int(1000 + 100 * math.cos(t * 0.05))
        },
        "sensors": {
            "magnetometer": "NOMINAL",
            "rate_sensor": "NOMINAL",
            "sun_sensors": "4/6 ILLUMINATED"
        },
        "power": {
            "bus_voltage": 28.0 + 0.2 * math.sin(t * 0.05),
            "current": 1.2 + 0.1 * math.cos(t * 0.1),
            "battery_soc": 92 - 0.01 * t % 10,
            "eclipse": False
        },
        "orbit": {
            "altitude_km": 400 + 0.5 * math.sin(t * 0.01),
            "latitude_deg": 30 * math.sin(t * 0.02),
            "longitude_deg": (t * 0.5) % 360 - 180
        }
    }
    
    # Add CCSDS status
    if ccsds_receiver:
        telemetry["ccsds_active"] = ccsds_receiver._running
        telemetry["ccsds_rate"] = ccsds_receiver.packets_per_second
    
    return jsonify(telemetry)


def telemetry_simulation_loop():
    """Background thread for telemetry simulation."""
    global telemetry_running
    frame_id = 0
    
    while telemetry_running:
        try:
            mission_time = 0.0
            if session_state['start_time']:
                mission_time = time.time() - session_state['start_time']
            
            import math
            t = mission_time
            
            # Create telemetry frame
            frame = TelemetryFrame(
                timestamp=time.time(),
                frame_id=frame_id,
                mission_time=mission_time,
                attitude={
                    "roll": 0.1 * math.sin(t * 0.1),
                    "pitch": 0.05 * math.cos(t * 0.1),
                    "yaw": 0.02 * math.sin(t * 0.05),
                    "q0": 1.0, "q1": 0.0, "q2": 0.0, "q3": 0.0
                },
                rates={
                    "roll": 0.001 * math.cos(t * 0.2),
                    "pitch": 0.0005 * math.sin(t * 0.2),
                    "yaw": 0.0002 * math.cos(t * 0.15)
                },
                wheel_speeds=[
                    100 + 50 * math.sin(t * 0.1),
                    200 - 30 * math.cos(t * 0.1),
                    150 + 20 * math.sin(t * 0.15),
                    1000 + 100 * math.cos(t * 0.05)
                ],
                magnetometer=[25000.0, 5000.0, -40000.0],
                power={
                    "voltage": 28.0 + 0.2 * math.sin(t * 0.05),
                    "current": 1.2 + 0.1 * math.cos(t * 0.1),
                    "battery_soc": 92
                },
                mode=session_state['mode']
            )
            
            # Record telemetry
            if data_collector:
                data_collector.record_telemetry(frame)
            
            frame_id += 1
            time.sleep(1.0)  # 1 Hz telemetry
            
        except Exception as e:
            print(f"Telemetry loop error: {e}")
            time.sleep(1.0)


# ============================================
# API Routes - Test Procedures
# ============================================

@app.route('/api/procedures', methods=['GET'])
def get_procedures():
    """Get list of available test procedures."""
    return jsonify(list_procedures())


@app.route('/api/procedures/<procedure_id>', methods=['GET'])
def get_procedure_details(procedure_id):
    """Get details of a specific procedure."""
    try:
        proc = get_procedure(procedure_id)
        return jsonify(proc.to_dict())
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route('/api/procedures/<procedure_id>/start', methods=['POST'])
def start_procedure(procedure_id):
    """Start a test procedure."""
    try:
        proc = get_procedure(procedure_id)
        procedure_runner.load_procedure(proc)
        
        data = request.json or {}
        tester = data.get('tester', '')
        
        procedure_runner.start(tester)
        
        if data_collector:
            data_collector.log_event("PROCEDURE", "INFO", "SOCC",
                                    f"Started procedure: {proc.name}")
        
        return jsonify({
            "success": True,
            "procedure": proc.to_dict()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/procedures/step/<int:step_number>/complete', methods=['POST'])
def complete_step(step_number):
    """Complete a procedure step."""
    data = request.json or {}
    passed = data.get('passed', True)
    actual_result = data.get('actual_result', '')
    notes = data.get('notes', '')
    
    procedure_runner.complete_step(step_number, passed, actual_result, notes)
    
    if procedure_runner.active_procedure:
        return jsonify({
            "success": True,
            "procedure": procedure_runner.active_procedure.to_dict()
        })
    
    return jsonify({"error": "No active procedure"}), 400


@app.route('/api/procedures/current', methods=['GET'])
def get_current_procedure():
    """Get current procedure state."""
    if procedure_runner.active_procedure:
        return jsonify(procedure_runner.active_procedure.to_dict())
    return jsonify({"status": "No active procedure"})


# ============================================
# API Routes - Data Collection
# ============================================

@app.route('/api/data/status', methods=['GET'])
def get_data_status():
    """Get data collection status."""
    if data_collector:
        return jsonify(data_collector.get_statistics())
    return jsonify({"error": "Data collector not initialized"})


@app.route('/api/data/export', methods=['POST'])
def export_data():
    """Export collected data."""
    if data_collector:
        data_collector.export_all()
        return jsonify({
            "success": True,
            "message": "Data exported",
            "path": str(data_collector.session_dir) if data_collector.session_id else None
        })
    return jsonify({"error": "Data collector not initialized"}), 500


# ============================================
# API Routes - EGSE
# ============================================

@app.route('/api/egse/status', methods=['GET'])
def get_egse_status():
    """Get EGSE status."""
    if egse:
        return jsonify(egse.get_status())
    return jsonify({"state": "OFFLINE"})


# ============================================
# API Routes - CCSDS Telemetry
# ============================================

@app.route('/api/ccsds/status', methods=['GET'])
def get_ccsds_status():
    """Get CCSDS receiver status."""
    global ccsds_receiver, current_telemetry_source
    
    if ccsds_receiver:
        status = ccsds_receiver.get_status()
        status['telemetry_source'] = current_telemetry_source
        return jsonify(status)
    
    return jsonify({
        "running": False,
        "telemetry_source": current_telemetry_source,
        "error": "CCSDS receiver not initialized"
    })


@app.route('/api/ccsds/start', methods=['POST'])
def start_ccsds():
    """Start the CCSDS UDP receiver."""
    global ccsds_receiver
    
    if not ccsds_receiver:
        ccsds_receiver = create_ccsds_receiver(host="0.0.0.0", port=5003)
    
    if ccsds_receiver.start():
        return jsonify({
            "success": True,
            "message": "CCSDS receiver started on port 5003"
        })
    else:
        return jsonify({
            "success": False,
            "error": "Failed to start CCSDS receiver"
        }), 500


@app.route('/api/ccsds/stop', methods=['POST'])
def stop_ccsds():
    """Stop the CCSDS UDP receiver."""
    global ccsds_receiver
    
    if ccsds_receiver:
        ccsds_receiver.stop()
        return jsonify({
            "success": True,
            "message": "CCSDS receiver stopped"
        })
    
    return jsonify({
        "success": False,
        "error": "CCSDS receiver not running"
    })


@app.route('/api/ccsds/packets', methods=['GET'])
def get_ccsds_packets():
    """Get recent CCSDS packets."""
    global ccsds_receiver
    
    count = request.args.get('count', 10, type=int)
    
    if ccsds_receiver:
        packets = ccsds_receiver.get_recent_packets(count)
        return jsonify({
            "packets": [
                {
                    "timestamp": p.timestamp,
                    "packet_id": p.packet_id,
                    "apid": p.apid,
                    "service_type": p.service_type,
                    "service_subtype": p.service_subtype,
                    "decoded_parameters": p.decoded_parameters,
                    "errors": p.decode_errors
                }
                for p in packets
            ]
        })
    
    return jsonify({"packets": []})


@app.route('/api/telemetry/source', methods=['GET'])
def get_telemetry_source():
    """Get current telemetry source."""
    global current_telemetry_source
    return jsonify({"source": current_telemetry_source})


@app.route('/api/telemetry/source', methods=['POST'])
def set_telemetry_source():
    """Set telemetry source (simulation or ccsds)."""
    global current_telemetry_source
    
    data = request.json or {}
    source = data.get('source', 'simulation')
    
    if source not in ['simulation', 'ccsds']:
        return jsonify({
            "success": False,
            "error": "Invalid source. Use 'simulation' or 'ccsds'"
        }), 400
    
    current_telemetry_source = source
    return jsonify({
        "success": True,
        "source": current_telemetry_source
    })


# ============================================
# API Routes - Constellation
# ============================================

@app.route('/api/constellation', methods=['GET'])
def get_constellation():
    """Get constellation satellite data."""
    import math
    t = time.time()
    
    # Simulated constellation data
    satellites = [
        {"id": "AURORA-01", "norad": 55001, "status": "nominal", "sma": 6778, "ecc": 0.0001, "inc": 51.6, "raan": 0, "argp": 45, "ta": (0 + t * 0.01) % 360},
        {"id": "AURORA-02", "norad": 55002, "status": "nominal", "sma": 6778, "ecc": 0.0002, "inc": 51.6, "raan": 60, "argp": 90, "ta": (60 + t * 0.01) % 360},
        {"id": "AURORA-03", "norad": 55003, "status": "nominal", "sma": 6778, "ecc": 0.0001, "inc": 51.6, "raan": 120, "argp": 135, "ta": (120 + t * 0.01) % 360},
        {"id": "AURORA-04", "norad": 55004, "status": "warning", "sma": 6778, "ecc": 0.0003, "inc": 51.6, "raan": 180, "argp": 180, "ta": (180 + t * 0.01) % 360},
        {"id": "AURORA-05", "norad": 55005, "status": "nominal", "sma": 6778, "ecc": 0.0001, "inc": 51.6, "raan": 240, "argp": 225, "ta": (240 + t * 0.01) % 360},
        {"id": "AURORA-06", "norad": 55006, "status": "nominal", "sma": 6778, "ecc": 0.0002, "inc": 51.6, "raan": 300, "argp": 270, "ta": (300 + t * 0.01) % 360},
        {"id": "AURORA-07", "norad": 55007, "status": "nominal", "sma": 7178, "ecc": 0.0001, "inc": 97.4, "raan": 45, "argp": 0, "ta": (90 + t * 0.01) % 360},
        {"id": "AURORA-08", "norad": 55008, "status": "nominal", "sma": 7178, "ecc": 0.0001, "inc": 97.4, "raan": 225, "argp": 180, "ta": (270 + t * 0.01) % 360}
    ]
    
    # Calculate derived values
    for sat in satellites:
        sat["altitude_km"] = sat["sma"] - 6371
        sat["period_min"] = 2 * math.pi * math.sqrt(sat["sma"]**3 / 398600) / 60
        sat["velocity_kms"] = math.sqrt(398600 / sat["sma"])
        
        # Simulate health metrics
        if sat["status"] == "nominal":
            sat["health"] = {
                "power": 85 + (hash(sat["id"]) % 10),
                "comm": "OK",
                "aocs": "OK",
                "thermal": "OK"
            }
        else:
            sat["health"] = {
                "power": 70 + (hash(sat["id"]) % 10),
                "comm": "DEGRADED",
                "aocs": "OK",
                "thermal": "OK"
            }
    
    # Calculate constellation stats
    stats = {
        "total": len(satellites),
        "nominal": sum(1 for s in satellites if s["status"] == "nominal"),
        "warning": sum(1 for s in satellites if s["status"] == "warning"),
        "critical": sum(1 for s in satellites if s["status"] == "critical"),
        "coverage_percent": 94.5
    }
    
    return jsonify({
        "satellites": satellites,
        "stats": stats,
        "timestamp": t
    })


@app.route('/api/constellation/<sat_id>', methods=['GET'])
def get_satellite_details(sat_id):
    """Get detailed data for a specific satellite."""
    import math
    t = time.time()
    
    # Find satellite (simplified)
    sat_data = {
        "id": sat_id,
        "norad": 55001,
        "status": "nominal",
        "orbital_elements": {
            "sma": 6778,
            "ecc": 0.0001,
            "inc": 51.6,
            "raan": 0,
            "argp": 45,
            "ta": (t * 0.01) % 360
        },
        "position": {
            "altitude_km": 407,
            "latitude_deg": 23.5 * math.sin(t * 0.01),
            "longitude_deg": (t * 0.1) % 360 - 180,
            "velocity_kms": 7.66
        },
        "health": {
            "power_percent": 92,
            "comm_status": "OK",
            "aocs_status": "OK",
            "thermal_status": "OK"
        },
        "link_budget": {
            "uplink_snr_db": 14.2,
            "downlink_snr_db": 12.8,
            "data_rate_mbps": 2.4
        },
        "next_pass": {
            "aos_utc": "12:45:30",
            "los_utc": "12:54:02",
            "max_elevation_deg": 67.5
        }
    }
    
    return jsonify(sat_data)


# ============================================
# Main Entry Point
# ============================================

def main():
    """Main entry point."""
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║     █████╗ ██╗   ██╗██████╗  ██████╗ ██████╗  █████╗          ║
    ║    ██╔══██╗██║   ██║██╔══██╗██╔═══██╗██╔══██╗██╔══██╗         ║
    ║    ███████║██║   ██║██████╔╝██║   ██║██████╔╝███████║         ║
    ║    ██╔══██║██║   ██║██╔══██╗██║   ██║██╔══██╗██╔══██║         ║
    ║    ██║  ██║╚██████╔╝██║  ██║╚██████╔╝██║  ██║██║  ██║         ║
    ║    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝         ║
    ║                                                               ║
    ║          SATELLITE OPERATIONS CONTROL CENTER                  ║
    ║              V&V Testing Environment v1.0                     ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)
    
    # Initialize subsystems
    init_systems()
    
    # Get port from environment or use default
    port = int(os.environ.get('SOCC_PORT', 5050))
    
    print(f"\n🚀 Starting SOCC server on http://localhost:{port}")
    print("   Open this URL in your browser to access the SOCC console.\n")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()

