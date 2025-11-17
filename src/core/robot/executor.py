"""
Robot Execution Module

Modes:
- SIMULATION: Logs to actions.yaml, simulates timing
- SOCKET: TCP/IP communication with robot controller

Environment: ROBOT_EXECUTION_MODE=simulation|socket
"""

import os
import json
import time
from typing import Dict, Any
from datetime import datetime
from pathlib import Path
from src.core.knowledge.sqlite_client import RobotStateDB, HistoryDB
from src.core.observability.logging import get_logger
from src.core.robot.socket_client_class import RobotSocketClient

logger = get_logger("robot_shim")


class RobotExecutor:
    """
    Executes robot sequences in simulation or socket mode.
    
    Simulation: Simulates timing, updates databases
    Socket: Writes YAML to file, socket code reads and executes each line
    
    Both modes update SQLite.
    """
    
    def __init__(self):
        self.execution_mode = os.getenv("ROBOT_EXECUTION_MODE", "socket")
        self.actions_file = Path("actions.yaml")
        self.state_db = RobotStateDB()
        self.history_db = HistoryDB()
        self.socket_client = None
        
        logger.info(f"RobotExecutor initialized ({self.execution_mode.upper()} MODE)", 
                   actions_file=str(self.actions_file))
    
    def execute_sequence(
        self, 
        yaml_sequence: str, 
        plan: list,
        correlation_id: str,
        operator_input: str
    ) -> Dict[str, Any]:
        """
        Execute robot sequence.
        
        YAML already written by verification layer.
        This layer executes and updates databases.
        """
        logger.info(f"Starting {self.execution_mode.upper()} execution", 
                   correlation_id=correlation_id)
        
        plan_json = json.dumps(plan)
        self.history_db.create_run(correlation_id, operator_input, plan_json)
        
        try:
            self.history_db.update_run_status(correlation_id, "running")
            
            if self.execution_mode == "socket":
                result = self._execute_socket_mode(plan, correlation_id)
            else:
                result = self._execute_simulation_mode(plan, correlation_id)
            
            return result
        
        except Exception as e:
            error_msg = f"{self.execution_mode.upper()} execution failed: {str(e)}"
            logger.error("Execution failed", 
                        correlation_id=correlation_id, 
                        error=error_msg)
            self.history_db.update_run_status(correlation_id, "failed")
            
            return {
                "success": False,
                "message": error_msg,
                "run_id": correlation_id
            }
    
    def _execute_simulation_mode(self, plan: list, correlation_id: str) -> Dict[str, Any]:
        """Simulate step-by-step execution."""
        for step in plan:
            step_id = self.history_db.add_step(
                correlation_id,
                step.get("target"),
                step["action"]
            )
            
            self.history_db.update_step_state(step_id, "running")
            time.sleep(0.5)
            self.history_db.update_step_state(step_id, "completed")
            self._update_state_from_step(step, correlation_id)
        
        self.history_db.update_run_status(correlation_id, "completed")
        
        logger.info("SIMULATION completed", 
                   correlation_id=correlation_id,
                   steps=len(plan))
        
        return {
            "success": True,
            "message": f"Simulated successfully ({len(plan)} steps)",
            "run_id": correlation_id
        }
    
    def _execute_socket_mode(self, plan: list, correlation_id: str) -> Dict[str, Any]:
        """Execute via socket communication."""
        try:
            if self.socket_client is None:
                self.socket_client = RobotSocketClient()
            
            if not self.socket_client.is_connected():
                host = os.getenv("ROBOT_SOCKET_HOST", "127.0.0.1")
                port = int(os.getenv("ROBOT_SOCKET_PORT", 5000))
                self.socket_client.connect_robot(host, port)
            
            # Execute sequence
            success = self.socket_client.execute_sequence(str(self.actions_file))
            if not success:
                self.history_db.update_run_status(correlation_id, "failed")
                return {
                    "success": False,
                    "message": "Socket execution failed",
                    "run_id": correlation_id
                }
            
            # Update databases
            for step in plan:
                step_id = self.history_db.add_step(
                    correlation_id,
                    step.get("target"),
                    step["action"]
                )
                self.history_db.update_step_state(step_id, "completed")
                self._update_state_from_step(step, correlation_id)
            
            self.history_db.update_run_status(correlation_id, "completed")
            
            logger.info("SOCKET execution completed",
                       correlation_id=correlation_id,
                       steps=len(plan))
            
            return {
                "success": True,
                "message": f"Sequence executed via robot ({len(plan)} steps)",
                "run_id": correlation_id
            }
            
        except Exception as e:
            logger.error("Socket execution error", error=str(e))
            self.history_db.update_run_status(correlation_id, "failed")
            return {
                "success": False,
                "message": f"Socket error: {str(e)}",
                "run_id": correlation_id
            }
    
    def _update_state_from_step(self, step: Dict[str, Any], correlation_id: str):
        """Update robot state DB based on step."""
        if step["action"] == "move":
            self.state_db.update_position(step["target"])
            logger.info(f"{self.execution_mode.upper()} move", 
                       correlation_id=correlation_id,
                       target=step["target"])
        
        elif step["action"] == "routine":
            if step["target"] == "tool_attach":
                tool_name = step.get("tool")
                if not tool_name:
                    raise ValueError(f"tool_attach step missing 'tool' key: {step}")
                self.state_db.update_tool(tool_name)
                logger.info(f"{self.execution_mode.upper()} tool attach", 
                           correlation_id=correlation_id,
                           tool=tool_name)
            
            elif step["target"] == "tool_release":
                self.state_db.update_tool("none")
                logger.info(f"{self.execution_mode.upper()} tool release", 
                           correlation_id=correlation_id)
            
            else:
                logger.info(f"{self.execution_mode.upper()} routine",
                           correlation_id=correlation_id,
                           routine=step["target"])
    
    def get_current_state(self) -> Dict[str, str]:
        """Get current robot state from SQLite."""
        return self.state_db.get_state()

