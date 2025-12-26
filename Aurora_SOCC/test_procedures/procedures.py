#!/usr/bin/env python3
"""
Test Procedure Framework for SOCC V&V Testing

Provides structured test procedures that can be executed
through the SOCC console to verify satellite software behavior.
"""

import json
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Optional, Any
from enum import Enum
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestProcedure")


class StepStatus(Enum):
    """Test step status"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    BLOCKED = "BLOCKED"


class ProcedureStatus(Enum):
    """Overall procedure status"""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


@dataclass
class TestStep:
    """Individual test step"""
    step_number: int
    title: str
    description: str
    expected_result: str
    commands: List[str] = field(default_factory=list)
    verifications: List[str] = field(default_factory=list)
    timeout_sec: float = 60.0
    status: StepStatus = StepStatus.PENDING
    actual_result: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    notes: str = ""
    
    def duration(self) -> float:
        """Get step duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    def to_dict(self) -> Dict:
        return {
            "step_number": self.step_number,
            "title": self.title,
            "description": self.description,
            "expected_result": self.expected_result,
            "commands": self.commands,
            "verifications": self.verifications,
            "status": self.status.value,
            "actual_result": self.actual_result,
            "duration_sec": self.duration(),
            "notes": self.notes
        }


@dataclass
class TestProcedure:
    """Test procedure containing multiple steps"""
    procedure_id: str
    name: str
    description: str
    version: str
    category: str
    prerequisites: List[str] = field(default_factory=list)
    steps: List[TestStep] = field(default_factory=list)
    status: ProcedureStatus = ProcedureStatus.NOT_STARTED
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    tester: str = ""
    notes: str = ""
    
    def current_step(self) -> Optional[TestStep]:
        """Get current active step."""
        for step in self.steps:
            if step.status == StepStatus.RUNNING:
                return step
            if step.status == StepStatus.PENDING:
                return step
        return None
    
    def progress(self) -> float:
        """Get completion percentage."""
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status in [StepStatus.PASS, StepStatus.FAIL, StepStatus.SKIP])
        return (completed / len(self.steps)) * 100
    
    def passed(self) -> bool:
        """Check if all steps passed."""
        return all(s.status == StepStatus.PASS for s in self.steps)
    
    def to_dict(self) -> Dict:
        return {
            "procedure_id": self.procedure_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "prerequisites": self.prerequisites,
            "status": self.status.value,
            "progress": self.progress(),
            "steps": [s.to_dict() for s in self.steps],
            "tester": self.tester,
            "notes": self.notes,
            "passed": self.passed() if self.status == ProcedureStatus.COMPLETED else None
        }


