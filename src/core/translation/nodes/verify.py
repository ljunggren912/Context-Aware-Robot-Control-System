"""
Verify Node

Performs safety validation on planned task sequences and generates YAML output.
This is the final stage before execution - verifies that:
  - All positions exist and are reachable
  - Tool operations are valid
  - Movement edges are allowed in the graph

See docs/verification/README.md for detailed validation logic.
"""

import os
from typing import Dict, Any
from datetime import datetime
from pathlib import Path
from src.core.translation.state import WorkflowState
from src.core.verification.verifier import verify_plan
from src.core.verification.yaml_converter import convert_to_yaml
from src.core.observability.logging import get_logger

logger = get_logger("verify_node")


def verify_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Verify plan safety and generate YAML output.
    
    This node performs two main functions:
    1. Safety validation: Check that all positions, tools, and movements are valid
    2. YAML generation: Convert validated plan to executable YAML format
    
    If verification fails, error feedback is provided for the planner to retry.
    
    Args:
        state: WorkflowState containing:
            - plan: Task sequence to verify
            - correlation_id: Run identifier
            - operator_input: Original command
            - plan_attempt: Current attempt number
    
    Returns:
        Dict with verification_result and yaml_sequence (if valid), 
        or validation_errors for retry (if invalid)
    """
    correlation_id = state["correlation_id"]
    plan = state["plan"]
    operator_input = state.get("operator_input", "Unknown command")
    plan_attempt = state.get("plan_attempt", 1)
    
    logger.info("Starting verification", correlation_id=correlation_id, attempt=plan_attempt)
    
    # Step 1: Verify plan safety (positions, tools, movements)
    result = verify_plan(plan, correlation_id)
    
    # Convert verification result to dictionary for state storage
    verification_result = result.to_dict()
    
    if result.valid:
        # Step 2: Generate YAML output for robot execution
        logger.info("Verification passed - generating YAML output", 
                   correlation_id=correlation_id)
        
        # Convert validated plan to YAML format
        yaml_output = convert_to_yaml(
            plan=plan,
            sequence_name=f"Sequence_{correlation_id[:8]}",
            description=operator_input,
            correlation_id=correlation_id
        )
        
        # Write YAML to file for robot execution
        _write_yaml_to_file(yaml_output, correlation_id, operator_input)
        
        return {
            "verification_result": verification_result,
            "yaml_sequence": yaml_output,
        }
    else:
        # Verification failed - provide feedback for planner to fix issues
        feedback = verification_result["feedback"]
        logger.warning("Verification failed", 
                      correlation_id=correlation_id,
                      feedback=feedback,
                      attempt=plan_attempt)
        return {
            "verification_result": verification_result,
            "validation_errors": feedback,  # Feedback sent to planner for retry
            "plan_attempt": plan_attempt + 1,
        }


def verify_condition(state: WorkflowState) -> str:
    """
    Routing logic after verification.
    
    Routes based on verification result and current attempt count.
    
    Returns:
        - "robot": Verification passed, proceed to execution
        - "sequence_planning": Verification failed, retry planning with feedback
        - "fallback": Verification failed and max attempts reached
    """
    verification_result = state.get("verification_result", {})
    plan_attempt = state.get("plan_attempt", 1)
    max_attempts = int(os.getenv("MAX_PLAN_ATTEMPTS", "3"))
    
    if verification_result.get("valid"):
        # Verification passed - proceed to robot execution
        return "robot"
    elif plan_attempt < max_attempts:
        # Verification failed - retry planning with feedback
        return "sequence_planning"
    else:
        # Max attempts exceeded - abort to fallback
        return "fallback"


def _write_yaml_to_file(yaml_sequence: str, correlation_id: str, operator_input: str):
    """
    Write YAML sequence to actions.yaml file.
    
    This file is the deliverable output that can be executed by the robot controller.
    Each write overwrites the previous content with the new sequence.
    """
    actions_file = Path("actions.yaml")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Add header with run metadata
    separator = f"""
# ============================================================================
# Run ID: {correlation_id}
# Command: "{operator_input}"
# Timestamp: {timestamp}
# ============================================================================

"""
    
    # Write YAML with header
    with open(actions_file, "w", encoding="utf-8") as f:
        f.write(separator)
        f.write(yaml_sequence)
        f.write("\n\n")
    
    logger.info("YAML written to actions.yaml", 
               correlation_id=correlation_id,
               file=str(actions_file))
