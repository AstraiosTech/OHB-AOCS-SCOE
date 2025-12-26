#!/usr/bin/env python3
"""
CCSDS UDP Telemetry Receiver for Aurora SOCC

Receives CCSDS/PUS telemetry packets via UDP and decodes them into
telemetry frames that can be used by the SOCC.

Supports:
- CCSDS Space Packet Protocol (SPP) primary header
- PUS (Packet Utilization Standard) secondary header
- ECSS-E-ST-70-41C compliant packet structures
- SDLP Transfer Frame extraction (optional)
"""

import socket
import struct
import threading
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from collections import deque

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CCSDSReceiver")


@dataclass
class CCSDSHeader:
    """CCSDS Space Packet Primary Header (6 bytes)"""
    version: int = 0
    packet_type: int = 0  # 0 = TM, 1 = TC
    sec_hdr_flag: int = 0
    apid: int = 0
    seq_flags: int = 0
    seq_count: int = 0
    data_length: int = 0
    
    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['CCSDSHeader']:
        """Parse CCSDS primary header from bytes"""
        if len(data) < 6:
            return None
        
        byte0, byte1, byte2, byte3, byte4, byte5 = struct.unpack('6B', data[:6])
        
        return cls(
            version=(byte0 >> 5) & 0x07,
            packet_type=(byte0 >> 4) & 0x01,
            sec_hdr_flag=(byte0 >> 3) & 0x01,
            apid=((byte0 & 0x07) << 8) | byte1,
            seq_flags=(byte2 >> 6) & 0x03,
            seq_count=((byte2 & 0x3F) << 8) | byte3,
            data_length=(byte4 << 8) | byte5
        )
    
    @property
    def is_telemetry(self) -> bool:
        return self.packet_type == 0
    
    @property
    def has_secondary_header(self) -> bool:
        return self.sec_hdr_flag == 1
    
    @property
    def total_packet_length(self) -> int:
        """Total packet length including header"""
        return 6 + self.data_length + 1


@dataclass
class PUSHeader:
    """PUS (ECSS) Secondary Header"""
    version: int = 0
    sc_time_ref: int = 0
    service_type: int = 0
    service_subtype: int = 0
    message_type_counter: int = 0
    destination_id: int = 0
    time_stamp: bytes = field(default_factory=bytes)
    
    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 6) -> Optional['PUSHeader']:
        """Parse PUS secondary header from bytes"""
        if len(data) < offset + 7:
            return None
        
        # PUS-C format: version(4b), sc_time_ref(4b), service, subservice, msg_counter(16b), dest_id(16b)
        byte0 = data[offset]
        version = (byte0 >> 4) & 0x0F
        sc_time_ref = byte0 & 0x0F
        service_type = data[offset + 1]
        service_subtype = data[offset + 2]
        
        # Message type counter (2 bytes, big-endian)
        message_type_counter = struct.unpack('>H', data[offset + 3:offset + 5])[0]
        
        # Destination ID (2 bytes, big-endian)
        destination_id = struct.unpack('>H', data[offset + 5:offset + 7])[0]
        
        # Timestamp is variable - typically 7 bytes for CUC time
        time_stamp = data[offset + 7:offset + 14] if len(data) >= offset + 14 else b''
        
        return cls(
            version=version,
            sc_time_ref=sc_time_ref,
            service_type=service_type,
            service_subtype=service_subtype,
            message_type_counter=message_type_counter,
            destination_id=destination_id,
            time_stamp=time_stamp
        )


@dataclass
class SDLPHeader:
    """SDLP Transfer Frame Header"""
    version: int = 0
    spacecraft_id: int = 0
    virtual_channel_id: int = 0
    frame_length: int = 0
    frame_sequence: int = 0
    
    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['SDLPHeader']:
        """Parse SDLP Transfer Frame header"""
        if len(data) < 6:
            return None
        
        return cls(
            version=(data[0] >> 6) & 0x03,
            spacecraft_id=((data[0] & 0x3F) << 8) | data[1],
            virtual_channel_id=data[2] & 0x3F,
            frame_length=struct.unpack('>H', data[3:5])[0],
            frame_sequence=data[5]
        )