class TestProcedureRunner:
    """Executes test procedures and tracks results"""
    
    def __init__(self, command_handler: Callable = None, telemetry_getter: Callable = None):
        """
        Initialize test procedure runner.
        
        Args:
            command_handler: Function to send commands to satellite
            telemetry_getter: Function to get current telemetry values
        """
        self.command_handler = command_handler
        self.telemetry_getter = telemetry_getter
        self.active_procedure: Optional[TestProcedure] = None
        self.results_history: List[Dict] = []
        self.callbacks: List[Callable] = []
    
    def load_procedure(self, procedure: TestProcedure):
        """Load a procedure for execution."""
        self.active_procedure = procedure
        logger.info(f"Loaded procedure: {procedure.name}")
    
    def start(self, tester: str = ""):
        """Start the loaded procedure."""
        if not self.active_procedure:
            raise ValueError("No procedure loaded")
        
        self.active_procedure.status = ProcedureStatus.IN_PROGRESS
        self.active_procedure.start_time = datetime.now()
        self.active_procedure.tester = tester
        
        logger.info(f"Started procedure: {self.active_procedure.name}")
        self._notify_update()
    
    def execute_step(self, step_number: int = None) -> TestStep:
        """
        Execute a specific step or the next pending step.
        
        Args:
            step_number: Specific step to execute, or None for next pending
            
        Returns:
            The executed step
        """
        if not self.active_procedure:
            raise ValueError("No procedure loaded")
        
        # Find the step to execute
        step = None
        if step_number is not None:
            for s in self.active_procedure.steps:
                if s.step_number == step_number:
                    step = s
                    break
        else:
            step = self.active_procedure.current_step()
        
        if not step:
            raise ValueError("No step to execute")
        
        # Start step execution
        step.status = StepStatus.RUNNING
        step.start_time = datetime.now()
        
        logger.info(f"Executing step {step.step_number}: {step.title}")
        self._notify_update()
        
        return step
    
    def complete_step(self, step_number: int, passed: bool, actual_result: str = "", notes: str = ""):
        """
        Mark a step as complete.
        
        Args:
            step_number: Step to complete
            passed: Whether step passed
            actual_result: Actual result observed
            notes: Any additional notes
        """
        if not self.active_procedure:
            return
        
        for step in self.active_procedure.steps:
            if step.step_number == step_number:
                step.status = StepStatus.PASS if passed else StepStatus.FAIL
                step.end_time = datetime.now()
                step.actual_result = actual_result
                step.notes = notes
                
                logger.info(f"Step {step_number} completed: {step.status.value}")
                self._notify_update()
                break
        
        # Check if procedure is complete
        if all(s.status in [StepStatus.PASS, StepStatus.FAIL, StepStatus.SKIP] 
               for s in self.active_procedure.steps):
            self._complete_procedure()
    
    def skip_step(self, step_number: int, reason: str = ""):
        """Skip a step."""
        if not self.active_procedure:
            return
        
        for step in self.active_procedure.steps:
            if step.step_number == step_number:
                step.status = StepStatus.SKIP
                step.notes = f"SKIPPED: {reason}"
                logger.info(f"Step {step_number} skipped: {reason}")
                self._notify_update()
                break
    
    def abort(self, reason: str = ""):
        """Abort the procedure."""
        if not self.active_procedure:
            return
        
        self.active_procedure.status = ProcedureStatus.ABORTED
        self.active_procedure.end_time = datetime.now()
        self.active_procedure.notes = f"ABORTED: {reason}"
        
        logger.warning(f"Procedure aborted: {reason}")
        self._save_results()
        self._notify_update()
    
    def _complete_procedure(self):
        """Complete the procedure and save results."""
        self.active_procedure.status = ProcedureStatus.COMPLETED
        self.active_procedure.end_time = datetime.now()
        
        # Determine overall pass/fail
        if self.active_procedure.passed():
            logger.info(f"Procedure PASSED: {self.active_procedure.name}")
        else:
            logger.warning(f"Procedure FAILED: {self.active_procedure.name}")
        
        self._save_results()
        self._notify_update()
    
    def _save_results(self):
        """Save procedure results."""
        if not self.active_procedure:
            return
        
        result = self.active_procedure.to_dict()
        result["completed_at"] = datetime.now().isoformat()
        self.results_history.append(result)
    
    def _notify_update(self):
        """Notify callbacks of procedure update."""
        for callback in self.callbacks:
            try:
                callback(self.active_procedure)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def register_callback(self, callback: Callable):
        """Register callback for procedure updates."""
        self.callbacks.append(callback)
    
    def get_results(self) -> List[Dict]:
        """Get all procedure results."""
        return self.results_history
    
    def export_results(self, filename: str):
        """Export results to JSON file."""
        with open(filename, 'w') as f:
            json.dump(self.results_history, f, indent=2, default=str)
        logger.info(f"Results exported to {filename}")


# ============================================
# Pre-defined Test Procedures
# ============================================

def create_nominal_checkout_procedure() -> TestProcedure:
    """Create nominal mode checkout procedure."""
    return TestProcedure(
        procedure_id="TP-001",
        name="Nominal Mode Checkout",
        description="Verify satellite nominal mode operations and attitude control",
        version="1.0.0",
        category="Functional",
        prerequisites=[
            "SCOE initialized with LEO Nominal scenario",
            "EGSE links verified",
            "Satellite in STANDBY mode"
        ],
        steps=[
            TestStep(
                step_number=1,
                title="Verify Initial State",
                description="Confirm satellite is in STANDBY mode with all sensors reporting nominal",
                expected_result="All sensors nominal, mode = STANDBY",
                verifications=["Mode = STANDBY", "All sensors NOMINAL", "Power NOMINAL"]
            ),
            TestStep(
                step_number=2,
                title="Command Nominal Mode",
                description="Send SET_NOMINAL command and verify mode transition",
                expected_result="Mode transitions to NOMINAL within 5 seconds",
                commands=["SET_NOMINAL"],
                verifications=["Mode = NOMINAL", "Attitude control enabled"]
            ),
            TestStep(
                step_number=3,
                title="Verify Attitude Control Active",
                description="Confirm reaction wheels enabled and attitude converging",
                expected_result="RW enabled, pointing error decreasing",
                verifications=["RW-1 through RW-4 enabled", "Pointing error < 1°"]
            ),
            TestStep(
                step_number=4,
                title="Check Telemetry Rates",
                description="Verify all telemetry points updating at expected rates",
                expected_result="All telemetry updating at 1 Hz",
                verifications=["Attitude TLM @ 1 Hz", "Sensor TLM @ 1 Hz", "Power TLM @ 1 Hz"]
            ),
            TestStep(
                step_number=5,
                title="Hold Nominal Mode",
                description="Maintain nominal mode for 5 minutes and verify stability",
                expected_result="Stable operation, no anomalies",
                timeout_sec=300.0,
                verifications=["No mode transitions", "Pointing stable", "No warnings/errors"]
            ),
            TestStep(
                step_number=6,
                title="Document Results",
                description="Record test completion and any anomalies observed",
                expected_result="Results documented",
                verifications=["Screenshot captured", "Notes recorded"]
            )
        ]
    )


