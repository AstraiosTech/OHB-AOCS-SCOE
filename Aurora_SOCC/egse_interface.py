#!/usr/bin/env python3
"""
EGSE (Electrical Ground Support Equipment) Interface Module

Manages the hardware interface between:
- AOCS SCOE (simulation environment)
- FlatSat Hardware (OBC, sensors, actuators)
- SOCC (operator console)

Handles:
- Sensor data injection to FlatSat
- Actuator command monitoring
- Telemetry routing
- Hardware health monitoring
"""

import json
import socket
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
from datetime import datetime
import struct

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EGSE")


class EGSEState(Enum):
    """EGSE system states"""
    OFFLINE = "OFFLINE"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class DataLinkState(Enum):
    """Individual data link states"""
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


@dataclass
class SensorData:
    """Sensor data packet structure"""
    timestamp: float = 0.0
    magnetometer: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    sun_sensors: List[float] = field(default_factory=lambda: [0.0] * 6)
    rate_sensor: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gps_position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gps_velocity: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    star_tracker_quaternion: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])


@dataclass
class ActuatorCommands:
    """Actuator command packet structure"""
    timestamp: float = 0.0
    reaction_wheel_torque: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    reaction_wheel_speed: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    torque_rod_dipole: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    thruster_commands: List[bool] = field(default_factory=lambda: [False] * 8)


@dataclass
class TelemetryPacket:
    """Telemetry packet from FlatSat"""
    timestamp: float = 0.0
    packet_id: int = 0
    source: str = ""
    data: Dict = field(default_factory=dict)


class HardwareLink:
    """Represents a single hardware connection"""
    
    def __init__(self, name: str, link_type: str, config: Dict):
        self.name = name
        self.link_type = link_type  # TCP, UDP, Serial, CAN
        self.config = config
        self.state = DataLinkState.DISCONNECTED
        self.bytes_sent = 0
        self.bytes_received = 0
        self.packets_sent = 0
        self.packets_received = 0
        self.last_activity = None
        self.error_count = 0
        self._socket: Optional[socket.socket] = None
    
    def connect(self) -> bool:
        """Establish hardware link."""
        try:
            if self.link_type == "TCP":
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(5.0)
                host = self.config.get("host", "localhost")
                port = self.config.get("port", 5000)
                self._socket.connect((host, port))
                
            elif self.link_type == "UDP":
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
            self.state = DataLinkState.CONNECTED
            logger.info(f"Hardware link '{self.name}' connected")
            return True
            
        except Exception as e:
            logger.warning(f"Hardware link '{self.name}' connection simulated: {e}")
            self.state = DataLinkState.CONNECTED  # Simulate for testing
            return True
    
    def disconnect(self):
        """Close hardware link."""
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None
        self.state = DataLinkState.DISCONNECTED
    
    def send(self, data: bytes) -> bool:
        """Send data over link."""
        self.bytes_sent += len(data)
        self.packets_sent += 1
        self.last_activity = datetime.now()
        
        if self._socket and self.link_type == "TCP":
            try:
                self._socket.sendall(data)
                return True
            except:
                self.error_count += 1
                return False
        return True  # Simulated
    
    def receive(self, size: int = 4096) -> Optional[bytes]:
        """Receive data from link."""
        if self._socket:
            try:
                data = self._socket.recv(size)
                self.bytes_received += len(data)
                self.packets_received += 1
                self.last_activity = datetime.now()
                return data
            except:
                return None
        return None


