"""
AOCS Simulation Models
Based on Section 7 of the Technical Proposal

Simulates:
- Environment Model (Section 7.1)
- Attitude Dynamics (Section 7.3)
- Sensors: Sun Sensor, Rate Sensor, Magnetometer (Section 7.5)
- Actuators: Reaction Wheels, Thrusters, Torque Rods, SADA (Section 7.6)
"""

import math
import time
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import IntEnum
import numpy as np


class EquipmentState(IntEnum):
    """Equipment power state"""
    OFF = 0
    ON = 1


class RWMode(IntEnum):
    """Reaction Wheel modes"""
    STANDBY = 0
    OPERATE = 1


class RWCommandCode(IntEnum):
    """Reaction Wheel command codes from Table 8/9"""
    MOTOR_CONTROL = 0x00
    SPEED_TORQUE_TIMEOUT = 0x02
    RESET_CONTROL = 0x03
    TORQUE_SPEED_CONTROL = 0x04
    CLEAR_FAULTS = 0x05
    CHANGE_ADDRESS = 0x0C
    MODE_CONTROL = 0x0E


@dataclass
class Vector3:
    """3D Vector representation"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    
    def __add__(self, other: 'Vector3') -> 'Vector3':
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)
    
    def __mul__(self, scalar: float) -> 'Vector3':
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)
    
    def magnitude(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)
    
    def normalize(self) -> 'Vector3':
        mag = self.magnitude()
        if mag > 0:
            return Vector3(self.x / mag, self.y / mag, self.z / mag)
        return Vector3()
    
    def to_list(self) -> List[float]:
        return [self.x, self.y, self.z]
    
    def to_dict(self) -> Dict[str, float]:
        return {'x': self.x, 'y': self.y, 'z': self.z}


@dataclass
class Quaternion:
    """Quaternion for attitude representation"""
    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    
    def normalize(self) -> 'Quaternion':
        mag = math.sqrt(self.w**2 + self.x**2 + self.y**2 + self.z**2)
        if mag > 0:
            return Quaternion(self.w / mag, self.x / mag, self.y / mag, self.z / mag)
        return Quaternion()
    
    def to_euler(self) -> Vector3:
        """Convert to Euler angles (roll, pitch, yaw) in radians"""
        # Roll (x-axis rotation)
        sinr_cosp = 2 * (self.w * self.x + self.y * self.z)
        cosr_cosp = 1 - 2 * (self.x**2 + self.y**2)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        
        # Pitch (y-axis rotation)
        sinp = 2 * (self.w * self.y - self.z * self.x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)
        else:
            pitch = math.asin(sinp)
        
        # Yaw (z-axis rotation)
        siny_cosp = 2 * (self.w * self.z + self.x * self.y)
        cosy_cosp = 1 - 2 * (self.y**2 + self.z**2)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        
        return Vector3(roll, pitch, yaw)
    
    def to_dict(self) -> Dict[str, float]:
        return {'w': self.w, 'x': self.x, 'y': self.y, 'z': self.z}


@dataclass
class ReactionWheel:
    """Reaction Wheel model based on Section 7.6.1"""
    wheel_id: int
    state: EquipmentState = EquipmentState.OFF
    mode: RWMode = RWMode.STANDBY
    motor_enabled: bool = False
    
    # Physical parameters
    inertia: float = 0.01  # kg*m^2
    max_speed: float = 6000.0  # RPM
    max_torque: float = 0.2  # Nm
    
    # Current state
    speed: float = 0.0  # RPM
    commanded_torque: float = 0.0  # Nm
    commanded_speed: float = 0.0  # RPM (if speed control mode)
    
    # Telemetry
    temperature: float = 25.0  # Celsius
    current: float = 0.0  # Amps
    voltage: float = 28.0  # Volts
    
    # Errors/faults
    fault_flags: int = 0
    
    # Noise parameters
    speed_noise_std: float = 0.5  # RPM
    torque_noise_std: float = 0.001  # Nm
    
    def update(self, dt: float):
        """Update wheel state for timestep dt"""
        if self.state == EquipmentState.OFF or not self.motor_enabled:
            # Spin down due to friction
            friction_torque = 0.001 * np.sign(self.speed)
            self.speed -= (friction_torque / self.inertia) * dt * 60 / (2 * math.pi)
            if abs(self.speed) < 1:
                self.speed = 0
            return
        
        if self.mode == RWMode.OPERATE:
            # Apply commanded torque with noise
            actual_torque = self.commanded_torque + random.gauss(0, self.torque_noise_std)
            actual_torque = max(-self.max_torque, min(self.max_torque, actual_torque))
            
            # Update speed (RPM)
            angular_accel = actual_torque / self.inertia  # rad/s^2
            self.speed += angular_accel * dt * 60 / (2 * math.pi)  # Convert to RPM
            self.speed = max(-self.max_speed, min(self.max_speed, self.speed))
            
            # Update current based on torque
            self.current = abs(self.commanded_torque) * 5.0 + 0.1  # Simplified model
            
            # Temperature model
            power = self.current * self.voltage
            self.temperature += power * 0.001 * dt - (self.temperature - 25.0) * 0.01 * dt
    
    def get_measured_speed(self) -> float:
        """Get speed with measurement noise"""
        return self.speed + random.gauss(0, self.speed_noise_std)
    
    def get_reaction_torque(self) -> float:
        """Get torque applied to spacecraft (opposite of wheel torque)"""
        if self.state == EquipmentState.ON and self.motor_enabled:
            return -self.commanded_torque
        return 0.0
    
    def process_command(self, cmd_code: int, data: bytes) -> bool:
        """Process reaction wheel command"""
        if cmd_code == RWCommandCode.MOTOR_CONTROL:
            self.motor_enabled = data[0] == 1 if data else False
            return True
        elif cmd_code == RWCommandCode.MODE_CONTROL:
            self.mode = RWMode.OPERATE if (data[0] == 1 if data else False) else RWMode.STANDBY
            return True
        elif cmd_code == RWCommandCode.RESET_CONTROL:
            self.motor_enabled = False
            self.mode = RWMode.STANDBY
            self.commanded_torque = 0.0
            self.fault_flags = 0
            return True
        elif cmd_code == RWCommandCode.TORQUE_SPEED_CONTROL:
            if len(data) >= 4:
                import struct
                value = struct.unpack('>f', data[:4])[0]
                self.commanded_torque = max(-self.max_torque, min(self.max_torque, value))
            return True
        return False
    
    def get_telemetry(self) -> Dict[str, float]:
        """Get telemetry dictionary"""
        return {
            f'rw{self.wheel_id}_speed': self.get_measured_speed(),
            f'rw{self.wheel_id}_temperature': self.temperature,
            f'rw{self.wheel_id}_current': self.current,
            f'rw{self.wheel_id}_cmd_torque': self.commanded_torque,
            f'rw{self.wheel_id}_mode': float(self.mode),
            f'rw{self.wheel_id}_motor_enabled': float(self.motor_enabled),
        }


@dataclass
class Magnetometer:
    """Magnetometer model based on Section 7.5.3"""
    state: EquipmentState = EquipmentState.OFF
    op_mode: int = 0  # 0=Init, 1=Service, 2=Operational
    
    # Calibration errors
    scale_factor: Vector3 = field(default_factory=lambda: Vector3(1.0, 1.0, 1.0))
    bias: Vector3 = field(default_factory=lambda: Vector3(0.0, 0.0, 0.0))
    misalignment: float = 0.001  # radians
    
    # Noise
    noise_std: float = 10.0  # nT
    
    # Measured field
    measured_field: Vector3 = field(default_factory=Vector3)
    
    def update(self, true_field: Vector3):
        """Update magnetometer reading"""
        if self.state == EquipmentState.OFF or self.op_mode != 2:
            self.measured_field = Vector3()
            return
        
        # Apply scale factor and bias
        self.measured_field = Vector3(
            true_field.x * self.scale_factor.x + self.bias.x + random.gauss(0, self.noise_std),
            true_field.y * self.scale_factor.y + self.bias.y + random.gauss(0, self.noise_std),
            true_field.z * self.scale_factor.z + self.bias.z + random.gauss(0, self.noise_std)
        )
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            'mag_x': self.measured_field.x,
            'mag_y': self.measured_field.y,
            'mag_z': self.measured_field.z,
            'mag_mode': float(self.op_mode),
        }


@dataclass
class RateSensor:
    """Rate Sensor (Gyro) model based on Section 7.5.2"""
    state: EquipmentState = EquipmentState.OFF
    
    # Error parameters
    arw: float = 0.003  # deg/sqrt(hr) - Angular Random Walk
    rrw: float = 0.0001  # deg/hr/sqrt(hr) - Rate Random Walk
    bias: Vector3 = field(default_factory=lambda: Vector3(0.01, 0.01, 0.01))  # deg/s
    scale_factor_error: float = 0.0001  # fractional
    misalignment: float = 0.001  # radians
    quantization: float = 0.001  # deg/s
    
    # Current bias (drifts over time)
    current_bias: Vector3 = field(default_factory=Vector3)
    
    # Measured rate
    measured_rate: Vector3 = field(default_factory=Vector3)
    
    def update(self, true_rate: Vector3, dt: float):
        """Update rate sensor reading"""
        if self.state == EquipmentState.OFF:
            self.measured_rate = Vector3()
            return
        
        # Update bias drift (random walk)
        rrw_sigma = self.rrw * math.sqrt(dt) / 3600  # Convert to deg/s
        self.current_bias = Vector3(
            self.current_bias.x + random.gauss(0, rrw_sigma),
            self.current_bias.y + random.gauss(0, rrw_sigma),
            self.current_bias.z + random.gauss(0, rrw_sigma)
        )
        
        # ARW noise
        arw_sigma = self.arw * math.sqrt(1/dt) / 60  # Convert to deg/s
        
        # Apply errors
        self.measured_rate = Vector3(
            true_rate.x * (1 + self.scale_factor_error) + self.bias.x + self.current_bias.x + random.gauss(0, arw_sigma),
            true_rate.y * (1 + self.scale_factor_error) + self.bias.y + self.current_bias.y + random.gauss(0, arw_sigma),
            true_rate.z * (1 + self.scale_factor_error) + self.bias.z + self.current_bias.z + random.gauss(0, arw_sigma)
        )
        
        # Quantization
        self.measured_rate = Vector3(
            round(self.measured_rate.x / self.quantization) * self.quantization,
            round(self.measured_rate.y / self.quantization) * self.quantization,
            round(self.measured_rate.z / self.quantization) * self.quantization
        )
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            'gyro_x': self.measured_rate.x,
            'gyro_y': self.measured_rate.y,
            'gyro_z': self.measured_rate.z,
        }


@dataclass
class SunSensor:
    """Sun Sensor model based on Section 7.5.1"""
    sensor_id: int
    state: EquipmentState = EquipmentState.OFF
    
    # Mounting (body frame)
    boresight: Vector3 = field(default_factory=lambda: Vector3(1, 0, 0))
    fov: float = 60.0  # degrees (half-angle)
    
    # Output
    sun_detected: bool = False
    azimuth: float = 0.0  # degrees
    elevation: float = 0.0  # degrees
    intensity: float = 0.0  # 0-1
    
    # Noise
    noise_std: float = 0.1  # degrees
    
    def update(self, sun_direction_body: Vector3, in_eclipse: bool):
        """Update sun sensor reading"""
        if self.state == EquipmentState.OFF or in_eclipse:
            self.sun_detected = False
            self.azimuth = 0.0
            self.elevation = 0.0
            self.intensity = 0.0
            return
        
        # Calculate angle from boresight
        sun_norm = sun_direction_body.normalize()
        cos_angle = (self.boresight.x * sun_norm.x + 
                     self.boresight.y * sun_norm.y + 
                     self.boresight.z * sun_norm.z)
        angle = math.acos(max(-1, min(1, cos_angle))) * 180 / math.pi
        
        if angle > self.fov:
            self.sun_detected = False
            self.azimuth = 0.0
            self.elevation = 0.0
            self.intensity = 0.0
            return
        
        self.sun_detected = True
        
        # Calculate azimuth and elevation in sensor frame (simplified)
        self.azimuth = math.atan2(sun_norm.y, sun_norm.x) * 180 / math.pi + random.gauss(0, self.noise_std)
        self.elevation = math.atan2(sun_norm.z, math.sqrt(sun_norm.x**2 + sun_norm.y**2)) * 180 / math.pi + random.gauss(0, self.noise_std)
        self.intensity = max(0, cos_angle) + random.gauss(0, 0.01)
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            f'ss{self.sensor_id}_detected': float(self.sun_detected),
            f'ss{self.sensor_id}_azimuth': self.azimuth,
            f'ss{self.sensor_id}_elevation': self.elevation,
            f'ss{self.sensor_id}_intensity': self.intensity,
        }


@dataclass
class Thruster:
    """Electric Propulsion Thruster model based on Section 7.6.2"""
    thruster_id: int
    state: EquipmentState = EquipmentState.OFF
    firing: bool = False
    
    # Physical parameters
    thrust_nominal: float = 0.1  # N
    isp: float = 3000  # seconds
    position: Vector3 = field(default_factory=Vector3)  # Position in body frame
    direction: Vector3 = field(default_factory=lambda: Vector3(0, 0, -1))  # Thrust direction
    
    # Errors
    thrust_error: float = 0.02  # 2% error
    misalignment: float = 0.5  # degrees
    
    # Telemetry
    temperature: float = 25.0
    propellant_flow: float = 0.0  # g/s
    
    def update(self, dt: float):
        """Update thruster state"""
        if self.state == EquipmentState.OFF:
            self.firing = False
            self.propellant_flow = 0.0
            return
        
        if self.firing:
            # Calculate flow rate: thrust = Isp * g0 * mdot
            g0 = 9.81
            self.propellant_flow = self.get_actual_thrust() / (self.isp * g0) * 1000  # g/s
            self.temperature += 0.5 * dt  # Heat up while firing
        else:
            self.propellant_flow = 0.0
            self.temperature -= (self.temperature - 25.0) * 0.1 * dt  # Cool down
    
    def get_actual_thrust(self) -> float:
        """Get actual thrust with errors"""
        if not self.firing or self.state == EquipmentState.OFF:
            return 0.0
        return self.thrust_nominal * (1 + random.gauss(0, self.thrust_error))
    
    def get_force_torque(self, com: Vector3) -> Tuple[Vector3, Vector3]:
        """Get force and torque on spacecraft"""
        if not self.firing or self.state == EquipmentState.OFF:
            return Vector3(), Vector3()
        
        thrust = self.get_actual_thrust()
        force = self.direction * thrust
        
        # Torque = r x F
        r = Vector3(self.position.x - com.x, self.position.y - com.y, self.position.z - com.z)
        torque = Vector3(
            r.y * force.z - r.z * force.y,
            r.z * force.x - r.x * force.z,
            r.x * force.y - r.y * force.x
        )
        
        return force, torque
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            f'thr{self.thruster_id}_firing': float(self.firing),
            f'thr{self.thruster_id}_temperature': self.temperature,
            f'thr{self.thruster_id}_flow': self.propellant_flow,
        }


@dataclass
class TorqueRod:
    """Torque Rod (Magnetorquer) model based on Section 7.6.3"""
    rod_id: int
    state: EquipmentState = EquipmentState.OFF
    
    # Axis in body frame
    axis: Vector3 = field(default_factory=lambda: Vector3(1, 0, 0))
    
    # Commanded dipole moment (Am^2)
    commanded_dipole: float = 0.0
    max_dipole: float = 50.0  # Am^2
    
    # Nonlinearity
    saturation: float = 50.0  # Am^2
    
    def get_actual_dipole(self) -> float:
        """Get actual magnetic dipole moment"""
        if self.state == EquipmentState.OFF:
            return 0.0
        
        # Apply saturation
        return max(-self.saturation, min(self.saturation, self.commanded_dipole))
    
    def get_torque(self, magnetic_field: Vector3) -> Vector3:
        """Calculate torque: T = m x B"""
        dipole = self.get_actual_dipole()
        m = self.axis * dipole
        
        return Vector3(
            m.y * magnetic_field.z - m.z * magnetic_field.y,
            m.z * magnetic_field.x - m.x * magnetic_field.z,
            m.x * magnetic_field.y - m.y * magnetic_field.x
        )
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            f'mtr{self.rod_id}_dipole': self.get_actual_dipole(),
            f'mtr{self.rod_id}_commanded': self.commanded_dipole,
        }


@dataclass
class SADA:
    """Solar Array Driving Assembly model based on Section 7.6.4"""
    sada_id: int
    state: EquipmentState = EquipmentState.OFF
    deployed: bool = False
    
    # Current angle (degrees)
    angle: float = 0.0
    commanded_angle: float = 0.0
    
    # Dynamics
    max_rate: float = 1.0  # deg/s
    
    # Telemetry
    temperature: float = 25.0
    
    def update(self, dt: float):
        """Update SADA position"""
        if self.state == EquipmentState.OFF or not self.deployed:
            return
        
        # Move towards commanded angle
        error = self.commanded_angle - self.angle
        rate = max(-self.max_rate, min(self.max_rate, error / dt if dt > 0 else 0))
        self.angle += rate * dt
    
    def get_telemetry(self) -> Dict[str, float]:
        return {
            f'sada{self.sada_id}_angle': self.angle,
            f'sada{self.sada_id}_commanded': self.commanded_angle,
            f'sada{self.sada_id}_deployed': float(self.deployed),
            f'sada{self.sada_id}_temperature': self.temperature,
        }


@dataclass
class SpacecraftState:
    """Complete spacecraft state"""
    # Attitude
    quaternion: Quaternion = field(default_factory=Quaternion)
    angular_rate: Vector3 = field(default_factory=Vector3)  # rad/s
    
    # Orbit
    position: Vector3 = field(default_factory=lambda: Vector3(7000000, 0, 0))  # m (ECI)
    velocity: Vector3 = field(default_factory=lambda: Vector3(0, 7500, 0))  # m/s (ECI)
    
    # Mass properties
    mass: float = 500.0  # kg
    inertia: Vector3 = field(default_factory=lambda: Vector3(100, 100, 50))  # kg*m^2
    com: Vector3 = field(default_factory=Vector3)  # Center of mass
    
    # Environment
    sun_direction_eci: Vector3 = field(default_factory=lambda: Vector3(1, 0, 0))
    magnetic_field_eci: Vector3 = field(default_factory=lambda: Vector3(0, 0, 30000))  # nT
    in_eclipse: bool = False


class AOCSSimulation:
    """Complete AOCS Simulation integrating all models"""
    
    def __init__(self):
        # Simulation parameters
        self.dt = 1.0 / 80.0  # 80 Hz
        self.time = 0.0
        self.running = False
        
        # Spacecraft state
        self.state = SpacecraftState()
        
        # Sensors
        self.magnetometer = Magnetometer()
        self.rate_sensor = RateSensor()
        self.sun_sensors = [
            SunSensor(i, boresight=Vector3(*b)) 
            for i, b in enumerate([
                (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)
            ])
        ]
        
        # Actuators
        self.reaction_wheels = [ReactionWheel(i) for i in range(4)]
        self.thrusters = [
            Thruster(i, position=Vector3(*p), direction=Vector3(*d))
            for i, (p, d) in enumerate([
                ((1, 0, 0), (-1, 0, 0)),  # +X thruster
                ((-1, 0, 0), (1, 0, 0)),  # -X thruster
                ((0, 1, 0), (0, -1, 0)),  # +Y thruster
                ((0, -1, 0), (0, 1, 0)),  # -Y thruster
            ])
        ]
        self.torque_rods = [
            TorqueRod(i, axis=Vector3(*a))
            for i, a in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1)])
        ]
        self.sadas = [SADA(i) for i in range(2)]
        
        # Initialize all equipment to ON
        self._power_on_all()
    
    def _power_on_all(self):
        """Power on all equipment"""
        self.magnetometer.state = EquipmentState.ON
        self.magnetometer.op_mode = 2  # Operational
        self.rate_sensor.state = EquipmentState.ON
        for ss in self.sun_sensors:
            ss.state = EquipmentState.ON
        for rw in self.reaction_wheels:
            rw.state = EquipmentState.ON
            rw.motor_enabled = True
            rw.mode = RWMode.OPERATE
        for thr in self.thrusters:
            thr.state = EquipmentState.ON
        for mtr in self.torque_rods:
            mtr.state = EquipmentState.ON
        for sada in self.sadas:
            sada.state = EquipmentState.ON
            sada.deployed = True
    
    def step(self):
        """Perform one simulation step"""
        if not self.running:
            return
        
        # Calculate total torque from actuators
        total_torque = Vector3()
        
        # Reaction wheel torques
        for rw in self.reaction_wheels:
            rw.update(self.dt)
            # Simplified: assume wheels aligned with body axes
            # In reality, would use wheel orientation matrix
            total_torque.x += rw.get_reaction_torque() * 0.5  # Wheel 0,1 contribute to X
            total_torque.y += rw.get_reaction_torque() * 0.5  # Wheel 2,3 contribute to Y
        
        # Thruster torques
        for thr in self.thrusters:
            thr.update(self.dt)
            _, torque = thr.get_force_torque(self.state.com)
            total_torque = total_torque + torque
        
        # Torque rod torques
        for mtr in self.torque_rods:
            torque = mtr.get_torque(self.state.magnetic_field_eci)
            total_torque = total_torque + torque
        
        # Update angular rate (simplified Euler dynamics)
        alpha = Vector3(
            total_torque.x / self.state.inertia.x,
            total_torque.y / self.state.inertia.y,
            total_torque.z / self.state.inertia.z
        )
        self.state.angular_rate = self.state.angular_rate + alpha * self.dt
        
        # Update quaternion (simplified)
        omega = self.state.angular_rate
        q = self.state.quaternion
        dq = Quaternion(
            -0.5 * (q.x * omega.x + q.y * omega.y + q.z * omega.z),
            0.5 * (q.w * omega.x + q.y * omega.z - q.z * omega.y),
            0.5 * (q.w * omega.y + q.z * omega.x - q.x * omega.z),
            0.5 * (q.w * omega.z + q.x * omega.y - q.y * omega.x)
        )
        self.state.quaternion = Quaternion(
            q.w + dq.w * self.dt,
            q.x + dq.x * self.dt,
            q.y + dq.y * self.dt,
            q.z + dq.z * self.dt
        ).normalize()
        
        # Update sensors
        self.magnetometer.update(self.state.magnetic_field_eci)
        self.rate_sensor.update(
            Vector3(
                self.state.angular_rate.x * 180 / math.pi,
                self.state.angular_rate.y * 180 / math.pi,
                self.state.angular_rate.z * 180 / math.pi
            ),
            self.dt
        )
        for ss in self.sun_sensors:
            ss.update(self.state.sun_direction_eci, self.state.in_eclipse)
        
        # Update SADA
        for sada in self.sadas:
            sada.update(self.dt)
        
        self.time += self.dt
    
    def get_all_telemetry(self) -> Dict[str, float]:
        """Get all telemetry as a dictionary"""
        tm = {
            'sim_time': self.time,
            'sim_running': float(self.running),
            
            # Attitude
            'att_q_w': self.state.quaternion.w,
            'att_q_x': self.state.quaternion.x,
            'att_q_y': self.state.quaternion.y,
            'att_q_z': self.state.quaternion.z,
            
            # Angular rate (deg/s)
            'rate_x': self.state.angular_rate.x * 180 / math.pi,
            'rate_y': self.state.angular_rate.y * 180 / math.pi,
            'rate_z': self.state.angular_rate.z * 180 / math.pi,
            
            # Position
            'pos_x': self.state.position.x,
            'pos_y': self.state.position.y,
            'pos_z': self.state.position.z,
            
            # Environment
            'in_eclipse': float(self.state.in_eclipse),
        }
        
        # Add sensor telemetry
        tm.update(self.magnetometer.get_telemetry())
        tm.update(self.rate_sensor.get_telemetry())
        for ss in self.sun_sensors:
            tm.update(ss.get_telemetry())
        
        # Add actuator telemetry
        for rw in self.reaction_wheels:
            tm.update(rw.get_telemetry())
        for thr in self.thrusters:
            tm.update(thr.get_telemetry())
        for mtr in self.torque_rods:
            tm.update(mtr.get_telemetry())
        for sada in self.sadas:
            tm.update(sada.get_telemetry())
        
        return tm
    
    def start(self):
        """Start simulation"""
        self.running = True
    
    def stop(self):
        """Stop simulation"""
        self.running = False
    
    def reset(self):
        """Reset simulation to initial conditions"""
        self.time = 0.0
        self.state = SpacecraftState()
        for rw in self.reaction_wheels:
            rw.speed = 0.0
            rw.commanded_torque = 0.0