def create_slew_maneuver_procedure() -> TestProcedure:
    """Create slew maneuver test procedure."""
    return TestProcedure(
        procedure_id="TP-002",
        name="Slew Maneuver Test",
        description="Verify satellite slew maneuver execution and settling",
        version="1.0.0",
        category="Maneuver",
        prerequisites=[
            "Satellite in NOMINAL mode",
            "Attitude stable (pointing error < 0.5°)",
            "Sufficient reaction wheel margin"
        ],
        steps=[
            TestStep(
                step_number=1,
                title="Record Initial Attitude",
                description="Document current attitude quaternion and Euler angles",
                expected_result="Initial attitude recorded",
                verifications=["Quaternion logged", "Euler angles logged"]
            ),
            TestStep(
                step_number=2,
                title="Command +X Slew 30°",
                description="Command a 30-degree slew about the X-axis",
                expected_result="Slew command accepted",
                commands=["SLEW_X_POS_30"],
                verifications=["Command ACK received"]
            ),
            TestStep(
                step_number=3,
                title="Monitor Slew Execution",
                description="Observe attitude rates and wheel speeds during slew",
                expected_result="Smooth rate profile, no saturation",
                timeout_sec=120.0,
                verifications=["Rate profile smooth", "Max rate within limits", "No wheel saturation"]
            ),
            TestStep(
                step_number=4,
                title="Verify Target Attitude",
                description="Confirm final attitude matches commanded target",
                expected_result="Attitude error < 0.1° from target",
                verifications=["Roll change = 30° ± 0.1°", "Pitch/Yaw unchanged"]
            ),
            TestStep(
                step_number=5,
                title="Verify Settling",
                description="Confirm attitude rates settle to near-zero",
                expected_result="All rates < 0.01°/s within 60s",
                timeout_sec=60.0,
                verifications=["Roll rate < 0.01°/s", "Pitch rate < 0.01°/s", "Yaw rate < 0.01°/s"]
            ),
            TestStep(
                step_number=6,
                title="Return to Initial Attitude",
                description="Command slew back to original attitude",
                expected_result="Return slew successful",
                commands=["SLEW_X_NEG_30"],
                verifications=["Return to initial attitude", "Error < 0.1°"]
            )
        ]
    )


