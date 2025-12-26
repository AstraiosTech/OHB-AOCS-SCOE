# Aurora SOCC - Satellite Operations Control Center

## V&V Testing Environment for AOCS SCOE Integration

Aurora SOCC provides a comprehensive environment for testing satellite software through a realistic operations console interface. It follows the "Train as you Fly" philosophy, allowing operators to execute commands and run test procedures exactly as they would during actual mission operations.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           AOCS SCOE                                     │
│  (Attitude & Orbit Control System - Special Check-Out Equipment)        │
│  • Orbital mechanics simulation                                         │
│  • Environment modeling (sun, magnetic field, etc.)                     │
│  • Initial conditions / scenarios                                       │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ Scenario Parameters
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              EGSE                                        │
│  (Electrical Ground Support Equipment)                                   │
│  • Hardware interface to FlatSat                                        │
│  • Sensor data injection                                                │
│  • Telemetry routing                                                    │
│  • Command forwarding                                                   │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ Sensor Data / Commands
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        FlatSat Hardware                                  │
│  • OBC (On-Board Computer) running flight software                      │
│  • GNC Sensors (Magnetometer, Rate Sensor, Sun Sensors)                 │
│  • GNC Actuators (Reaction Wheels, Torque Rods)                         │
│  • Payload units (optional)                                             │
└─────────────────────────────┬───────────────────────────────────────────┘
                              │ Telemetry / Command Response
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                             SOCC                                         │
│  (Satellite Operations Control Center)                                   │
│  • Operator console interface                                           │
│  • Command generation                                                   │
│  • Telemetry display                                                    │
│  • Test procedure execution                                             │
│  • Data collection & analysis                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd Aurora_SOCC
pip install -r requirements.txt
```

### 2. Launch SOCC

```bash
python socc_app.py
```

### 3. Open Browser

Navigate to: **http://localhost:5050**

---

## 📁 Directory Structure

```
Aurora_SOCC/
├── socc_app.py              # Main Flask application
├── aocs_scoe_interface.py   # SCOE communication interface
├── egse_interface.py        # EGSE hardware interface
├── requirements.txt         # Python dependencies
├── README.md               # This file
│
├── scenarios/               # Initial condition files
│   ├── leo_nominal.json
│   ├── leo_eclipse_entry.json
│   ├── detumble_high_rate.json
│   ├── slew_maneuver_large.json
│   ├── safe_mode_recovery.json
│   └── ground_station_pass.json
│
├── test_procedures/         # Test procedure definitions
│   ├── __init__.py
│   └── procedures.py
│
├── data_collection/         # Data logging system
│   ├── __init__.py
│   ├── data_collector.py
│   └── output/              # Recorded session data
│
├── templates/               # HTML templates
│   ├── splash.html          # Scenario selection page
│   └── console.html         # Operations console
│
├── static/                  # Static assets
│   ├── css/
│   │   └── styles.css       # Main stylesheet
│   ├── js/
│   └── images/
│
├── config/                  # Configuration files
│   └── socc_config.json
│
└── logs/                    # Application logs
```

---

## 🎯 Features

### Scenario Selection (Splash Page)
- Visual selection of pre-defined initial conditions
- Scenario details preview
- One-click injection into SCOE
- EGSE/FlatSat connection status

### Operations Console
- **Mode Commands**: Nominal, Safe, Detumble, Sun-Pointing, Nadir
- **Maneuver Commands**: Slew operations in all axes
- **Actuator Commands**: Reaction wheel and torque rod control
- **Real-time Telemetry**: Attitude, rates, wheel speeds, power, thermal
- **Attitude Visualization**: 3D attitude indicator with roll/pitch/yaw
- **Event Logging**: Timestamped command and event history

### Test Procedures
- Pre-defined test procedures (TP-001 through TP-004)
- Step-by-step execution with pass/fail tracking
- Automatic data collection during tests
- Report generation

### Data Collection
- Continuous telemetry recording
- Command logging with acknowledgment tracking
- Event capture with severity levels
- Export to JSON and CSV formats
- Compressed raw data archival

---

## 📋 Pre-defined Scenarios

| ID | Name | Category | Description |
|----|------|----------|-------------|
| LEO_NOMINAL_001 | LEO Nominal Operations | Nominal | Standard LEO conditions for baseline testing |
| LEO_ECLIPSE_001 | LEO Eclipse Entry | Transitions | Entering eclipse - tests power management |
| DETUMBLE_001 | High Rate Detumble | Safe Mode | Post-separation tumbling - tests B-dot |
| SLEW_LARGE_001 | Large Angle Slew | Maneuvers | 90° slew - tests reaction wheel control |
| SAFE_MODE_001 | Safe Mode Recovery | Recovery | Recovery from safe mode to nominal |
| GS_PASS_001 | Ground Station Pass | Communications | Ground contact window simulation |

---

## 📋 Pre-defined Test Procedures

| ID | Name | Category | Steps |
|----|------|----------|-------|
| TP-001 | Nominal Mode Checkout | Functional | 6 |
| TP-002 | Slew Maneuver Test | Maneuver | 6 |
| TP-003 | Safe Mode Entry/Exit | Safe Mode | 6 |
| TP-004 | Momentum Dump Operation | ADCS | 6 |

---

## 🔌 API Endpoints

### Scenarios
- `GET /api/scenarios` - List available scenarios
- `GET /api/scenarios/<id>` - Get scenario details
- `POST /api/inject` - Inject scenario into SCOE

### SCOE Control
- `GET /api/scoe/status` - Get SCOE status
- `POST /api/scoe/start` - Start simulation
- `POST /api/scoe/stop` - Stop simulation

### Commands
- `POST /api/command` - Send command to satellite

### Telemetry
- `GET /api/telemetry` - Get current telemetry snapshot

### Test Procedures
- `GET /api/procedures` - List available procedures
- `GET /api/procedures/<id>` - Get procedure details
- `POST /api/procedures/<id>/start` - Start procedure
- `POST /api/procedures/step/<n>/complete` - Complete step

### Data Collection
- `GET /api/data/status` - Get collection status
- `POST /api/data/export` - Export collected data

---

## 🔧 Configuration

Edit `config/socc_config.json` to customize:

- **SCOE connection** parameters
- **EGSE port** mappings
- **Telemetry** rates and buffer sizes
- **Data collection** settings
- **Display** preferences

---

## 👥 Workflow

### V&V Testing Workflow

1. **GNC Team** creates scenario parameter files defining initial conditions
2. **V&V Lead** approves scenarios for testing
3. **EGSE Team** verifies FlatSat connections
4. **Operator** selects scenario on SOCC splash page
5. **SCOE** is loaded with initial conditions
6. **Operator** executes test procedures from SOCC console
7. **Data Collector** captures all telemetry and commands
8. **Reports** are generated for V&V documentation

---

## 📊 Data Output

Each test session creates a directory under `data_collection/output/` containing:

- `session_info.json` - Session metadata
- `telemetry.json` / `telemetry.csv` - Full telemetry record
- `commands.json` - All commands sent
- `events.json` - System events
- `raw_data.json.gz` - Compressed raw data points
- `telemetry_live.csv` - Live recording file

---

## 🛠️ Development

### Adding New Scenarios

Create a JSON file in `scenarios/` with the required structure:

```json
{
    "scenario_id": "CUSTOM_001",
    "name": "Custom Scenario",
    "description": "Description of the scenario",
    "category": "Custom",
    "version": "1.0.0",
    "orbital_elements": { ... },
    "initial_attitude": { ... },
    "environment": { ... },
    "sensor_injection": { ... },
    "actuator_config": { ... }
}
```

### Adding New Test Procedures

Edit `test_procedures/procedures.py` and add a new factory function:

```python
def create_custom_procedure() -> TestProcedure:
    return TestProcedure(
        procedure_id="TP-XXX",
        name="Custom Procedure",
        steps=[
            TestStep(step_number=1, title="Step 1", ...)
        ]
    )
```

---

## 📝 License

Part of the Aurora FlatSat V&V Testing Suite.

---

## 🤝 Contributing

1. GNC Team: Scenario definitions
2. V&V Team: Test procedure development
3. EGSE Team: Hardware interface integration
4. Software Team: SOCC enhancements