@dataclass
class DecodedTelemetry:
    """Decoded telemetry packet with all parsed information"""
    timestamp: float
    raw_data: bytes
    ccsds_header: Optional[CCSDSHeader] = None
    pus_header: Optional[PUSHeader] = None
    sdlp_header: Optional[SDLPHeader] = None
    application_data: bytes = field(default_factory=bytes)
    decoded_parameters: Dict[str, Any] = field(default_factory=dict)
    decode_errors: List[str] = field(default_factory=list)
    
    @property
    def apid(self) -> int:
        return self.ccsds_header.apid if self.ccsds_header else 0
    
    @property
    def service_type(self) -> int:
        return self.pus_header.service_type if self.pus_header else 0
    
    @property
    def service_subtype(self) -> int:
        return self.pus_header.service_subtype if self.pus_header else 0
    
    @property
    def packet_id(self) -> str:
        """Generate a packet identifier based on APID/Service/Subservice"""
        if self.pus_header:
            return f"TM_{self.service_type:03d}_{self.service_subtype:03d}_APID{self.apid}"
        return f"TM_APID{self.apid}"


# Known APID definitions from the TmTcHandbook
KNOWN_APIDS = {
    0: "SCSW_TIME",      # Time service
    256: "SCSW_TC",      # Telecommand service
}

# PUS Service Type names
PUS_SERVICES = {
    1: "TC Verification",
    3: "Housekeeping",
    5: "Event Reporting",
    6: "Memory Management",
    9: "Time Management",
    11: "Time-based Scheduling",
    12: "On-board Monitoring",
    14: "Packet Forwarding Control",
    15: "On-board Storage",
    17: "Test",
    19: "Event-Action",
    20: "Parameter Management",
    21: "Request Sequencing",
    178: "CAN Bus Reporting",
}