def create_safe_mode_procedure() -> TestProcedure:
    """Create safe mode entry/exit test procedure."""
    return TestProcedure(
        procedure_id="TP-003",
        name="Safe Mode Entry and Recovery",
        description="Verify safe mode transition and recovery operations",
        version="1.0.0",
        category="Safe Mode",
        prerequisites=[
            "Satellite in NOMINAL mode",
            "All systems nominal",
            "Ground command link verified"
        ],
        steps=[
            TestStep(
                step_number=1,
                title="Verify Nominal State",
                description="Confirm satellite operating normally before test",
                expected_result="All systems nominal",
                verifications=["Mode = NOMINAL", "Power positive", "Comms active"]
            ),
            TestStep(
                step_number=2,
                title="Command Safe Mode Entry",
                description="Send SET_SAFE command to trigger safe mode",
                expected_result="Safe mode entered within 10 seconds",
                commands=["SET_SAFE"],
                verifications=["Mode = SAFE", "Sun acquisition started"]
            ),
            TestStep(
                step_number=3,
                title="Verify Safe Mode Behavior",
                description="Confirm safe mode sun-pointing and power conservation",
                expected_result="Sun acquired, power positive",
                timeout_sec=300.0,
                verifications=["Sun angle < 30°", "Power positive", "Non-essential loads off"]
            ),
            TestStep(
                step_number=4,
                title="Hold Safe Mode",
                description="Maintain safe mode for 5 minutes",
                expected_result="Stable safe mode operation",
                timeout_sec=300.0,
                verifications=["No mode oscillation", "Attitude stable", "No new anomalies"]
            ),
            TestStep(
                step_number=5,
                title="Command Nominal Recovery",
                description="Send SET_NOMINAL to recover from safe mode",
                expected_result="Nominal mode recovered",
                commands=["SET_NOMINAL"],
                verifications=["Mode = NOMINAL", "Full attitude control restored"]
            ),
            TestStep(
                step_number=6,
                title="Verify Full Recovery",
                description="Confirm all systems nominal after recovery",
                expected_result="All systems nominal",
                verifications=["Pointing error < 1°", "All sensors nominal", "All actuators enabled"]
            )
        ]
    )


def create_momentum_dump_procedure() -> TestProcedure:
    """Create momentum dump test procedure."""
    return TestProcedure(
        procedure_id="TP-004",
        name="Momentum Dump Operation",
        description="Verify reaction wheel momentum dumping using magnetorquers",
        version="1.0.0",
        category="ADCS",
        prerequisites=[
            "Satellite in NOMINAL mode",
            "Magnetorquers verified operational",
            "Accumulated momentum > 50% capacity"
        ],
        steps=[
            TestStep(
                step_number=1,
                title="Record Initial Momentum",
                description="Document current wheel speeds and total momentum",
                expected_result="Momentum state recorded",
                verifications=["RW speeds logged", "Total momentum logged"]
            ),
            TestStep(
                step_number=2,
                title="Verify Magnetic Field",
                description="Confirm valid magnetic field measurement",
                expected_result="Mag field valid and sufficient",
                verifications=["Mag reading valid", "|B| > 20000 nT"]
            ),
            TestStep(
                step_number=3,
                title="Command Momentum Dump",
                description="Initiate momentum dump sequence",
                expected_result="Dump sequence started",
                commands=["RW_MOMENTUM_DUMP"],
                verifications=["Dump mode active", "MTQ enabled"]
            ),
            TestStep(
                step_number=4,
                title="Monitor Dump Progress",
                description="Observe momentum reduction over time",
                expected_result="Momentum decreasing steadily",
                timeout_sec=600.0,
                verifications=["Momentum decreasing", "Attitude maintained", "No saturation"]
            ),
            TestStep(
                step_number=5,
                title="Verify Dump Completion",
                description="Confirm momentum within target band",
                expected_result="Momentum < 20% capacity",
                verifications=["All wheel speeds nominal", "Total momentum < target"]
            ),
            TestStep(
                step_number=6,
                title="Resume Normal Control",
                description="Confirm return to normal attitude control",
                expected_result="Normal control resumed",
                verifications=["Dump mode exited", "Pointing stable"]
            )
        ]
    )


# Procedure library
PROCEDURE_LIBRARY = {
    "TP-001": create_nominal_checkout_procedure,
    "TP-002": create_slew_maneuver_procedure,
    "TP-003": create_safe_mode_procedure,
    "TP-004": create_momentum_dump_procedure,
}


def get_procedure(procedure_id: str) -> TestProcedure:
    """Get a procedure from the library."""
    if procedure_id in PROCEDURE_LIBRARY:
        return PROCEDURE_LIBRARY[procedure_id]()
    raise ValueError(f"Unknown procedure: {procedure_id}")


def list_procedures() -> List[Dict]:
    """List all available procedures."""
    procedures = []
    for proc_id, factory in PROCEDURE_LIBRARY.items():
        proc = factory()
        procedures.append({
            "id": proc.procedure_id,
            "name": proc.name,
            "description": proc.description,
            "category": proc.category,
            "steps": len(proc.steps)
        })
    return procedures


if __name__ == "__main__":
    # Demo
    print("=== Available Test Procedures ===\n")
    for proc in list_procedures():
        print(f"[{proc['id']}] {proc['name']}")
        print(f"    Category: {proc['category']}")
        print(f"    Steps: {proc['steps']}")
        print(f"    {proc['description']}\n")

