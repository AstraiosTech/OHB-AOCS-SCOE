"""
PUS (Packet Utilization Standard) Protocol Implementation
Based on ECSS-E-ST-70-41C for AOCS SCOE

Implements the PUS services as defined in section 6.2:
- Service 1: Request Verification
- Service 3: Housekeeping
- Service 8: Function Management
- Service 17: Connection Test
- Service 20: Parameter Management
"""

import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List, Dict, Any
import hashlib


class PUSServiceType(IntEnum):
    """PUS Service Types supported by AOCS SCOE"""
    REQUEST_VERIFICATION = 1
    HOUSEKEEPING = 3
    FUNCTION_MANAGEMENT = 8
    CONNECTION_TEST = 17
    PARAMETER_MANAGEMENT = 20


class PUSServiceSubtype(IntEnum):
    """PUS Service Subtypes"""
    # Service 1 - Request Verification (TM only)
    TM_ACCEPTANCE_SUCCESS = 1
    TM_ACCEPTANCE_FAILURE = 2
    TM_EXECUTION_SUCCESS = 7
    TM_EXECUTION_FAILURE = 8
    
    # Service 3 - Housekeeping
    TC_CREATE_HK_REPORT = 1
    TC_DELETE_HK_REPORT = 3
    TC_ENABLE_HK_REPORT = 5
    TC_DISABLE_HK_REPORT = 6
    TM_HK_REPORT = 25
    TC_ONE_SHOT_HK = 27
    TC_MODIFY_HK_INTERVAL = 31
    
    # Service 8 - Function Management
    TC_PERFORM_FUNCTION = 1
    
    # Service 17 - Connection Test
    TC_CONNECTION_TEST = 1
    TM_CONNECTION_REPORT = 2
    
    # Service 20 - Parameter Management
    TC_SET_PARAMETER = 3


class PacketType(IntEnum):
    """CCSDS Packet Types"""
    TM = 0  # Telemetry
    TC = 1  # Telecommand


@dataclass
class CCSDSHeader:
    """CCSDS Space Packet Primary Header (6 bytes)"""
    version: int = 0  # 3 bits
    packet_type: PacketType = PacketType.TM  # 1 bit
    secondary_header_flag: int = 1  # 1 bit
    apid: int = 0  # 11 bits
    sequence_flags: int = 3  # 2 bits (standalone packet)
    sequence_count: int = 0  # 14 bits
    data_length: int = 0  # 16 bits (data field length - 1)
    
    def pack(self) -> bytes:
        """Pack header to bytes"""
        word1 = ((self.version & 0x7) << 13) | \
                ((self.packet_type & 0x1) << 12) | \
                ((self.secondary_header_flag & 0x1) << 11) | \
                (self.apid & 0x7FF)
        word2 = ((self.sequence_flags & 0x3) << 14) | \
                (self.sequence_count & 0x3FFF)
        return struct.pack('>HHH', word1, word2, self.data_length)
    
    @classmethod
    def unpack(cls, data: bytes) -> 'CCSDSHeader':
        """Unpack header from bytes"""
        word1, word2, data_length = struct.unpack('>HHH', data[:6])
        return cls(
            version=(word1 >> 13) & 0x7,
            packet_type=PacketType((word1 >> 12) & 0x1),
            secondary_header_flag=(word1 >> 11) & 0x1,
            apid=word1 & 0x7FF,
            sequence_flags=(word2 >> 14) & 0x3,
            sequence_count=word2 & 0x3FFF,
            data_length=data_length
        )