class CCSDSPacketDecoder:
    """Decodes CCSDS/PUS packets into structured telemetry data"""
    
    def __init__(self):
        self.stats = {
            "packets_received": 0,
            "packets_decoded": 0,
            "decode_errors": 0,
            "apid_counts": {},
            "service_counts": {},
        }
    
    def decode_packet(self, data: bytes, try_sdlp: bool = True) -> DecodedTelemetry:
        """
        Decode a CCSDS packet from raw bytes.
        
        Args:
            data: Raw packet bytes
            try_sdlp: Whether to try SDLP transfer frame decoding first
            
        Returns:
            DecodedTelemetry object with parsed data
        """
        result = DecodedTelemetry(
            timestamp=time.time(),
            raw_data=data
        )
        
        self.stats["packets_received"] += 1
        
        # Try SDLP transfer frame first if enabled
        ccsds_data = data
        if try_sdlp and len(data) >= 12:
            sdlp_header = SDLPHeader.from_bytes(data)
            if sdlp_header and sdlp_header.version in [0, 1]:
                result.sdlp_header = sdlp_header
                ccsds_data = data[6:]  # Skip SDLP header to get CCSDS packet
        
        # Parse CCSDS primary header
        ccsds_header = CCSDSHeader.from_bytes(ccsds_data)
        if not ccsds_header:
            result.decode_errors.append("Failed to parse CCSDS header")
            self.stats["decode_errors"] += 1
            return result
        
        result.ccsds_header = ccsds_header
        
        # Track APID statistics
        apid_key = f"APID_{ccsds_header.apid}"
        self.stats["apid_counts"][apid_key] = self.stats["apid_counts"].get(apid_key, 0) + 1
        
        # Parse PUS secondary header if present
        if ccsds_header.has_secondary_header and ccsds_header.is_telemetry:
            pus_header = PUSHeader.from_bytes(ccsds_data)
            if pus_header:
                result.pus_header = pus_header
                
                # Track service statistics
                service_key = f"SVC_{pus_header.service_type}_{pus_header.service_subtype}"
                self.stats["service_counts"][service_key] = self.stats["service_counts"].get(service_key, 0) + 1
                
                # Extract application data (after PUS header)
                app_data_start = 6 + 14  # CCSDS header + PUS header with timestamp
                if len(ccsds_data) > app_data_start:
                    result.application_data = ccsds_data[app_data_start:]
                    
                    # Decode application data based on service type
                    result.decoded_parameters = self._decode_application_data(
                        pus_header.service_type,
                        pus_header.service_subtype,
                        result.application_data,
                        ccsds_header.apid
                    )
            else:
                result.decode_errors.append("Failed to parse PUS header")
        else:
            # No PUS header - extract raw application data
            if len(ccsds_data) > 6:
                result.application_data = ccsds_data[6:]
        
        self.stats["packets_decoded"] += 1
        return result
    
    def _decode_application_data(self, service_type: int, service_subtype: int, 
                                  data: bytes, apid: int) -> Dict[str, Any]:
        """Decode application data based on service type/subtype"""
        params = {}
        
        try:
            # Housekeeping service (3,25)
            if service_type == 3 and service_subtype == 25:
                params = self._decode_housekeeping(data, apid)
            
            # Event reporting service (5,x)
            elif service_type == 5:
                params = self._decode_event_report(data, service_subtype)
            
            # Time management service (9,2)
            elif service_type == 9 and service_subtype == 2:
                params = self._decode_time_report(data)
            
            # TC Verification service (1,x)
            elif service_type == 1:
                params = self._decode_tc_verification(data, service_subtype)
            
            # Generic decode for unknown services
            else:
                params = self._decode_generic(data)
                
        except Exception as e:
            params["decode_error"] = str(e)
            logger.debug(f"Error decoding service {service_type},{service_subtype}: {e}")
        
        return params
    
    def _decode_housekeeping(self, data: bytes, apid: int) -> Dict[str, Any]:
        """Decode housekeeping telemetry (Service 3, Subtype 25)"""
        params = {}
        
        if len(data) < 2:
            return params
        
        # SID is typically the first 2 bytes
        sid = struct.unpack('>H', data[:2])[0]
        params["sid"] = sid
        
        # Decode based on known SIDs
        remaining = data[2:]
        
        # Generic parameter extraction - try to extract common types
        if len(remaining) >= 4:
            # Try extracting as various formats
            offset = 0
            param_idx = 0
            
            while offset + 4 <= len(remaining):
                try:
                    # Extract as 32-bit float
                    float_val = struct.unpack('>f', remaining[offset:offset+4])[0]
                    # Sanity check the float
                    if -1e10 < float_val < 1e10 and float_val == float_val:  # NaN check
                        params[f"param_{param_idx}_float"] = float_val
                    
                    # Extract as 32-bit unsigned int
                    uint_val = struct.unpack('>I', remaining[offset:offset+4])[0]
                    params[f"param_{param_idx}_uint"] = uint_val
                    
                    # Extract as 32-bit signed int
                    int_val = struct.unpack('>i', remaining[offset:offset+4])[0]
                    params[f"param_{param_idx}_int"] = int_val
                    
                except Exception:
                    pass
                
                offset += 4
                param_idx += 1
        
        params["raw_hex"] = remaining.hex().upper()
        return params
    
    def _decode_event_report(self, data: bytes, subtype: int) -> Dict[str, Any]:
        """Decode event reports (Service 5)"""
        params = {"subtype": subtype}
        
        if subtype == 1:
            params["event_type"] = "Info"
        elif subtype == 2:
            params["event_type"] = "Low Severity"
        elif subtype == 3:
            params["event_type"] = "Medium Severity"
        elif subtype == 4:
            params["event_type"] = "High Severity"
        
        if len(data) >= 4:
            params["event_id"] = struct.unpack('>I', data[:4])[0]
        
        if len(data) > 4:
            params["event_data"] = data[4:].hex().upper()
        
        return params
    
    def _decode_time_report(self, data: bytes) -> Dict[str, Any]:
        """Decode time report (Service 9, Subtype 2)"""
        params = {}
        
        if len(data) >= 7:
            # CUC time format (typically)
            # P-field + T-field
            params["time_raw"] = data[:7].hex().upper()
            
            # Try to extract coarse/fine time
            if len(data) >= 4:
                coarse = struct.unpack('>I', data[:4])[0]
                params["coarse_time"] = coarse
            if len(data) >= 6:
                fine = struct.unpack('>H', data[4:6])[0]
                params["fine_time"] = fine
        
        return params
    
    def _decode_tc_verification(self, data: bytes, subtype: int) -> Dict[str, Any]:
        """Decode TC verification reports (Service 1)"""
        params = {"subtype": subtype}
        
        if subtype == 1:
            params["verification_type"] = "Acceptance Success"
        elif subtype == 2:
            params["verification_type"] = "Acceptance Failure"
        elif subtype == 7:
            params["verification_type"] = "Execution Complete Success"
        elif subtype == 8:
            params["verification_type"] = "Execution Complete Failure"
        
        if len(data) >= 4:
            # TC Packet ID
            tc_packet_id = struct.unpack('>H', data[:2])[0]
            tc_seq_control = struct.unpack('>H', data[2:4])[0]
            params["tc_packet_id"] = tc_packet_id
            params["tc_seq_control"] = tc_seq_control
        
        return params
    
    def _decode_generic(self, data: bytes) -> Dict[str, Any]:
        """Generic decode for unknown packet types"""
        params = {
            "data_length": len(data),
            "raw_hex": data.hex().upper() if len(data) <= 64 else data[:64].hex().upper() + "..."
        }
        return params
    
    def get_stats(self) -> Dict[str, Any]:
        """Get decoding statistics"""
        return self.stats.copy()


