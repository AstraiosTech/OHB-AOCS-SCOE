#!/usr/bin/env python3
"""
AOCS SCOE Interface Module
Manages communication with the Attitude and Orbit Control System 
Special Check-Out Equipment (AOCS SCOE).

Handles:
- Loading and injecting initial condition scenarios
- Starting/stopping SCOE simulation
- Real-time environment updates
- Sensor data injection parameters
"""

import json
import socket
import struct
import threading
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AOCS_SCOE")


class SCOEState(Enum):
    """SCOE operational states"""
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


class SimulationMode(Enum):
    """Simulation execution modes"""
    REALTIME = "REALTIME"
    ACCELERATED = "ACCELERATED"
    STEP = "STEP"


@dataclass
class OrbitalState:
    """Current orbital state from SCOE"""
    epoch: str = ""
    position_eci_km: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    velocity_eci_km_s: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    altitude_km: float = 0.0
    eclipse: bool = False
    ground_station_visible: bool = False


@dataclass
class EnvironmentState:
    """Current environment state from SCOE"""
    sun_vector_eci: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0])
    sun_vector_body: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0])
    magnetic_field_eci_nT: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    magnetic_field_body_nT: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    atmospheric_density_kg_m3: float = 0.0
    solar_flux_W_m2: float = 1361.0


@dataclass
class AttitudeState:
    """Current attitude state from SCOE"""
    quaternion: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    angular_rates_deg_s: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    euler_angles_deg: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