@dataclass
class PUSSecondaryHeader:
    """PUS Packet Secondary Header"""
    pus_version: int = 2  # 4 bits (PUS-C)
    ack_flags: int = 0  # 4 bits
    service_type: int = 0  # 8 bits
    service_subtype: int = 0  # 8 bits
    source_id: int = 0  # 16 bits (destination for TC)
    
    # For TM packets
    time_stamp: int = 0  # 32 bits (mission time)
    
    def pack(self, is_tm: bool = True) -> bytes:
        """Pack secondary header to bytes"""
        byte1 = ((self.pus_version & 0xF) << 4) | (self.ack_flags & 0xF)
        if is_tm:
            return struct.pack('>BBBHI', 
                byte1, 
                self.service_type, 
                self.service_subtype,
                self.source_id,
                self.time_stamp)
        else:
            return struct.pack('>BBBH', 
                byte1, 
                self.service_type, 
                self.service_subtype,
                self.source_id)
    
    @classmethod
    def unpack(cls, data: bytes, is_tm: bool = True) -> 'PUSSecondaryHeader':
        """Unpack secondary header from bytes"""
        byte1 = data[0]
        if is_tm:
            _, service_type, service_subtype, source_id, time_stamp = \
                struct.unpack('>BBBHI', data[:9])
            return cls(
                pus_version=(byte1 >> 4) & 0xF,
                ack_flags=byte1 & 0xF,
                service_type=service_type,
                service_subtype=service_subtype,
                source_id=source_id,
                time_stamp=time_stamp
            )
        else:
            _, service_type, service_subtype, source_id = \
                struct.unpack('>BBBH', data[:5])
            return cls(
                pus_version=(byte1 >> 4) & 0xF,
                ack_flags=byte1 & 0xF,
                service_type=service_type,
                service_subtype=service_subtype,
                source_id=source_id
            )


