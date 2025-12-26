"""
Mock AOCS Server
Simulates the OHB AOCS system for testing the SCOE Controller

Receives telecommands via EDEN/TCP and generates PUS telemetry responses.
Based on Section 6.2 of the Technical Proposal.
"""

import asyncio
import struct
import time
import logging
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass, field

from pus_protocol import (
    PUSPacket, PUSPacketFactory, EDENProtocol,
    PUSServiceType, PUSServiceSubtype, PacketType
)
from aocs_simulation import AOCSSimulation, RWCommandCode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class HKReportStructure:
    """Housekeeping report structure definition"""
    structure_id: int
    enabled: bool = False
    interval: float = 1.0  # seconds
    parameters: List[str] = field(default_factory=list)
    last_report_time: float = 0.0


class MockAOCSServer:
    """
    Mock AOCS Server implementing PUS services over EDEN/TCP
    
    Implements:
    - Service 1: Request Verification
    - Service 3: Housekeeping
    - Service 8: Function Management
    - Service 17: Connection Test
    - Service 20: Parameter Management
    """
    
    def __init__(self, host: str = '0.0.0.0', port: int = 10025):
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: List[asyncio.StreamWriter] = []
        
        # PUS packet factory
        self.packet_factory = PUSPacketFactory(apid=100, source_id=1)
        
        # AOCS Simulation
        self.simulation = AOCSSimulation()
        
        # Housekeeping structures
        self.hk_structures: Dict[int, HKReportStructure] = {}
        self._create_default_hk_structures()
        
        # Staged parameters (Service 20)
        self.staged_parameters: Dict[str, float] = {}
        
        # Running state
        self.running = False
        self._sim_task: Optional[asyncio.Task] = None
        self._hk_task: Optional[asyncio.Task] = None
    
    def _create_default_hk_structures(self):
        """Create default housekeeping report structures"""
        # Structure 1: Attitude telemetry
        self.hk_structures[1] = HKReportStructure(
            structure_id=1,
            enabled=True,
            interval=1.0,
            parameters=[
                'att_q_w', 'att_q_x', 'att_q_y', 'att_q_z',
                'rate_x', 'rate_y', 'rate_z'
            ]
        )
        
        # Structure 2: Reaction wheel telemetry
        self.hk_structures[2] = HKReportStructure(
            structure_id=2,
            enabled=True,
            interval=0.5,
            parameters=[
                'rw0_speed', 'rw1_speed', 'rw2_speed', 'rw3_speed',
                'rw0_temperature', 'rw1_temperature', 'rw2_temperature', 'rw3_temperature',
                'rw0_cmd_torque', 'rw1_cmd_torque', 'rw2_cmd_torque', 'rw3_cmd_torque',
            ]
        )
        
        # Structure 3: Sensor telemetry
        self.hk_structures[3] = HKReportStructure(
            structure_id=3,
            enabled=True,
            interval=1.0,
            parameters=[
                'mag_x', 'mag_y', 'mag_z',
                'gyro_x', 'gyro_y', 'gyro_z',
                'ss0_detected', 'ss0_azimuth', 'ss0_elevation',
            ]
        )
        
        # Structure 4: Thruster telemetry
        self.hk_structures[4] = HKReportStructure(
            structure_id=4,
            enabled=True,
            interval=1.0,
            parameters=[
                'thr0_firing', 'thr1_firing', 'thr2_firing', 'thr3_firing',
                'thr0_temperature', 'thr1_temperature', 'thr2_temperature', 'thr3_temperature',
            ]
        )
        
        # Structure 5: SADA telemetry
        self.hk_structures[5] = HKReportStructure(
            structure_id=5,
            enabled=True,
            interval=2.0,
            parameters=[
                'sada0_angle', 'sada1_angle',
                'sada0_deployed', 'sada1_deployed',
            ]
        )
        
        # Structure 6: Simulation status
        self.hk_structures[6] = HKReportStructure(
            structure_id=6,
            enabled=True,
            interval=1.0,
            parameters=[
                'sim_time', 'sim_running',
                'pos_x', 'pos_y', 'pos_z',
                'in_eclipse',
            ]
        )
    
    async def start(self):
        """Start the mock AOCS server"""
        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port
        )
        self.running = True
        
        # Start simulation loop
        self._sim_task = asyncio.create_task(self._simulation_loop())
        
        # Start housekeeping loop
        self._hk_task = asyncio.create_task(self._housekeeping_loop())
        
        addr = self.server.sockets[0].getsockname()
        logger.info(f"Mock AOCS Server started on {addr}")
        
        async with self.server:
            await self.server.serve_forever()
    
    async def stop(self):
        """Stop the server"""
        self.running = False
        if self._sim_task:
            self._sim_task.cancel()
        if self._hk_task:
            self._hk_task.cancel()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("Mock AOCS Server stopped")
    
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection"""
        addr = writer.get_extra_info('peername')
        logger.info(f"Client connected from {addr}")
        self.clients.append(writer)
        
        buffer = b''
        try:
            while self.running:
                data = await reader.read(4096)
                if not data:
                    break
                
                buffer += data
                
                # Process complete packets
                while True:
                    packet_data, buffer = EDENProtocol.find_packet(buffer)
                    if packet_data is None:
                        break
                    
                    try:
                        pus_packet = EDENProtocol.unwrap_packet(packet_data)
                        if pus_packet:
                            await self._process_telecommand(pus_packet, writer)
                    except Exception as e:
                        logger.error(f"Error processing packet: {e}")
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            self.clients.remove(writer)
            writer.close()
            await writer.wait_closed()
            logger.info(f"Client disconnected from {addr}")
    
    async def _process_telecommand(self, tc: PUSPacket, writer: asyncio.StreamWriter):
        """Process a telecommand packet"""
        service = tc.pus_header.service_type
        subtype = tc.pus_header.service_subtype
        
        logger.info(f"Received TC[{service},{subtype}]")
        
        # Send acceptance success
        if tc.pus_header.ack_flags & 0x1:
            tm = self.packet_factory.create_acceptance_success(tc)
            await self._send_telemetry(tm, writer)
        
        success = True
        error_code = 0
        
        try:
            if service == PUSServiceType.HOUSEKEEPING:
                success = await self._handle_housekeeping(subtype, tc.data, writer)
            elif service == PUSServiceType.FUNCTION_MANAGEMENT:
                success = await self._handle_function_management(tc.data, writer)
            elif service == PUSServiceType.CONNECTION_TEST:
                success = await self._handle_connection_test(writer)
            elif service == PUSServiceType.PARAMETER_MANAGEMENT:
                success = await self._handle_parameter_management(subtype, tc.data)
            else:
                logger.warning(f"Unsupported service type: {service}")
                success = False
                error_code = 1
        
        except Exception as e:
            logger.error(f"Error handling TC: {e}")
            success = False
            error_code = 2
        
        # Send execution result
        if tc.pus_header.ack_flags & 0x8:
            if success:
                tm = self.packet_factory.create_execution_success(tc)
            else:
                tm = self.packet_factory.create_execution_failure(tc, error_code)
            await self._send_telemetry(tm, writer)
    
    async def _handle_housekeeping(self, subtype: int, data: bytes, 
                                   writer: asyncio.StreamWriter) -> bool:
        """Handle Service 3 - Housekeeping"""
        if subtype == PUSServiceSubtype.TC_CREATE_HK_REPORT:
            # Create new HK structure
            if len(data) >= 2:
                struct_id = struct.unpack('>H', data[:2])[0]
                # Parse parameter list from remaining data
                params = []
                # Simplified: use predefined structures
                self.hk_structures[struct_id] = HKReportStructure(
                    structure_id=struct_id,
                    enabled=False,
                    parameters=params
                )
                logger.info(f"Created HK structure {struct_id}")
            return True
        
        elif subtype == PUSServiceSubtype.TC_DELETE_HK_REPORT:
            if len(data) >= 2:
                struct_id = struct.unpack('>H', data[:2])[0]
                if struct_id in self.hk_structures:
                    del self.hk_structures[struct_id]
                    logger.info(f"Deleted HK structure {struct_id}")
            return True
        
        elif subtype == PUSServiceSubtype.TC_ENABLE_HK_REPORT:
            if len(data) >= 2:
                struct_id = struct.unpack('>H', data[:2])[0]
                if struct_id in self.hk_structures:
                    self.hk_structures[struct_id].enabled = True
                    logger.info(f"Enabled HK structure {struct_id}")
            return True
        
        elif subtype == PUSServiceSubtype.TC_DISABLE_HK_REPORT:
            if len(data) >= 2:
                struct_id = struct.unpack('>H', data[:2])[0]
                if struct_id in self.hk_structures:
                    self.hk_structures[struct_id].enabled = False
                    logger.info(f"Disabled HK structure {struct_id}")
            return True
        
        elif subtype == PUSServiceSubtype.TC_ONE_SHOT_HK:
            if len(data) >= 2:
                struct_id = struct.unpack('>H', data[:2])[0]
                await self._send_hk_report(struct_id, writer)
            return True
        
        elif subtype == PUSServiceSubtype.TC_MODIFY_HK_INTERVAL:
            if len(data) >= 6:
                struct_id = struct.unpack('>H', data[:2])[0]
                interval = struct.unpack('>f', data[2:6])[0]
                if struct_id in self.hk_structures:
                    self.hk_structures[struct_id].interval = interval
                    logger.info(f"Modified HK structure {struct_id} interval to {interval}s")
            return True
        
        return False
    
    async def _handle_function_management(self, data: bytes, 
                                          writer: asyncio.StreamWriter) -> bool:
        """Handle Service 8 - Function Management"""
        if len(data) < 1:
            return False
        
        function_id = data[0]
        
        if function_id == 1:  # Start simulation
            self.simulation.start()
            logger.info("Simulation started")
            return True
        
        elif function_id == 2:  # Stop simulation
            self.simulation.stop()
            logger.info("Simulation stopped")
            return True
        
        elif function_id == 3:  # Reset simulation
            self.simulation.reset()
            logger.info("Simulation reset")
            return True
        
        elif function_id == 4:  # Apply staged parameters
            self._apply_staged_parameters()
            logger.info("Staged parameters applied")
            return True
        
        elif function_id == 5:  # Self-test
            logger.info("Self-test started")
            # Simulate self-test
            await asyncio.sleep(1)
            logger.info("Self-test completed")
            return True
        
        elif function_id >= 0x10 and function_id <= 0x1F:  # Reaction wheel commands
            # Function ID = 0x10 + wheel_id
            rw_id = function_id - 0x10
            if rw_id < len(self.simulation.reaction_wheels):
                # Command code is in data[1], torque value in data[2:6]
                cmd_code = data[1] if len(data) > 1 else 0
                cmd_data = data[2:] if len(data) > 2 else b''
                logger.info(f"RW{rw_id} command: code={cmd_code}, data={cmd_data.hex()}")
                return self.simulation.reaction_wheels[rw_id].process_command(cmd_code, cmd_data)
        
        elif function_id >= 0x20 and function_id <= 0x2F:  # Thruster commands
            thr_id = function_id - 0x20
            if thr_id < len(self.simulation.thrusters):
                fire = data[1] == 1 if len(data) > 1 else False
                self.simulation.thrusters[thr_id].firing = fire
                logger.info(f"Thruster {thr_id} firing: {fire}")
                return True
        
        elif function_id >= 0x30 and function_id <= 0x3F:  # Torque rod commands
            mtr_id = function_id - 0x30
            if mtr_id < len(self.simulation.torque_rods) and len(data) >= 5:
                dipole = struct.unpack('>f', data[1:5])[0]
                self.simulation.torque_rods[mtr_id].commanded_dipole = dipole
                logger.info(f"Torque rod {mtr_id} dipole: {dipole}")
                return True
        
        elif function_id >= 0x40 and function_id <= 0x4F:  # SADA commands
            sada_id = function_id - 0x40
            if sada_id < len(self.simulation.sadas) and len(data) >= 5:
                angle = struct.unpack('>f', data[1:5])[0]
                self.simulation.sadas[sada_id].commanded_angle = angle
                logger.info(f"SADA {sada_id} angle: {angle}")
                return True
        
        return False
    
    async def _handle_connection_test(self, writer: asyncio.StreamWriter) -> bool:
        """Handle Service 17 - Connection Test"""
        tm = self.packet_factory.create_connection_report()
        await self._send_telemetry(tm, writer)
        logger.info("Connection test response sent")
        return True
    
    async def _handle_parameter_management(self, subtype: int, data: bytes) -> bool:
        """Handle Service 20 - Parameter Management"""
        if subtype == PUSServiceSubtype.TC_SET_PARAMETER:
            if len(data) >= 6:
                param_id = struct.unpack('>H', data[:2])[0]
                value = struct.unpack('>f', data[2:6])[0]
                self.staged_parameters[param_id] = value
                logger.info(f"Staged parameter {param_id} = {value}")
            return True
        return False
    
    def _apply_staged_parameters(self):
        """Apply staged parameters to simulation"""
        for param_id, value in self.staged_parameters.items():
            # Map parameter IDs to simulation parameters
            # This would be defined in the MIB
            if param_id == 100:  # Example: Initial quaternion W
                self.simulation.state.quaternion.w = value
            elif param_id == 101:
                self.simulation.state.quaternion.x = value
            # Add more parameter mappings as needed
        
        self.staged_parameters.clear()
    
    async def _send_telemetry(self, tm: PUSPacket, writer: Optional[asyncio.StreamWriter] = None):
        """Send telemetry packet"""
        eden_packet = EDENProtocol.wrap_packet(tm)
        
        if writer:
            writer.write(eden_packet)
            await writer.drain()
        else:
            # Broadcast to all clients
            for client in self.clients:
                try:
                    client.write(eden_packet)
                    await client.drain()
                except Exception as e:
                    logger.error(f"Error sending to client: {e}")
    
    async def _send_hk_report(self, struct_id: int, writer: Optional[asyncio.StreamWriter] = None):
        """Send a housekeeping report"""
        if struct_id not in self.hk_structures:
            return
        
        structure = self.hk_structures[struct_id]
        all_tm = self.simulation.get_all_telemetry()
        
        # Get parameter values
        params = {}
        for param_name in structure.parameters:
            if param_name in all_tm:
                params[param_name] = all_tm[param_name]
        
        tm = self.packet_factory.create_hk_report(struct_id, params)
        await self._send_telemetry(tm, writer)
    
    async def _simulation_loop(self):
        """Main simulation loop running at 80 Hz"""
        interval = self.simulation.dt
        
        while self.running:
            start = time.time()
            
            self.simulation.step()
            
            # Sleep for remaining time
            elapsed = time.time() - start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
    
    async def _housekeeping_loop(self):
        """Housekeeping report generation loop"""
        while self.running:
            current_time = time.time()
            
            for struct_id, structure in self.hk_structures.items():
                if not structure.enabled:
                    continue
                
                if current_time - structure.last_report_time >= structure.interval:
                    await self._send_hk_report(struct_id)
                    structure.last_report_time = current_time
            
            await asyncio.sleep(0.1)  # Check every 100ms


async def main():
    """Main entry point"""
    server = MockAOCSServer(host='0.0.0.0', port=10025)
    
    try:
        await server.start()
    except KeyboardInterrupt:
        await server.stop()


if __name__ == '__main__':
    asyncio.run(main())

