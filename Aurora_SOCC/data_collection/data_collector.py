#!/usr/bin/env python3
"""
Data Collection and Logging System for SOCC V&V Testing

Collects, stores, and exports test data from:
- AOCS SCOE (simulation state)
- EGSE (hardware interface)
- FlatSat telemetry
- Operator commands
- Test procedure execution
"""

import json
import csv
import time
import threading
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from pathlib import Path
from collections import deque
import gzip

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DataCollector")


@dataclass
class DataPoint:
    """Single data point with timestamp and metadata"""
    timestamp: float
    source: str
    category: str
    name: str
    value: Any
    unit: str = ""
    quality: str = "GOOD"
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TelemetryFrame:
    """Complete telemetry frame from satellite"""
    timestamp: float
    frame_id: int
    mission_time: float
    attitude: Dict = field(default_factory=dict)
    rates: Dict = field(default_factory=dict)
    wheel_speeds: List[float] = field(default_factory=list)
    torque_rod_dipoles: List[float] = field(default_factory=list)
    magnetometer: List[float] = field(default_factory=list)
    sun_sensors: List[float] = field(default_factory=list)
    power: Dict = field(default_factory=dict)
    thermal: Dict = field(default_factory=dict)
    mode: str = ""
    flags: Dict = field(default_factory=dict)


@dataclass
class CommandRecord:
    """Record of commanded action"""
    timestamp: float
    command_id: str
    command_type: str
    parameters: Dict = field(default_factory=dict)
    source: str = "SOCC"
    acknowledged: bool = False
    executed: bool = False
    result: str = ""


@dataclass
class EventRecord:
    """Record of system event"""
    timestamp: float
    event_id: str
    severity: str  # INFO, WARNING, ERROR, CRITICAL
    source: str
    message: str
    data: Dict = field(default_factory=dict)


class DataBuffer:
    """Thread-safe circular buffer for data storage"""
    
    def __init__(self, max_size: int = 100000):
        self.max_size = max_size
        self._buffer: deque = deque(maxlen=max_size)
        self._lock = threading.Lock()
    
    def append(self, item):
        with self._lock:
            self._buffer.append(item)
    
    def get_all(self) -> List:
        with self._lock:
            return list(self._buffer)
    
    def get_recent(self, count: int) -> List:
        with self._lock:
            return list(self._buffer)[-count:]
    
    def get_since(self, timestamp: float) -> List:
        with self._lock:
            return [item for item in self._buffer 
                    if hasattr(item, 'timestamp') and item.timestamp >= timestamp]
    
    def clear(self):
        with self._lock:
            self._buffer.clear()
    
    def __len__(self):
        return len(self._buffer)