@dataclass
class PUSPacket:
    """Complete PUS Packet"""
    ccsds_header: CCSDSHeader
    pus_header: PUSSecondaryHeader
    data: bytes = b''
    crc: int = 0
    
    def pack(self) -> bytes:
        """Pack complete packet to bytes"""
        is_tm = self.ccsds_header.packet_type == PacketType.TM
        pus_bytes = self.pus_header.pack(is_tm)
        
        # Calculate data length (secondary header + data + CRC - 1)
        pus_header_len = 9 if is_tm else 5
        self.ccsds_header.data_length = pus_header_len + len(self.data) + 2 - 1
        
        packet = self.ccsds_header.pack() + pus_bytes + self.data
        
        # Calculate CRC-16
        crc = self._calculate_crc(packet)
        return packet + struct.pack('>H', crc)
    
    @classmethod
    def unpack(cls, data: bytes) -> 'PUSPacket':
        """Unpack complete packet from bytes"""
        ccsds_header = CCSDSHeader.unpack(data[:6])
        is_tm = ccsds_header.packet_type == PacketType.TM
        
        pus_offset = 6
        pus_header_len = 9 if is_tm else 5
        pus_header = PUSSecondaryHeader.unpack(data[pus_offset:pus_offset + pus_header_len], is_tm)
        
        data_start = pus_offset + pus_header_len
        data_end = 6 + ccsds_header.data_length + 1 - 2  # Remove CRC
        packet_data = data[data_start:data_end]
        
        crc = struct.unpack('>H', data[data_end:data_end + 2])[0]
        
        return cls(
            ccsds_header=ccsds_header,
            pus_header=pus_header,
            data=packet_data,
            crc=crc
        )
    
    @staticmethod
    def _calculate_crc(data: bytes) -> int:
        """Calculate CRC-16-CCITT"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc


class PUSPacketFactory:
    """Factory for creating PUS packets"""
    
    def __init__(self, apid: int = 100, source_id: int = 1):
        self.apid = apid
        self.source_id = source_id
        self.sequence_counter = 0
    
    def _next_sequence(self) -> int:
        """Get next sequence counter"""
        seq = self.sequence_counter
        self.sequence_counter = (self.sequence_counter + 1) & 0x3FFF
        return seq
    
    def _mission_time(self) -> int:
        """Get current mission time (seconds since epoch)"""
        return int(time.time())
    
    def create_tm(self, service_type: int, service_subtype: int, 
                  data: bytes = b'') -> PUSPacket:
        """Create a telemetry packet"""
        ccsds = CCSDSHeader(
            packet_type=PacketType.TM,
            apid=self.apid,
            sequence_count=self._next_sequence()
        )
        pus = PUSSecondaryHeader(
            service_type=service_type,
            service_subtype=service_subtype,
            source_id=self.source_id,
            time_stamp=self._mission_time()
        )
        return PUSPacket(ccsds_header=ccsds, pus_header=pus, data=data)
    
    def create_tc(self, service_type: int, service_subtype: int,
                  data: bytes = b'', ack_flags: int = 0xF) -> PUSPacket:
        """Create a telecommand packet"""
        ccsds = CCSDSHeader(
            packet_type=PacketType.TC,
            apid=self.apid,
            sequence_count=self._next_sequence()
        )
        pus = PUSSecondaryHeader(
            service_type=service_type,
            service_subtype=service_subtype,
            source_id=self.source_id,
            ack_flags=ack_flags
        )
        return PUSPacket(ccsds_header=ccsds, pus_header=pus, data=data)
    
    # Service 1 - Request Verification
    def create_acceptance_success(self, tc_packet: PUSPacket) -> PUSPacket:
        """TM[1,1] - Acceptance success"""
        return self.create_tm(1, 1, struct.pack('>H', tc_packet.ccsds_header.sequence_count))
    
    def create_acceptance_failure(self, tc_packet: PUSPacket, error_code: int) -> PUSPacket:
        """TM[1,2] - Acceptance failure"""
        data = struct.pack('>HI', tc_packet.ccsds_header.sequence_count, error_code)
        return self.create_tm(1, 2, data)
    
    def create_execution_success(self, tc_packet: PUSPacket) -> PUSPacket:
        """TM[1,7] - Execution success"""
        return self.create_tm(1, 7, struct.pack('>H', tc_packet.ccsds_header.sequence_count))
    
    def create_execution_failure(self, tc_packet: PUSPacket, error_code: int) -> PUSPacket:
        """TM[1,8] - Execution failure"""
        data = struct.pack('>HI', tc_packet.ccsds_header.sequence_count, error_code)
        return self.create_tm(1, 8, data)
    
    # Service 3 - Housekeeping
    def create_hk_report(self, structure_id: int, params: Dict[str, float]) -> PUSPacket:
        """TM[3,25] - Housekeeping parameter report"""
        # Pack structure ID + parameter values
        data = struct.pack('>H', structure_id)
        for value in params.values():
            data += struct.pack('>f', value)
        return self.create_tm(3, 25, data)
    
    # Service 17 - Connection Test
    def create_connection_report(self) -> PUSPacket:
        """TM[17,2] - Connection report"""
        return self.create_tm(17, 2)


# EDEN Protocol wrapper (simplified)
class EDENProtocol:
    """EDEN (EGSE-to-DTE/Equipment Network) Protocol wrapper"""
    
    SYNC_MARKER = b'\xEB\x90'
    
    @staticmethod
    def wrap_packet(pus_packet: PUSPacket) -> bytes:
        """Wrap a PUS packet in EDEN frame"""
        pus_bytes = pus_packet.pack()
        length = len(pus_bytes)
        return EDENProtocol.SYNC_MARKER + struct.pack('>H', length) + pus_bytes
    
    @staticmethod
    def unwrap_packet(data: bytes) -> Optional[PUSPacket]:
        """Unwrap EDEN frame to get PUS packet"""
        if not data.startswith(EDENProtocol.SYNC_MARKER):
            return None
        length = struct.unpack('>H', data[2:4])[0]
        pus_data = data[4:4 + length]
        return PUSPacket.unpack(pus_data)
    
    @staticmethod
    def find_packet(buffer: bytes) -> tuple[Optional[bytes], bytes]:
        """Find complete packet in buffer, return (packet, remaining)"""
        idx = buffer.find(EDENProtocol.SYNC_MARKER)
        if idx == -1:
            return None, buffer
        
        if len(buffer) < idx + 4:
            return None, buffer[idx:]
        
        length = struct.unpack('>H', buffer[idx + 2:idx + 4])[0]
        total_len = 4 + length
        
        if len(buffer) < idx + total_len:
            return None, buffer[idx:]
        
        packet = buffer[idx:idx + total_len]
        remaining = buffer[idx + total_len:]
        return packet, remaining