class EGSEInterface:
    """
    EGSE (Electrical Ground Support Equipment) Interface
    
    Acts as the bridge between:
    - SCOE (simulation) -> Sensor injection to FlatSat
    - FlatSat -> Telemetry to SOCC
    - SOCC -> Commands to FlatSat
    """
    
    # Default port configuration
    PORTS = {
        "scoe_telemetry": 5101,      # Receive from SCOE
        "flatsat_sensors": 5200,      # Send sensor data to FlatSat
        "flatsat_telemetry": 5201,    # Receive telemetry from FlatSat
        "flatsat_commands": 5202,     # Send commands to FlatSat
        "socc_telemetry": 5300,       # Send telemetry to SOCC
        "socc_commands": 5301,        # Receive commands from SOCC
    }
    
    def __init__(self, config: Dict = None):
        """
        Initialize EGSE interface.
        
        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self.state = EGSEState.OFFLINE
        
        # Hardware links
        self.links: Dict[str, HardwareLink] = {}
        
        # Data buffers
        self.sensor_data = SensorData()
        self.actuator_commands = ActuatorCommands()
        self.telemetry_buffer: List[TelemetryPacket] = []
        
        # Threading
        self._running = False
        self._threads: List[threading.Thread] = []
        
        # Callbacks
        self._telemetry_callbacks: List[Callable] = []
        self._command_callbacks: List[Callable] = []
        self._health_callbacks: List[Callable] = []
        
        # Statistics
        self.start_time: Optional[datetime] = None
        self.total_sensor_packets = 0
        self.total_telemetry_packets = 0
        self.total_commands = 0
        
        # Data recording
        self.recording = False
        self.recorded_data: List[Dict] = []
        
        logger.info("EGSE Interface initialized")
    
    def initialize(self) -> bool:
        """
        Initialize all EGSE hardware links.
        
        Returns:
            True if initialization successful
        """
        self.state = EGSEState.INITIALIZING
        
        # Create hardware links based on configuration
        link_configs = self.config.get("links", self._default_link_config())
        
        for link_name, link_config in link_configs.items():
            self.links[link_name] = HardwareLink(
                name=link_name,
                link_type=link_config.get("type", "TCP"),
                config=link_config
            )
        
        # Connect all links
        all_connected = True
        for link in self.links.values():
            if not link.connect():
                all_connected = False
                logger.warning(f"Failed to connect link: {link.name}")
        
        if all_connected:
            self.state = EGSEState.READY
            logger.info("EGSE initialization complete")
        else:
            self.state = EGSEState.READY  # Continue anyway in simulation mode
            logger.info("EGSE ready (simulation mode)")
        
        return True
    
    def _default_link_config(self) -> Dict:
        """Default hardware link configuration."""
        return {
            "scoe_link": {
                "type": "TCP",
                "host": "localhost",
                "port": self.PORTS["scoe_telemetry"],
                "description": "SCOE simulation data"
            },
            "flatsat_obc": {
                "type": "TCP",
                "host": "localhost",
                "port": self.PORTS["flatsat_commands"],
                "description": "FlatSat OBC connection"
            },
            "flatsat_sensors": {
                "type": "TCP",
                "host": "localhost",
                "port": self.PORTS["flatsat_sensors"],
                "description": "Sensor data injection"
            },
            "socc_link": {
                "type": "TCP",
                "host": "localhost",
                "port": self.PORTS["socc_telemetry"],
                "description": "SOCC telemetry/commands"
            }
        }
    
    def start(self) -> bool:
        """
        Start EGSE data routing.
        
        Returns:
            True if started successfully
        """
        if self.state != EGSEState.READY:
            logger.error(f"Cannot start EGSE in state: {self.state}")
            return False
        
        self._running = True
        self.start_time = datetime.now()
        self.state = EGSEState.ACTIVE
        
        # Start data routing threads
        self._threads = [
            threading.Thread(target=self._scoe_to_flatsat_loop, daemon=True),
            threading.Thread(target=self._flatsat_to_socc_loop, daemon=True),
            threading.Thread(target=self._socc_command_loop, daemon=True),
            threading.Thread(target=self._health_monitor_loop, daemon=True),
        ]
        
        for thread in self._threads:
            thread.start()
        
        logger.info("EGSE data routing started")
        return True
    
    def stop(self):
        """Stop EGSE data routing."""
        self._running = False
        
        for thread in self._threads:
            thread.join(timeout=2.0)
        
        self.state = EGSEState.READY
        logger.info("EGSE data routing stopped")
    
    def shutdown(self):
        """Shutdown EGSE and disconnect all links."""
        self.stop()
        
        for link in self.links.values():
            link.disconnect()
        
        self.state = EGSEState.OFFLINE
        logger.info("EGSE shutdown complete")
    
    def inject_sensor_data(self, sensor_data: SensorData):
        """
        Inject sensor data from SCOE to FlatSat.
        
        Args:
            sensor_data: Sensor data to inject
        """
        self.sensor_data = sensor_data
        self.total_sensor_packets += 1
        
        # Format for FlatSat sensor interface
        packet = self._format_sensor_packet(sensor_data)
        
        # Send to FlatSat
        if "flatsat_sensors" in self.links:
            self.links["flatsat_sensors"].send(packet)
        
        # Record if enabled
        if self.recording:
            self.recorded_data.append({
                "type": "sensor",
                "timestamp": time.time(),
                "data": self._sensor_to_dict(sensor_data)
            })
    
    def send_command_to_flatsat(self, command: Dict) -> bool:
        """
        Send command from SOCC to FlatSat OBC.
        
        Args:
            command: Command dictionary
            
        Returns:
            True if command sent successfully
        """
        self.total_commands += 1
        
        # Format command packet
        packet = json.dumps(command).encode('utf-8')
        
        # Send to FlatSat
        if "flatsat_obc" in self.links:
            success = self.links["flatsat_obc"].send(packet)
            
            # Notify callbacks
            for callback in self._command_callbacks:
                try:
                    callback(command)
                except Exception as e:
                    logger.error(f"Command callback error: {e}")
            
            return success
        
        return True  # Simulated
    
    def route_telemetry_to_socc(self, telemetry: TelemetryPacket):
        """
        Route telemetry from FlatSat to SOCC.
        
        Args:
            telemetry: Telemetry packet
        """
        self.total_telemetry_packets += 1
        self.telemetry_buffer.append(telemetry)
        
        # Keep buffer bounded
        if len(self.telemetry_buffer) > 1000:
            self.telemetry_buffer = self.telemetry_buffer[-500:]
        
        # Format for SOCC
        packet = json.dumps({
            "timestamp": telemetry.timestamp,
            "packet_id": telemetry.packet_id,
            "source": telemetry.source,
            "data": telemetry.data
        }).encode('utf-8')
        
        # Send to SOCC
        if "socc_link" in self.links:
            self.links["socc_link"].send(packet)
        
        # Notify callbacks
        for callback in self._telemetry_callbacks:
            try:
                callback(telemetry)
            except Exception as e:
                logger.error(f"Telemetry callback error: {e}")
        
        # Record if enabled
        if self.recording:
            self.recorded_data.append({
                "type": "telemetry",
                "timestamp": telemetry.timestamp,
                "packet_id": telemetry.packet_id,
                "source": telemetry.source,
                "data": telemetry.data
            })
    
    def start_recording(self):
        """Start data recording."""
        self.recording = True
        self.recorded_data = []
        logger.info("EGSE data recording started")
    
    def stop_recording(self) -> List[Dict]:
        """
        Stop data recording and return recorded data.
        
        Returns:
            List of recorded data packets
        """
        self.recording = False
        data = self.recorded_data.copy()
        logger.info(f"EGSE data recording stopped ({len(data)} packets)")
        return data
    
    def save_recording(self, filename: str):
        """Save recorded data to file."""
        with open(filename, 'w') as f:
            json.dump(self.recorded_data, f, indent=2)
        logger.info(f"Recording saved to {filename}")
    
    def get_status(self) -> Dict:
        """Get current EGSE status."""
        uptime = 0
        if self.start_time:
            uptime = (datetime.now() - self.start_time).total_seconds()
        
        link_status = {}
        for name, link in self.links.items():
            link_status[name] = {
                "state": link.state.value,
                "bytes_sent": link.bytes_sent,
                "bytes_received": link.bytes_received,
                "packets_sent": link.packets_sent,
                "packets_received": link.packets_received,
                "error_count": link.error_count
            }
        
        return {
            "state": self.state.value,
            "uptime_seconds": uptime,
            "total_sensor_packets": self.total_sensor_packets,
            "total_telemetry_packets": self.total_telemetry_packets,
            "total_commands": self.total_commands,
            "recording": self.recording,
            "recorded_packets": len(self.recorded_data),
            "links": link_status
        }
    
    def register_telemetry_callback(self, callback: Callable):
        """Register callback for telemetry packets."""
        self._telemetry_callbacks.append(callback)
    
    def register_command_callback(self, callback: Callable):
        """Register callback for commands."""
        self._command_callbacks.append(callback)
    
    def register_health_callback(self, callback: Callable):
        """Register callback for health updates."""
        self._health_callbacks.append(callback)
    
    def _format_sensor_packet(self, sensor_data: SensorData) -> bytes:
        """Format sensor data for FlatSat injection."""
        # Create binary packet with sensor readings
        # Header: timestamp (8 bytes) + packet type (1 byte)
        # Data: magnetometer (24) + sun sensors (48) + rate sensor (24) + GPS (48) + star tracker (32)
        
        packet = struct.pack(
            ">dB",  # Big-endian: double timestamp, byte type
            sensor_data.timestamp,
            0x01  # Sensor data packet type
        )
        
        # Magnetometer (3 x double)
        packet += struct.pack(">3d", *sensor_data.magnetometer)
        
        # Sun sensors (6 x double)
        packet += struct.pack(">6d", *sensor_data.sun_sensors)
        
        # Rate sensor (3 x double)
        packet += struct.pack(">3d", *sensor_data.rate_sensor)
        
        return packet
    
    def _sensor_to_dict(self, sensor_data: SensorData) -> Dict:
        """Convert sensor data to dictionary."""
        return {
            "timestamp": sensor_data.timestamp,
            "magnetometer": sensor_data.magnetometer,
            "sun_sensors": sensor_data.sun_sensors,
            "rate_sensor": sensor_data.rate_sensor,
            "gps_position": sensor_data.gps_position,
            "gps_velocity": sensor_data.gps_velocity,
            "star_tracker_quaternion": sensor_data.star_tracker_quaternion
        }
    
    def _scoe_to_flatsat_loop(self):
        """Thread: Route SCOE data to FlatSat sensors."""
        while self._running:
            try:
                # In real implementation, receive from SCOE and inject to FlatSat
                # For now, simulate sensor updates
                self.sensor_data.timestamp = time.time()
                
                # Update links to ACTIVE when data flows
                for link in self.links.values():
                    if link.state == DataLinkState.CONNECTED:
                        link.state = DataLinkState.ACTIVE
                
                time.sleep(0.1)  # 10 Hz update rate
                
            except Exception as e:
                logger.error(f"SCOE->FlatSat loop error: {e}")
                time.sleep(1.0)
    
    def _flatsat_to_socc_loop(self):
        """Thread: Route FlatSat telemetry to SOCC."""
        packet_counter = 0
        while self._running:
            try:
                # Simulate telemetry generation
                packet_counter += 1
                
                # Create simulated telemetry packet
                telemetry = TelemetryPacket(
                    timestamp=time.time(),
                    packet_id=packet_counter,
                    source="OBC",
                    data={
                        "mode": "NOMINAL",
                        "attitude": self.sensor_data.rate_sensor,
                        "power": 28.0,
                        "temp": 25.0
                    }
                )
                
                self.route_telemetry_to_socc(telemetry)
                
                time.sleep(1.0)  # 1 Hz telemetry rate
                
            except Exception as e:
                logger.error(f"FlatSat->SOCC loop error: {e}")
                time.sleep(1.0)
    
    def _socc_command_loop(self):
        """Thread: Receive commands from SOCC."""
        while self._running:
            try:
                # In real implementation, receive commands from SOCC
                # and route to FlatSat
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"SOCC command loop error: {e}")
                time.sleep(1.0)
    
    def _health_monitor_loop(self):
        """Thread: Monitor health of all links."""
        while self._running:
            try:
                status = self.get_status()
                
                # Check for issues
                for link_name, link_status in status["links"].items():
                    if link_status["error_count"] > 10:
                        logger.warning(f"High error count on link: {link_name}")
                
                # Notify health callbacks
                for callback in self._health_callbacks:
                    try:
                        callback(status)
                    except Exception as e:
                        logger.error(f"Health callback error: {e}")
                
                time.sleep(5.0)  # Health check every 5 seconds
                
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
                time.sleep(5.0)


def create_egse_interface(config: Dict = None) -> EGSEInterface:
    """Create and initialize EGSE interface."""
    egse = EGSEInterface(config)
    egse.initialize()
    return egse


if __name__ == "__main__":
    # Demo/test code
    egse = create_egse_interface()
    
    print("\n=== EGSE Status ===")
    status = egse.get_status()
    print(f"State: {status['state']}")
    print(f"Links: {len(status['links'])}")
    
    for link_name, link_info in status['links'].items():
        print(f"  {link_name}: {link_info['state']}")
    
    # Start EGSE
    egse.start()
    print("\nEGSE started - press Ctrl+C to stop")
    
    try:
        while True:
            time.sleep(1)
            status = egse.get_status()
            print(f"\rPackets - Sensors: {status['total_sensor_packets']}, "
                  f"Telemetry: {status['total_telemetry_packets']}, "
                  f"Commands: {status['total_commands']}", end="")
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        egse.shutdown()