class CCSDSUDPReceiver:
    """
    UDP receiver for CCSDS telemetry packets.
    
    Listens on a specified UDP port and decodes incoming CCSDS packets.
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 5003, 
                 buffer_size: int = 4096, max_history: int = 1000):
        """
        Initialize the CCSDS UDP receiver.
        
        Args:
            host: Host address to bind to
            port: UDP port to listen on
            buffer_size: Socket receive buffer size
            max_history: Maximum number of packets to keep in history
        """
        self.host = host
        self.port = port
        self.buffer_size = buffer_size
        self.max_history = max_history
        
        self.decoder = CCSDSPacketDecoder()
        self.packet_history: deque = deque(maxlen=max_history)
        
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Callbacks
        self._packet_callbacks: List[Callable[[DecodedTelemetry], None]] = []
        
        # Latest telemetry state
        self.latest_telemetry: Dict[str, Any] = {}
        self.last_packet_time: float = 0
        self.packets_per_second: float = 0
        self._packet_times: deque = deque(maxlen=100)
        
        logger.info(f"CCSDS UDP Receiver initialized (port: {port})")
    
    def start(self) -> bool:
        """Start the UDP receiver thread."""
        if self._running:
            logger.warning("Receiver already running")
            return False
        
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.settimeout(1.0)  # 1 second timeout for clean shutdown
            self._socket.bind((self.host, self.port))
            
            self._running = True
            self._thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._thread.start()
            
            logger.info(f"CCSDS UDP Receiver started on {self.host}:{self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start CCSDS receiver: {e}")
            self._running = False
            return False
    
    def stop(self):
        """Stop the UDP receiver."""
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        
        logger.info("CCSDS UDP Receiver stopped")
    
    def _receive_loop(self):
        """Main receive loop running in background thread."""
        while self._running:
            try:
                data, addr = self._socket.recvfrom(self.buffer_size)
                
                if data:
                    self._process_packet(data, addr)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Receive error: {e}")
    
    def _process_packet(self, data: bytes, addr: tuple):
        """Process a received packet."""
        # Decode the packet
        decoded = self.decoder.decode_packet(data)
        
        # Update statistics
        now = time.time()
        self._packet_times.append(now)
        self.last_packet_time = now
        
        # Calculate packets per second
        if len(self._packet_times) >= 2:
            time_span = self._packet_times[-1] - self._packet_times[0]
            if time_span > 0:
                self.packets_per_second = len(self._packet_times) / time_span
        
        # Store in history
        with self._lock:
            self.packet_history.append(decoded)
            
            # Update latest telemetry state
            self._update_telemetry_state(decoded)
        
        # Notify callbacks
        for callback in self._packet_callbacks:
            try:
                callback(decoded)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def _update_telemetry_state(self, decoded: DecodedTelemetry):
        """Update the latest telemetry state from decoded packet."""
        self.latest_telemetry["last_packet_id"] = decoded.packet_id
        self.latest_telemetry["last_apid"] = decoded.apid
        self.latest_telemetry["last_timestamp"] = decoded.timestamp
        
        if decoded.pus_header:
            self.latest_telemetry["last_service"] = decoded.service_type
            self.latest_telemetry["last_subservice"] = decoded.service_subtype
        
        if decoded.decoded_parameters:
            # Merge decoded parameters into latest state
            for key, value in decoded.decoded_parameters.items():
                self.latest_telemetry[f"param_{key}"] = value
    
    def register_callback(self, callback: Callable[[DecodedTelemetry], None]):
        """Register a callback for received packets."""
        self._packet_callbacks.append(callback)
    
    def unregister_callback(self, callback: Callable[[DecodedTelemetry], None]):
        """Unregister a callback."""
        if callback in self._packet_callbacks:
            self._packet_callbacks.remove(callback)
    
    def get_recent_packets(self, count: int = 10) -> List[DecodedTelemetry]:
        """Get recent decoded packets."""
        with self._lock:
            return list(self.packet_history)[-count:]
    
    def get_status(self) -> Dict[str, Any]:
        """Get receiver status."""
        return {
            "running": self._running,
            "host": self.host,
            "port": self.port,
            "last_packet_time": self.last_packet_time,
            "packets_per_second": round(self.packets_per_second, 2),
            "packets_in_history": len(self.packet_history),
            "decoder_stats": self.decoder.get_stats(),
            "latest_telemetry": self.latest_telemetry.copy()
        }
    
    def get_telemetry_for_socc(self) -> Dict[str, Any]:
        """
        Get telemetry formatted for SOCC display.
        
        Returns:
            Dictionary with telemetry values suitable for the SOCC UI
        """
        status = self.get_status()
        
        result = {
            "ccsds_active": self._running,
            "ccsds_rate": status["packets_per_second"],
            "last_ccsds_time": self.last_packet_time,
            "ccsds_packets_received": status["decoder_stats"]["packets_received"],
            "ccsds_decode_errors": status["decoder_stats"]["decode_errors"],
        }
        
        # Add latest decoded telemetry
        if self.latest_telemetry:
            result["ccsds_latest"] = self.latest_telemetry.copy()
        
        return result


# Global receiver instance
_receiver: Optional[CCSDSUDPReceiver] = None


def get_ccsds_receiver() -> CCSDSUDPReceiver:
    """Get or create global CCSDS receiver."""
    global _receiver
    if _receiver is None:
        _receiver = CCSDSUDPReceiver()
    return _receiver


def create_ccsds_receiver(host: str = "0.0.0.0", port: int = 5003) -> CCSDSUDPReceiver:
    """Create a new CCSDS receiver with specified settings."""
    global _receiver
    if _receiver:
        _receiver.stop()
    _receiver = CCSDSUDPReceiver(host=host, port=port)
    return _receiver


# Demo/test code
if __name__ == "__main__":
    import sys
    
    def packet_callback(decoded: DecodedTelemetry):
        print(f"\n{'='*60}")
        print(f"📦 Received CCSDS Packet")
        print(f"⏰ Time: {datetime.fromtimestamp(decoded.timestamp).isoformat()}")
        print(f"📏 Length: {len(decoded.raw_data)} bytes")
        
        if decoded.ccsds_header:
            h = decoded.ccsds_header
            print(f"\n🎯 CCSDS Header:")
            print(f"   APID: {h.apid} (0x{h.apid:04X})")
            print(f"   Type: {'TM' if h.is_telemetry else 'TC'}")
            print(f"   Seq Count: {h.seq_count}")
            print(f"   Data Length: {h.data_length}")
        
        if decoded.pus_header:
            p = decoded.pus_header
            service_name = PUS_SERVICES.get(p.service_type, "Unknown")
            print(f"\n🔧 PUS Header:")
            print(f"   Service: {p.service_type} ({service_name})")
            print(f"   Subtype: {p.service_subtype}")
            print(f"   Msg Counter: {p.message_type_counter}")
        
        if decoded.decoded_parameters:
            print(f"\n📊 Decoded Parameters:")
            for key, value in decoded.decoded_parameters.items():
                print(f"   {key}: {value}")
        
        if decoded.decode_errors:
            print(f"\n⚠️ Decode Errors:")
            for error in decoded.decode_errors:
                print(f"   - {error}")
    
    # Create and start receiver
    receiver = CCSDSUDPReceiver(port=5003)
    receiver.register_callback(packet_callback)
    
    print("🛰️  CCSDS UDP Telemetry Receiver")
    print(f"📡 Listening on UDP port {receiver.port}")
    print("Press Ctrl+C to stop\n")
    
    if receiver.start():
        try:
            while True:
                time.sleep(5)
                status = receiver.get_status()
                print(f"\n📈 Status: {status['packets_per_second']:.1f} pkt/s, "
                      f"Total: {status['decoder_stats']['packets_received']} packets")
        except KeyboardInterrupt:
            print("\n\n🛑 Stopping receiver...")
            receiver.stop()
            print("✅ Done")
    else:
        print("❌ Failed to start receiver")
        sys.exit(1)

