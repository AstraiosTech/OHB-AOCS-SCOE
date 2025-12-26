"""
SCOE Controller
Interfaces between Grafana and the AOCS via EDEN/PUS over TCP/IP

Based on Section 6.2 of the Technical Proposal:
- Receives telemetry from AOCS and stores in InfluxDB
- Provides REST API for sending telecommands
- Implements EDEN protocol for communication
"""

import asyncio
import struct
import time
import logging
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from datetime import datetime
import json

from aiohttp import web
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from pus_protocol import (
    PUSPacket, PUSPacketFactory, EDENProtocol,
    PUSServiceType, PUSServiceSubtype, PacketType
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class SCOEConfig:
    """SCOE Controller configuration"""
    # AOCS connection
    aocs_host: str = 'localhost'
    aocs_port: int = 10025
    
    # HTTP API
    api_host: str = '0.0.0.0'
    api_port: int = 8080
    
    # InfluxDB
    influxdb_url: str = 'http://localhost:8086'
    influxdb_token: str = 'my-super-secret-token'
    influxdb_org: str = 'aocs'
    influxdb_bucket: str = 'telemetry'
    
    # WebSocket for real-time updates
    ws_host: str = '0.0.0.0'
    ws_port: int = 8081


class SCOEController:
    """
    SCOE Controller implementing EDEN/PUS client and REST API
    """
    
    def __init__(self, config: SCOEConfig):
        self.config = config
        
        # Connection state
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        
        # PUS packet factory
        self.packet_factory = PUSPacketFactory(apid=200, source_id=2)
        
        # InfluxDB client
        self.influx_client: Optional[InfluxDBClient] = None
        self.write_api = None
        
        # Latest telemetry cache
        self.telemetry_cache: Dict[str, float] = {}
        self.last_update = time.time()
        
        # WebSocket clients
        self.ws_clients: List[web.WebSocketResponse] = []
        
        # Command response tracking
        self.pending_commands: Dict[int, asyncio.Future] = {}
        
        # Running state
        self.running = False
        self._recv_task: Optional[asyncio.Task] = None
        self._connect_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the SCOE Controller"""
        self.running = True
        
        # Initialize InfluxDB
        await self._init_influxdb()
        
        # Start connection manager
        self._connect_task = asyncio.create_task(self._connection_manager())
        
        # Start HTTP API
        app = self._create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.config.api_host, self.config.api_port)
        await site.start()
        
        logger.info(f"SCOE Controller API started on http://{self.config.api_host}:{self.config.api_port}")
        
        # Keep running
        while self.running:
            await asyncio.sleep(1)
    
    async def stop(self):
        """Stop the controller"""
        self.running = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._connect_task:
            self._connect_task.cancel()
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        if self.influx_client:
            self.influx_client.close()
        logger.info("SCOE Controller stopped")
    
    async def _init_influxdb(self):
        """Initialize InfluxDB connection"""
        try:
            self.influx_client = InfluxDBClient(
                url=self.config.influxdb_url,
                token=self.config.influxdb_token,
                org=self.config.influxdb_org
            )
            self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            logger.info("InfluxDB connection initialized")
        except Exception as e:
            logger.warning(f"Failed to connect to InfluxDB: {e}")
            self.influx_client = None
    
    async def _connection_manager(self):
        """Manage connection to AOCS server with auto-reconnect"""
        while self.running:
            if not self.connected:
                try:
                    await self._connect()
                except Exception as e:
                    logger.warning(f"Failed to connect to AOCS: {e}")
                    await asyncio.sleep(5)
                    continue
            
            await asyncio.sleep(1)
    
    async def _connect(self):
        """Connect to AOCS server"""
        logger.info(f"Connecting to AOCS at {self.config.aocs_host}:{self.config.aocs_port}")
        
        self.reader, self.writer = await asyncio.open_connection(
            self.config.aocs_host,
            self.config.aocs_port
        )
        
        self.connected = True
        logger.info("Connected to AOCS server")
        
        # Start receive task
        self._recv_task = asyncio.create_task(self._receive_loop())
        
        # Send connection test
        await self.send_connection_test()
    
    async def _receive_loop(self):
        """Receive and process telemetry from AOCS"""
        buffer = b''
        
        try:
            while self.connected and self.running:
                data = await self.reader.read(4096)
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
                            await self._process_telemetry(pus_packet)
                    except Exception as e:
                        logger.error(f"Error processing packet: {e}")
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receive error: {e}")
        finally:
            self.connected = False
            logger.info("Disconnected from AOCS server")
    
    async def _process_telemetry(self, tm: PUSPacket):
        """Process a telemetry packet"""
        service = tm.pus_header.service_type
        subtype = tm.pus_header.service_subtype
        
        if service == PUSServiceType.REQUEST_VERIFICATION:
            await self._handle_verification(tm)
        
        elif service == PUSServiceType.HOUSEKEEPING and subtype == PUSServiceSubtype.TM_HK_REPORT:
            await self._handle_hk_report(tm)
        
        elif service == PUSServiceType.CONNECTION_TEST and subtype == PUSServiceSubtype.TM_CONNECTION_REPORT:
            logger.info("Connection test successful")
    
    async def _handle_verification(self, tm: PUSPacket):
        """Handle verification telemetry"""
        subtype = tm.pus_header.service_subtype
        
        if len(tm.data) >= 2:
            seq_count = struct.unpack('>H', tm.data[:2])[0]
            
            if seq_count in self.pending_commands:
                future = self.pending_commands.pop(seq_count)
                
                if subtype in [1, 7]:  # Success
                    future.set_result(True)
                else:  # Failure
                    error_code = struct.unpack('>I', tm.data[2:6])[0] if len(tm.data) >= 6 else 0
                    future.set_result(False)
    
    async def _handle_hk_report(self, tm: PUSPacket):
        """Handle housekeeping report"""
        if len(tm.data) < 2:
            return
        
        struct_id = struct.unpack('>H', tm.data[:2])[0]
        
        # Parse parameter values (floats)
        values = []
        offset = 2
        while offset + 4 <= len(tm.data):
            value = struct.unpack('>f', tm.data[offset:offset + 4])[0]
            values.append(value)
            offset += 4
        
        # Get parameter names for this structure (from cached mapping)
        param_names = self._get_hk_param_names(struct_id)
        
        # Update telemetry cache
        timestamp = datetime.utcnow()
        for i, name in enumerate(param_names):
            if i < len(values):
                self.telemetry_cache[name] = values[i]
        
        self.last_update = time.time()
        
        # Write to InfluxDB
        await self._write_to_influxdb(struct_id, param_names, values, timestamp)
        
        # Notify WebSocket clients
        await self._notify_ws_clients()
    
    def _get_hk_param_names(self, struct_id: int) -> List[str]:
        """Get parameter names for HK structure"""
        # This should match the structure definitions in mock_aocs_server
        structures = {
            1: ['att_q_w', 'att_q_x', 'att_q_y', 'att_q_z', 'rate_x', 'rate_y', 'rate_z'],
            2: ['rw0_speed', 'rw1_speed', 'rw2_speed', 'rw3_speed',
                'rw0_temperature', 'rw1_temperature', 'rw2_temperature', 'rw3_temperature',
                'rw0_cmd_torque', 'rw1_cmd_torque', 'rw2_cmd_torque', 'rw3_cmd_torque'],
            3: ['mag_x', 'mag_y', 'mag_z', 'gyro_x', 'gyro_y', 'gyro_z',
                'ss0_detected', 'ss0_azimuth', 'ss0_elevation'],
            4: ['thr0_firing', 'thr1_firing', 'thr2_firing', 'thr3_firing',
                'thr0_temperature', 'thr1_temperature', 'thr2_temperature', 'thr3_temperature'],
            5: ['sada0_angle', 'sada1_angle', 'sada0_deployed', 'sada1_deployed'],
            6: ['sim_time', 'sim_running', 'pos_x', 'pos_y', 'pos_z', 'in_eclipse'],
        }
        return structures.get(struct_id, [])
    
    async def _write_to_influxdb(self, struct_id: int, param_names: List[str],
                                  values: List[float], timestamp: datetime):
        """Write telemetry to InfluxDB"""
        if not self.write_api:
            return
        
        try:
            points = []
            for i, name in enumerate(param_names):
                if i < len(values):
                    point = Point("telemetry") \
                        .tag("structure_id", str(struct_id)) \
                        .tag("parameter", name) \
                        .field("value", values[i]) \
                        .time(timestamp)
                    points.append(point)
            
            self.write_api.write(
                bucket=self.config.influxdb_bucket,
                org=self.config.influxdb_org,
                record=points
            )
        except Exception as e:
            logger.error(f"Failed to write to InfluxDB: {e}")
    
    async def _notify_ws_clients(self):
        """Notify WebSocket clients of new telemetry"""
        if not self.ws_clients:
            return
        
        message = json.dumps({
            'type': 'telemetry',
            'timestamp': time.time(),
            'data': self.telemetry_cache
        })
        
        for ws in self.ws_clients[:]:
            try:
                await ws.send_str(message)
            except Exception:
                self.ws_clients.remove(ws)
    
    async def send_telecommand(self, service: int, subtype: int, 
                               data: bytes = b'') -> bool:
        """Send a telecommand and wait for response"""
        if not self.connected:
            raise Exception("Not connected to AOCS")
        
        tc = self.packet_factory.create_tc(service, subtype, data)
        
        # Track command for response
        future = asyncio.get_event_loop().create_future()
        self.pending_commands[tc.ccsds_header.sequence_count] = future
        
        # Send command
        eden_packet = EDENProtocol.wrap_packet(tc)
        self.writer.write(eden_packet)
        await self.writer.drain()
        
        logger.info(f"Sent TC[{service},{subtype}]")
        
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except asyncio.TimeoutError:
            self.pending_commands.pop(tc.ccsds_header.sequence_count, None)
            return False
    
    async def send_connection_test(self) -> bool:
        """Send connection test command"""
        return await self.send_telecommand(17, 1)
    
    async def start_simulation(self) -> bool:
        """Start the AOCS simulation"""
        return await self.send_telecommand(8, 1, bytes([1]))
    
    async def stop_simulation(self) -> bool:
        """Stop the AOCS simulation"""
        return await self.send_telecommand(8, 1, bytes([2]))
    
    async def reset_simulation(self) -> bool:
        """Reset the AOCS simulation"""
        return await self.send_telecommand(8, 1, bytes([3]))
    
    async def set_rw_torque(self, wheel_id: int, torque: float) -> bool:
        """Set reaction wheel torque"""
        # Function ID: 0x10 + wheel_id, Command code: 0x04 (TORQUE_SPEED_CONTROL)
        data = bytes([0x10 + wheel_id, 0x04]) + struct.pack('>f', torque)
        return await self.send_telecommand(8, 1, data)
    
    async def set_thruster(self, thruster_id: int, firing: bool) -> bool:
        """Set thruster firing state"""
        data = bytes([0x20 + thruster_id, 1 if firing else 0])
        return await self.send_telecommand(8, 1, data)
    
    async def set_torque_rod(self, rod_id: int, dipole: float) -> bool:
        """Set torque rod dipole moment"""
        data = bytes([0x30 + rod_id]) + struct.pack('>f', dipole)
        return await self.send_telecommand(8, 1, data)
    
    async def set_sada_angle(self, sada_id: int, angle: float) -> bool:
        """Set SADA angle"""
        data = bytes([0x40 + sada_id]) + struct.pack('>f', angle)
        return await self.send_telecommand(8, 1, data)
    
    async def enable_hk_report(self, struct_id: int) -> bool:
        """Enable housekeeping report"""
        data = struct.pack('>H', struct_id)
        return await self.send_telecommand(3, 5, data)
    
    async def disable_hk_report(self, struct_id: int) -> bool:
        """Disable housekeeping report"""
        data = struct.pack('>H', struct_id)
        return await self.send_telecommand(3, 6, data)
    
    async def request_hk_report(self, struct_id: int) -> bool:
        """Request one-shot housekeeping report"""
        data = struct.pack('>H', struct_id)
        return await self.send_telecommand(3, 27, data)
    
    def _create_app(self) -> web.Application:
        """Create the HTTP API application"""
        import os
        app = web.Application()
        
        # CORS middleware
        app.middlewares.append(self._cors_middleware)
        
        # API Routes
        app.router.add_get('/api/status', self._handle_status)
        app.router.add_get('/api/telemetry', self._handle_get_telemetry)
        app.router.add_post('/api/command', self._handle_command)
        app.router.add_post('/api/simulation/start', self._handle_sim_start)
        app.router.add_post('/api/simulation/stop', self._handle_sim_stop)
        app.router.add_post('/api/simulation/reset', self._handle_sim_reset)
        app.router.add_post('/api/rw/{wheel_id}/torque', self._handle_rw_torque)
        app.router.add_post('/api/thruster/{thruster_id}/fire', self._handle_thruster)
        app.router.add_post('/api/torquerod/{rod_id}/dipole', self._handle_torquerod)
        app.router.add_post('/api/sada/{sada_id}/angle', self._handle_sada)
        app.router.add_get('/api/ws', self._handle_websocket)
        
        # Serve static files for control panel
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        if os.path.exists(static_path):
            app.router.add_static('/static/', static_path)
            app.router.add_get('/', self._handle_index)
            app.router.add_get('/constellation.html', self._handle_constellation)
            app.router.add_get('/{filename}.html', self._handle_html_file)
        
        return app
    
    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the control panel index page"""
        import os
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'index.html')
        if os.path.exists(static_path):
            with open(static_path, 'r') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        return web.Response(text='Control panel not found', status=404)
    
    async def _handle_constellation(self, request: web.Request) -> web.Response:
        """Serve the constellation view page"""
        import os
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'constellation.html')
        if os.path.exists(static_path):
            with open(static_path, 'r') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        return web.Response(text='Constellation page not found', status=404)
    
    async def _handle_html_file(self, request: web.Request) -> web.Response:
        """Serve any HTML file from static directory"""
        import os
        filename = request.match_info.get('filename', '')
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', f'{filename}.html')
        if os.path.exists(static_path):
            with open(static_path, 'r') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        return web.Response(text='Page not found', status=404)
    
    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """CORS middleware"""
        if request.method == 'OPTIONS':
            response = web.Response()
        else:
            response = await handler(request)
        
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    
    async def _handle_status(self, request: web.Request) -> web.Response:
        """Get SCOE status"""
        status = {
            'connected': self.connected,
            'last_update': self.last_update,
            'telemetry_count': len(self.telemetry_cache),
        }
        return web.json_response(status)
    
    async def _handle_get_telemetry(self, request: web.Request) -> web.Response:
        """Get current telemetry"""
        return web.json_response({
            'timestamp': self.last_update,
            'data': self.telemetry_cache
        })
    
    async def _handle_command(self, request: web.Request) -> web.Response:
        """Send raw PUS command"""
        try:
            body = await request.json()
            service = body.get('service', 17)
            subtype = body.get('subtype', 1)
            data_hex = body.get('data', '')
            data = bytes.fromhex(data_hex) if data_hex else b''
            
            result = await self.send_telecommand(service, subtype, data)
            return web.json_response({'success': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)
    
    async def _handle_sim_start(self, request: web.Request) -> web.Response:
        """Start simulation"""
        result = await self.start_simulation()
        return web.json_response({'success': result})
    
    async def _handle_sim_stop(self, request: web.Request) -> web.Response:
        """Stop simulation"""
        result = await self.stop_simulation()
        return web.json_response({'success': result})
    
    async def _handle_sim_reset(self, request: web.Request) -> web.Response:
        """Reset simulation"""
        result = await self.reset_simulation()
        return web.json_response({'success': result})
    
    async def _handle_rw_torque(self, request: web.Request) -> web.Response:
        """Set reaction wheel torque"""
        try:
            wheel_id = int(request.match_info['wheel_id'])
            body = await request.json()
            torque = float(body.get('torque', 0))
            result = await self.set_rw_torque(wheel_id, torque)
            return web.json_response({'success': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)
    
    async def _handle_thruster(self, request: web.Request) -> web.Response:
        """Control thruster"""
        try:
            thruster_id = int(request.match_info['thruster_id'])
            body = await request.json()
            firing = bool(body.get('firing', False))
            result = await self.set_thruster(thruster_id, firing)
            return web.json_response({'success': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)
    
    async def _handle_torquerod(self, request: web.Request) -> web.Response:
        """Control torque rod"""
        try:
            rod_id = int(request.match_info['rod_id'])
            body = await request.json()
            dipole = float(body.get('dipole', 0))
            result = await self.set_torque_rod(rod_id, dipole)
            return web.json_response({'success': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)
    
    async def _handle_sada(self, request: web.Request) -> web.Response:
        """Control SADA"""
        try:
            sada_id = int(request.match_info['sada_id'])
            body = await request.json()
            angle = float(body.get('angle', 0))
            result = await self.set_sada_angle(sada_id, angle)
            return web.json_response({'success': result})
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)
    
    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket handler for real-time updates"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        self.ws_clients.append(ws)
        logger.info("WebSocket client connected")
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # Handle incoming messages if needed
                    pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            self.ws_clients.remove(ws)
            logger.info("WebSocket client disconnected")
        
        return ws


async def main():
    """Main entry point"""
    config = SCOEConfig()
    controller = SCOEController(config)
    
    try:
        await controller.start()
    except KeyboardInterrupt:
        await controller.stop()


if __name__ == '__main__':
    asyncio.run(main())

