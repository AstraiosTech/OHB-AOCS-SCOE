# AOCS SCOE Controller

A Special Checkout Equipment (SCOE) system for testing and controlling the Aurora AOCS (Attitude and Orbit Control System) via TCP/IP using EDEN/PUS protocol. Features Grafana dashboards for real-time monitoring and control.

Based on OHB-Sweden Technical Proposal BD-OSE-PROP-0843, Section 6.2: Remote Operation - EDEN and PUS-services.

## Architecture

```
┌─────────────────┐     EDEN/PUS      ┌─────────────────┐
│   Mock AOCS     │◄──── TCP/IP ─────►│ SCOE Controller │
│    Server       │     Port 10025    │                 │
│  (Simulation)   │                   │   - REST API    │
└─────────────────┘                   │   - InfluxDB    │
                                      │   - WebSocket   │
                                      └────────┬────────┘
                                               │
                           ┌───────────────────┼───────────────────┐
                           │                   │                   │
                           ▼                   ▼                   ▼
                    ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
                    │  InfluxDB   │     │   Grafana   │     │  REST API   │
                    │  (Metrics)  │◄────│ Dashboards  │     │  Port 8080  │
                    │  Port 8086  │     │  Port 3000  │     │             │
                    └─────────────┘     └─────────────┘     └─────────────┘
```

## Features

### PUS Services Supported (Section 6.2.1)

| Service | Type | Description |
|---------|------|-------------|
| 1 | Request Verification | TM[1,1], TM[1,2], TM[1,7], TM[1,8] |
| 3 | Housekeeping | TC[3,1], TC[3,3], TC[3,5], TC[3,6], TM[3,25], TC[3,27], TC[3,31] |
| 8 | Function Management | TC[8,1] - Start/Stop/Reset simulation, actuator control |
| 17 | Connection Test | TC[17,1], TM[17,2] |
| 20 | Parameter Management | TC[20,3] - Set/stage parameters |

### Simulated Equipment

- **Reaction Wheels (4x)**: Speed, torque control, temperature monitoring
- **Magnetometer**: 3-axis magnetic field measurement
- **Rate Sensor (Gyroscope)**: Angular rate measurement with ARW/RRW simulation
- **Sun Sensors (6x)**: Sun detection and angle measurement
- **Electric Propulsion Thrusters (4x)**: Firing control, temperature monitoring
- **Torque Rods (3x)**: Magnetic dipole moment control
- **SADA (2x)**: Solar array angle control

## Quick Start

### 1. Start Infrastructure (Docker)

```bash
cd scoe
docker-compose up -d
```

This starts:
- **InfluxDB** on port 8086 (metrics storage)
- **Grafana** on port 3001 (dashboards)

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start Mock AOCS Server

In one terminal:

```bash
python run_mock_aocs.py
```

This simulates the OHB AOCS, listening on port 10025 for EDEN/PUS commands.

### 4. Start SCOE Controller

In another terminal:

```bash
python run_scoe_controller.py
```

This connects to the Mock AOCS and provides:
- REST API on port 8080
- Data logging to InfluxDB
- Real-time WebSocket updates

### 5. Access Control Panel

**Recommended: Web Control Panel**

Open http://localhost:8085 in your browser to access the modern graphical control panel.

Features:
- 🎮 Simulation start/stop/reset buttons
- ⚙️ Reaction wheel torque sliders with real-time speed gauges
- 🔥 Thruster fire/stop buttons with firing status indicators
- ☀️ SADA angle controls with dial displays
- 📡 Real-time sensor readings
- 🎯 Attitude display with angular rates

**Alternative: Grafana Dashboards**

Open http://localhost:3001 in your browser.

- **Username**: admin
- **Password**: admin

Navigate to the **AOCS SCOE** folder to find:
- **AOCS SCOE Overview**: Real-time telemetry visualization
- **AOCS SCOE Control**: Basic control panel

## REST API Reference

### Status & Telemetry

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get connection status |
| `/api/telemetry` | GET | Get current telemetry values |
| `/api/ws` | GET | WebSocket for real-time updates |