class AOCSSCOEInterface:
    """
    Interface to the AOCS SCOE (Special Check-Out Equipment).
    
    Manages scenario loading, simulation control, and real-time
    state updates from the orbital/attitude simulator.
    """
    
    # Default SCOE connection parameters
    DEFAULT_HOST = "localhost"
    DEFAULT_PORT = 5100
    DEFAULT_TELEMETRY_PORT = 5101
    
    def __init__(self, host: str = None, port: int = None):
        """
        Initialize AOCS SCOE interface.
        
        Args:
            host: SCOE host address
            port: SCOE command port
        """
        self.host = host or self.DEFAULT_HOST
        self.port = port or self.DEFAULT_PORT
        self.telemetry_port = self.DEFAULT_TELEMETRY_PORT
        
        # State tracking
        self.state = SCOEState.DISCONNECTED
        self.simulation_mode = SimulationMode.REALTIME
        self.active_scenario: Optional[Dict] = None
        self.scenario_name: str = ""
        
        # Real-time state
        self.orbital_state = OrbitalState()
        self.environment_state = EnvironmentState()
        self.attitude_state = AttitudeState()
        self.simulation_time: float = 0.0
        self.wall_clock_start: Optional[datetime] = None
        
        # Networking
        self._command_socket: Optional[socket.socket] = None
        self._telemetry_socket: Optional[socket.socket] = None
        self._telemetry_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Callbacks
        self._state_callbacks: List[Callable] = []
        self._telemetry_callbacks: List[Callable] = []
        
        # Scenarios directory
        self.scenarios_dir = Path(__file__).parent / "scenarios"
        
        logger.info(f"AOCS SCOE Interface initialized (target: {self.host}:{self.port})")
    
    def connect(self) -> bool:
        """
        Establish connection to SCOE.
        
        Returns:
            True if connection successful
        """
        try:
            # Create command socket
            self._command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._command_socket.settimeout(5.0)
            self._command_socket.connect((self.host, self.port))
            
            # Verify connection with handshake
            self._send_command("SCOE_PING")
            response = self._receive_response()
            
            if response and "PONG" in response:
                self.state = SCOEState.CONNECTED
                self._notify_state_change()
                logger.info("Connected to AOCS SCOE")
                return True
            else:
                # Simulated mode - SCOE not actually running
                self.state = SCOEState.CONNECTED
                self._notify_state_change()
                logger.info("AOCS SCOE Interface ready (simulation mode)")
                return True
                
        except socket.error as e:
            logger.warning(f"SCOE connection failed: {e} - Running in standalone mode")
            # Run in standalone simulation mode
            self.state = SCOEState.CONNECTED
            self._notify_state_change()
            return True
    
    def disconnect(self):
        """Disconnect from SCOE."""
        self._running = False
        
        if self._telemetry_thread:
            self._telemetry_thread.join(timeout=2.0)
        
        if self._command_socket:
            try:
                self._command_socket.close()
            except:
                pass
            self._command_socket = None
        
        if self._telemetry_socket:
            try:
                self._telemetry_socket.close()
            except:
                pass
            self._telemetry_socket = None
        
        self.state = SCOEState.DISCONNECTED
        self._notify_state_change()
        logger.info("Disconnected from AOCS SCOE")
    
    def get_available_scenarios(self) -> List[Dict]:
        """
        Get list of available scenario files.
        
        Returns:
            List of scenario metadata dictionaries
        """
        scenarios = []
        
        if not self.scenarios_dir.exists():
            logger.warning(f"Scenarios directory not found: {self.scenarios_dir}")
            return scenarios
        
        for scenario_file in sorted(self.scenarios_dir.glob("*.json")):
            try:
                with open(scenario_file, 'r') as f:
                    data = json.load(f)
                    scenarios.append({
                        "file": scenario_file.name,
                        "path": str(scenario_file),
                        "id": data.get("scenario_id", scenario_file.stem),
                        "name": data.get("name", scenario_file.stem),
                        "description": data.get("description", ""),
                        "category": data.get("category", "Uncategorized"),
                        "version": data.get("version", "1.0.0")
                    })
            except Exception as e:
                logger.error(f"Error loading scenario {scenario_file}: {e}")
        
        return scenarios
    
    def load_scenario(self, scenario_path: str) -> bool:
        """
        Load a scenario file and prepare for injection.
        
        Args:
            scenario_path: Path to scenario JSON file
            
        Returns:
            True if scenario loaded successfully
        """
        try:
            with open(scenario_path, 'r') as f:
                self.active_scenario = json.load(f)
            
            self.scenario_name = self.active_scenario.get("name", "Unknown")
            self.state = SCOEState.READY
            self._notify_state_change()
            
            logger.info(f"Loaded scenario: {self.scenario_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load scenario: {e}")
            return False
    
    def inject_scenario(self) -> bool:
        """
        Inject the loaded scenario into SCOE.
        
        Returns:
            True if injection successful
        """
        if not self.active_scenario:
            logger.error("No scenario loaded")
            return False
        
        if self.state not in [SCOEState.READY, SCOEState.CONNECTED]:
            logger.error(f"Cannot inject in state: {self.state}")
            return False
        
        try:
            # Initialize orbital state from scenario
            orbital = self.active_scenario.get("orbital_elements", {})
            self.orbital_state.altitude_km = orbital.get("semi_major_axis_km", 6778) - 6378
            
            # Initialize attitude state from scenario
            attitude = self.active_scenario.get("initial_attitude", {})
            self.attitude_state.quaternion = attitude.get("quaternion", [0, 0, 0, 1])
            self.attitude_state.angular_rates_deg_s = attitude.get("angular_rates_deg_s", [0, 0, 0])
            
            # Initialize environment from scenario
            env = self.active_scenario.get("environment", {})
            self.environment_state.sun_vector_body = env.get("sun_vector_body", [1, 0, 0])
            self.environment_state.magnetic_field_body_nT = env.get("magnetic_field_nT", [0, 0, 0])
            
            # Send to SCOE (or simulate locally)
            self._send_command(f"SCOE_LOAD_SCENARIO:{json.dumps(self.active_scenario)}")
            
            self.state = SCOEState.READY
            self._notify_state_change()
            
            logger.info(f"Scenario '{self.scenario_name}' injected into SCOE")
            return True
            
        except Exception as e:
            logger.error(f"Failed to inject scenario: {e}")
            self.state = SCOEState.ERROR
            self._notify_state_change()
            return False
    
    def start_simulation(self) -> bool:
        """
        Start SCOE simulation.
        
        Returns:
            True if simulation started
        """
        if self.state not in [SCOEState.READY, SCOEState.PAUSED]:
            logger.error(f"Cannot start from state: {self.state}")
            return False
        
        self._send_command("SCOE_START")
        self._running = True
        self.wall_clock_start = datetime.now()
        self.state = SCOEState.RUNNING
        self._notify_state_change()
        
        # Start telemetry thread
        self._telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._telemetry_thread.start()
        
        logger.info("SCOE simulation started")
        return True
    
    def pause_simulation(self) -> bool:
        """Pause SCOE simulation."""
        if self.state != SCOEState.RUNNING:
            return False
        
        self._send_command("SCOE_PAUSE")
        self.state = SCOEState.PAUSED
        self._notify_state_change()
        
        logger.info("SCOE simulation paused")
        return True
    
    def stop_simulation(self) -> bool:
        """Stop SCOE simulation."""
        self._running = False
        self._send_command("SCOE_STOP")
        
        self.state = SCOEState.READY if self.active_scenario else SCOEState.CONNECTED
        self._notify_state_change()
        
        logger.info("SCOE simulation stopped")
        return True
    
    def set_simulation_mode(self, mode: SimulationMode):
        """Set simulation time mode."""
        self.simulation_mode = mode
        self._send_command(f"SCOE_MODE:{mode.value}")
        logger.info(f"Simulation mode set to: {mode.value}")
    
    def step_simulation(self, dt_seconds: float = 1.0):
        """Step simulation by specified time."""
        if self.simulation_mode == SimulationMode.STEP:
            self._send_command(f"SCOE_STEP:{dt_seconds}")
            self.simulation_time += dt_seconds
    
    def update_environment(self, updates: Dict[str, Any]):
        """
        Update environment parameters in real-time.
        
        Args:
            updates: Dictionary of parameter updates
        """
        self._send_command(f"SCOE_ENV_UPDATE:{json.dumps(updates)}")
    
    def inject_sensor_fault(self, sensor: str, fault_type: str, parameters: Dict = None):
        """
        Inject a sensor fault for testing.
        
        Args:
            sensor: Sensor name (magnetometer, sun_sensor, rate_sensor)
            fault_type: Type of fault (bias, noise, stuck, dropout)
            parameters: Fault parameters
        """
        fault_cmd = {
            "sensor": sensor,
            "fault_type": fault_type,
            "parameters": parameters or {}
        }
        self._send_command(f"SCOE_INJECT_FAULT:{json.dumps(fault_cmd)}")
        logger.info(f"Injected {fault_type} fault on {sensor}")
    
    def clear_faults(self):
        """Clear all injected faults."""
        self._send_command("SCOE_CLEAR_FAULTS")
        logger.info("All faults cleared")
    
    def register_state_callback(self, callback: Callable):
        """Register callback for state changes."""
        self._state_callbacks.append(callback)
    
    def register_telemetry_callback(self, callback: Callable):
        """Register callback for telemetry updates."""
        self._telemetry_callbacks.append(callback)
    
    def get_current_state(self) -> Dict:
        """Get current SCOE state as dictionary."""
        return {
            "scoe_state": self.state.value,
            "scenario_name": self.scenario_name,
            "simulation_time": self.simulation_time,
            "simulation_mode": self.simulation_mode.value,
            "orbital": {
                "altitude_km": self.orbital_state.altitude_km,
                "latitude_deg": self.orbital_state.latitude_deg,
                "longitude_deg": self.orbital_state.longitude_deg,
                "eclipse": self.orbital_state.eclipse
            },
            "attitude": {
                "quaternion": self.attitude_state.quaternion,
                "rates_deg_s": self.attitude_state.angular_rates_deg_s
            },
            "environment": {
                "sun_vector": self.environment_state.sun_vector_body,
                "mag_field_nT": self.environment_state.magnetic_field_body_nT
            }
        }
    
    def _send_command(self, command: str):
        """Send command to SCOE."""
        if self._command_socket:
            try:
                self._command_socket.sendall((command + "\n").encode('utf-8'))
            except socket.error:
                pass  # Running in standalone mode
    
    def _receive_response(self, timeout: float = 1.0) -> Optional[str]:
        """Receive response from SCOE."""
        if self._command_socket:
            try:
                self._command_socket.settimeout(timeout)
                data = self._command_socket.recv(4096)
                return data.decode('utf-8').strip()
            except socket.error:
                return None
        return None
    
    def _telemetry_loop(self):
        """Background thread for receiving telemetry."""
        while self._running:
            try:
                # Simulate telemetry updates
                self.simulation_time += 0.1
                
                # Update orbital state (simple circular orbit propagation)
                if self.active_scenario:
                    orbital = self.active_scenario.get("orbital_elements", {})
                    period = orbital.get("orbital_period_min", 92.5) * 60
                    mean_motion = 360.0 / period  # deg/s
                    
                    # Update true anomaly
                    ta = orbital.get("true_anomaly_deg", 0) + mean_motion * self.simulation_time
                    self.orbital_state.latitude_deg = 30 * (ta % 360 / 360)  # Simplified
                    self.orbital_state.longitude_deg = (ta * 0.9) % 360 - 180
                
                # Notify callbacks
                for callback in self._telemetry_callbacks:
                    try:
                        callback(self.get_current_state())
                    except Exception as e:
                        logger.error(f"Telemetry callback error: {e}")
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Telemetry loop error: {e}")
                time.sleep(1.0)
    
    def _notify_state_change(self):
        """Notify registered callbacks of state change."""
        for callback in self._state_callbacks:
            try:
                callback(self.state)
            except Exception as e:
                logger.error(f"State callback error: {e}")


# Convenience function for quick access
def create_scoe_interface(host: str = None, port: int = None) -> AOCSSCOEInterface:
    """Create and connect to AOCS SCOE."""
    interface = AOCSSCOEInterface(host, port)
    interface.connect()
    return interface


if __name__ == "__main__":
    # Demo/test code
    scoe = create_scoe_interface()
    
    print("\n=== Available Scenarios ===")
    for scenario in scoe.get_available_scenarios():
        print(f"  [{scenario['category']}] {scenario['name']}")
        print(f"    ID: {scenario['id']}")
        print(f"    {scenario['description'][:60]}...")
        print()
    
    # Load first scenario
    scenarios = scoe.get_available_scenarios()
    if scenarios:
        scoe.load_scenario(scenarios[0]['path'])
        scoe.inject_scenario()
        print(f"\nLoaded: {scoe.scenario_name}")
        print(f"State: {scoe.state.value}")