class DataCollector:
    """
    Central data collection system for SOCC testing.
    
    Collects data from multiple sources, maintains rolling buffers,
    and provides export capabilities for post-test analysis.
    """
    
    def __init__(self, output_dir: str = None):
        """
        Initialize data collector.
        
        Args:
            output_dir: Directory for data output files
        """
        self.output_dir = Path(output_dir) if output_dir else Path("data_collection/output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Data buffers
        self.telemetry_buffer = DataBuffer(max_size=100000)
        self.command_buffer = DataBuffer(max_size=10000)
        self.event_buffer = DataBuffer(max_size=50000)
        self.raw_data_buffer = DataBuffer(max_size=500000)
        
        # Session info
        self.session_id: str = ""
        self.session_start: Optional[datetime] = None
        self.session_scenario: str = ""
        self.is_recording: bool = False
        
        # Statistics
        self.stats = {
            "telemetry_frames": 0,
            "commands": 0,
            "events": 0,
            "raw_points": 0,
            "errors": 0
        }
        
        # File handles for live recording
        self._telemetry_file = None
        self._command_file = None
        self._event_file = None
        
        # Callbacks
        self._telemetry_callbacks: List[Callable] = []
        self._event_callbacks: List[Callable] = []
        
        logger.info(f"Data collector initialized. Output: {self.output_dir}")
    
    def start_session(self, scenario_name: str = "", tester: str = ""):
        """
        Start a new data collection session.
        
        Args:
            scenario_name: Name of the test scenario
            tester: Name of the person running the test
        """
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_start = datetime.now()
        self.session_scenario = scenario_name
        
        # Clear buffers
        self.telemetry_buffer.clear()
        self.command_buffer.clear()
        self.event_buffer.clear()
        self.raw_data_buffer.clear()
        
        # Reset stats
        for key in self.stats:
            self.stats[key] = 0
        
        # Create session directory
        self.session_dir = self.output_dir / self.session_id
        self.session_dir.mkdir(exist_ok=True)
        
        # Save session metadata
        metadata = {
            "session_id": self.session_id,
            "start_time": self.session_start.isoformat(),
            "scenario": scenario_name,
            "tester": tester
        }
        with open(self.session_dir / "session_info.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        self.log_event("SESSION", "INFO", "DataCollector", 
                      f"Session started: {scenario_name}")
        
        logger.info(f"Session started: {self.session_id}")
    
    def stop_session(self):
        """Stop the current session and finalize data."""
        if not self.session_id:
            return
        
        self.log_event("SESSION", "INFO", "DataCollector", "Session ended")
        
        # Update metadata
        metadata_file = self.session_dir / "session_info.json"
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            metadata["end_time"] = datetime.now().isoformat()
            metadata["duration_sec"] = (datetime.now() - self.session_start).total_seconds()
            metadata["statistics"] = self.stats.copy()
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        
        # Export all data
        self.export_all()
        
        self.stop_recording()
        
        logger.info(f"Session stopped: {self.session_id}")
        self.session_id = ""
    
    def start_recording(self):
        """Start live recording to files."""
        if not self.session_id:
            logger.warning("No session active - start a session first")
            return
        
        self.is_recording = True
        
        # Open live recording files
        self._telemetry_file = open(
            self.session_dir / "telemetry_live.csv", 'w', newline=''
        )
        self._command_file = open(
            self.session_dir / "commands_live.csv", 'w', newline=''
        )
        self._event_file = open(
            self.session_dir / "events_live.csv", 'w', newline=''
        )
        
        # Write headers
        self._telemetry_writer = csv.writer(self._telemetry_file)
        self._telemetry_writer.writerow([
            "timestamp", "frame_id", "mission_time", "mode",
            "roll", "pitch", "yaw", "roll_rate", "pitch_rate", "yaw_rate",
            "rw1", "rw2", "rw3", "rw4", "bus_voltage", "current"
        ])
        
        self._command_writer = csv.writer(self._command_file)
        self._command_writer.writerow([
            "timestamp", "command_id", "command_type", "parameters", 
            "source", "acknowledged", "executed", "result"
        ])
        
        self._event_writer = csv.writer(self._event_file)
        self._event_writer.writerow([
            "timestamp", "event_id", "severity", "source", "message"
        ])
        
        self.log_event("RECORDING", "INFO", "DataCollector", "Recording started")
        logger.info("Live recording started")
    
    def stop_recording(self):
        """Stop live recording."""
        self.is_recording = False
        
        # Close files
        if self._telemetry_file:
            self._telemetry_file.close()
            self._telemetry_file = None
        if self._command_file:
            self._command_file.close()
            self._command_file = None
        if self._event_file:
            self._event_file.close()
            self._event_file = None
        
        logger.info("Live recording stopped")
    
    def record_telemetry(self, frame: TelemetryFrame):
        """
        Record a telemetry frame.
        
        Args:
            frame: Telemetry frame to record
        """
        self.telemetry_buffer.append(frame)
        self.stats["telemetry_frames"] += 1
        
        # Live recording
        if self.is_recording and self._telemetry_writer:
            try:
                wheel_speeds = frame.wheel_speeds[:4] if frame.wheel_speeds else [0, 0, 0, 0]
                self._telemetry_writer.writerow([
                    frame.timestamp, frame.frame_id, frame.mission_time, frame.mode,
                    frame.attitude.get("roll", 0), frame.attitude.get("pitch", 0), 
                    frame.attitude.get("yaw", 0),
                    frame.rates.get("roll", 0), frame.rates.get("pitch", 0),
                    frame.rates.get("yaw", 0),
                    wheel_speeds[0], wheel_speeds[1], wheel_speeds[2], wheel_speeds[3],
                    frame.power.get("voltage", 0), frame.power.get("current", 0)
                ])
                self._telemetry_file.flush()
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"Error writing telemetry: {e}")
        
        # Notify callbacks
        for callback in self._telemetry_callbacks:
            try:
                callback(frame)
            except Exception as e:
                logger.error(f"Telemetry callback error: {e}")
    
    def record_command(self, command: CommandRecord):
        """
        Record a command.
        
        Args:
            command: Command record
        """
        self.command_buffer.append(command)
        self.stats["commands"] += 1
        
        # Live recording
        if self.is_recording and self._command_writer:
            try:
                self._command_writer.writerow([
                    command.timestamp, command.command_id, command.command_type,
                    json.dumps(command.parameters), command.source,
                    command.acknowledged, command.executed, command.result
                ])
                self._command_file.flush()
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"Error writing command: {e}")
    
    def log_event(self, event_id: str, severity: str, source: str, 
                  message: str, data: Dict = None):
        """
        Log an event.
        
        Args:
            event_id: Event identifier
            severity: INFO, WARNING, ERROR, CRITICAL
            source: Source of the event
            message: Event message
            data: Additional event data
        """
        event = EventRecord(
            timestamp=time.time(),
            event_id=event_id,
            severity=severity,
            source=source,
            message=message,
            data=data or {}
        )
        
        self.event_buffer.append(event)
        self.stats["events"] += 1
        
        # Live recording
        if self.is_recording and self._event_writer:
            try:
                self._event_writer.writerow([
                    event.timestamp, event.event_id, event.severity,
                    event.source, event.message
                ])
                self._event_file.flush()
            except Exception as e:
                self.stats["errors"] += 1
        
        # Notify callbacks
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")
    
    def record_raw_data(self, source: str, category: str, name: str, 
                        value: Any, unit: str = ""):
        """
        Record a raw data point.
        
        Args:
            source: Data source
            category: Data category
            name: Parameter name
            value: Parameter value
            unit: Unit of measurement
        """
        point = DataPoint(
            timestamp=time.time(),
            source=source,
            category=category,
            name=name,
            value=value,
            unit=unit
        )
        
        self.raw_data_buffer.append(point)
        self.stats["raw_points"] += 1
    
    def export_all(self):
        """Export all buffered data to files."""
        if not self.session_id:
            logger.warning("No session active")
            return
        
        self._export_telemetry()
        self._export_commands()
        self._export_events()
        self._export_raw_data()
        
        logger.info(f"All data exported to {self.session_dir}")
    
    def _export_telemetry(self):
        """Export telemetry data."""
        data = self.telemetry_buffer.get_all()
        if not data:
            return
        
        # JSON export
        with open(self.session_dir / "telemetry.json", 'w') as f:
            json.dump([asdict(frame) for frame in data], f, indent=2)
        
        # CSV export
        with open(self.session_dir / "telemetry.csv", 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "frame_id", "mission_time", "mode",
                "q0", "q1", "q2", "q3",
                "roll", "pitch", "yaw",
                "roll_rate", "pitch_rate", "yaw_rate",
                "rw1", "rw2", "rw3", "rw4",
                "mag_x", "mag_y", "mag_z",
                "bus_voltage", "current", "battery_soc"
            ])
            
            for frame in data:
                wheel_speeds = frame.wheel_speeds[:4] if frame.wheel_speeds else [0, 0, 0, 0]
                magnetometer = frame.magnetometer[:3] if frame.magnetometer else [0, 0, 0]
                writer.writerow([
                    frame.timestamp, frame.frame_id, frame.mission_time, frame.mode,
                    frame.attitude.get("q0", 0), frame.attitude.get("q1", 0),
                    frame.attitude.get("q2", 0), frame.attitude.get("q3", 0),
                    frame.attitude.get("roll", 0), frame.attitude.get("pitch", 0),
                    frame.attitude.get("yaw", 0),
                    frame.rates.get("roll", 0), frame.rates.get("pitch", 0),
                    frame.rates.get("yaw", 0),
                    wheel_speeds[0], wheel_speeds[1], wheel_speeds[2], wheel_speeds[3],
                    magnetometer[0], magnetometer[1], magnetometer[2],
                    frame.power.get("voltage", 0), frame.power.get("current", 0),
                    frame.power.get("battery_soc", 0)
                ])
    
    def _export_commands(self):
        """Export command data."""
        data = self.command_buffer.get_all()
        if not data:
            return
        
        with open(self.session_dir / "commands.json", 'w') as f:
            json.dump([asdict(cmd) for cmd in data], f, indent=2)
    
    def _export_events(self):
        """Export event data."""
        data = self.event_buffer.get_all()
        if not data:
            return
        
        with open(self.session_dir / "events.json", 'w') as f:
            json.dump([asdict(evt) for evt in data], f, indent=2)
    
    def _export_raw_data(self):
        """Export raw data points."""
        data = self.raw_data_buffer.get_all()
        if not data:
            return
        
        # Compress large raw data files
        with gzip.open(self.session_dir / "raw_data.json.gz", 'wt') as f:
            json.dump([point.to_dict() for point in data], f)
    
    def get_statistics(self) -> Dict:
        """Get collection statistics."""
        return {
            "session_id": self.session_id,
            "session_start": self.session_start.isoformat() if self.session_start else None,
            "is_recording": self.is_recording,
            "buffers": {
                "telemetry": len(self.telemetry_buffer),
                "commands": len(self.command_buffer),
                "events": len(self.event_buffer),
                "raw_data": len(self.raw_data_buffer)
            },
            "statistics": self.stats.copy()
        }
    
    def register_telemetry_callback(self, callback: Callable):
        """Register callback for telemetry frames."""
        self._telemetry_callbacks.append(callback)
    
    def register_event_callback(self, callback: Callable):
        """Register callback for events."""
        self._event_callbacks.append(callback)


# Global collector instance
_collector: Optional[DataCollector] = None


def get_collector() -> DataCollector:
    """Get or create global data collector."""
    global _collector
    if _collector is None:
        _collector = DataCollector()
    return _collector


if __name__ == "__main__":
    # Demo
    collector = DataCollector("./test_output")
    
    collector.start_session("Test Scenario", "Test Engineer")
    collector.start_recording()
    
    # Simulate some data
    for i in range(10):
        frame = TelemetryFrame(
            timestamp=time.time(),
            frame_id=i,
            mission_time=i * 1.0,
            attitude={"roll": 0.1 * i, "pitch": 0.05 * i, "yaw": 0.02 * i},
            rates={"roll": 0.01, "pitch": 0.005, "yaw": 0.002},
            wheel_speeds=[100 + i, 200 - i, 150, 1000],
            mode="NOMINAL"
        )
        collector.record_telemetry(frame)
        
        collector.log_event("TLM", "INFO", "Test", f"Frame {i} received")
        time.sleep(0.1)
    
    collector.stop_session()
    
    print("\n=== Collection Statistics ===")
    stats = collector.get_statistics()
    print(json.dumps(stats, indent=2))