### Simulation Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/simulation/start` | POST | Start the simulation |
| `/api/simulation/stop` | POST | Stop the simulation |
| `/api/simulation/reset` | POST | Reset simulation to initial conditions |

### Actuator Control

| Endpoint | Method | Body | Description |
|----------|--------|------|-------------|
| `/api/rw/{id}/torque` | POST | `{"torque": 0.05}` | Set reaction wheel torque (Nm) |
| `/api/thruster/{id}/fire` | POST | `{"firing": true}` | Control thruster firing |
| `/api/torquerod/{id}/dipole` | POST | `{"dipole": 10.0}` | Set torque rod dipole (Am²) |
| `/api/sada/{id}/angle` | POST | `{"angle": 45.0}` | Set SADA angle (degrees) |

### Raw PUS Command

| Endpoint | Method | Body | Description |
|----------|--------|------|-------------|
| `/api/command` | POST | `{"service": 17, "subtype": 1, "data": ""}` | Send raw PUS command |

## Example API Usage

### Start Simulation
```bash
curl -X POST http://localhost:8080/api/simulation/start
```

### Set Reaction Wheel Torque
```bash
curl -X POST http://localhost:8080/api/rw/0/torque \
  -H "Content-Type: application/json" \
  -d '{"torque": 0.1}'
```

### Fire Thruster
```bash
curl -X POST http://localhost:8080/api/thruster/0/fire \
  -H "Content-Type: application/json" \
  -d '{"firing": true}'
```

### Get Telemetry
```bash
curl http://localhost:8080/api/telemetry
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AOCS_HOST` | localhost | Mock AOCS server host |
| `AOCS_PORT` | 10025 | Mock AOCS server port |
| `INFLUXDB_URL` | http://localhost:8086 | InfluxDB URL |
| `INFLUXDB_TOKEN` | my-super-secret-token | InfluxDB authentication token |
| `INFLUXDB_ORG` | aocs | InfluxDB organization |
| `INFLUXDB_BUCKET` | telemetry | InfluxDB bucket for telemetry |

### Housekeeping Report Structures

| ID | Interval | Parameters |
|----|----------|------------|
| 1 | 1.0s | Attitude quaternion, angular rates |
| 2 | 0.5s | Reaction wheel speeds, torques, temperatures |
| 3 | 1.0s | Magnetometer, gyroscope, sun sensor data |
| 4 | 1.0s | Thruster firing status, temperatures |
| 5 | 2.0s | SADA angles, deployment status |
| 6 | 1.0s | Simulation time, position, eclipse status |

## Development

### Project Structure

```
scoe/
├── src/
│   ├── pus_protocol.py      # PUS/EDEN protocol implementation
│   ├── aocs_simulation.py   # AOCS simulation models
│   ├── mock_aocs_server.py  # Mock AOCS TCP server
│   └── scoe_controller.py   # SCOE controller with REST API
├── config/
│   └── grafana/
│       └── provisioning/    # Grafana auto-provisioning
├── dashboards/
│   ├── aocs-overview.json   # Telemetry dashboard
│   └── aocs-control.json    # Control panel dashboard
├── docker-compose.yml       # InfluxDB + Grafana
├── requirements.txt         # Python dependencies
├── run_mock_aocs.py         # Mock AOCS entry point
├── run_scoe_controller.py   # SCOE Controller entry point
└── README.md
```

### Adding New Telemetry Parameters

1. Add the parameter to the simulation model in `aocs_simulation.py`
2. Include it in the appropriate HK structure in `mock_aocs_server.py`
3. Add parameter name mapping in `scoe_controller.py`
4. Update Grafana dashboard to display the new parameter

## Connecting to Real OHB AOCS

When the real OHB AOCS hardware is available:

1. Stop the Mock AOCS server
2. Configure the SCOE Controller to connect to the real AOCS:
   ```bash
   python run_scoe_controller.py --aocs-host <REAL_AOCS_IP> --aocs-port 10025
   ```

The system uses the same EDEN/PUS protocol, so the SCOE Controller and Grafana dashboards work unchanged.

## License

Proprietary - OHB-Sweden / Aurora AOCS SCOE Project

