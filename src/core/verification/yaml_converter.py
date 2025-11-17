"""
YAML Converter
Simple JSON-to-YAML converter for robot sequences.

This is the final output stage after verification.
Company requirement: System stops at verification layer, outputs YAML.
Execution layer is for internal testing/simulation only.

See docs/robot-layer/README.md for YAML format specification.
"""

import yaml
from typing import Dict, List, Any
from src.core.observability.logging import get_logger

logger = get_logger("yaml_converter")


def convert_to_yaml(
    plan: List[Dict[str, Any]], 
    sequence_name: str, 
    description: str,
    correlation_id: str
) -> str:
    """
    Convert validated JSON plan to YAML format.
    
    This is a SIMPLE converter - no business logic, no Neo4j queries.
    The JSON from sequence_builder already has all required information.
    
    Required YAML structure (per docs/robot-layer/README.md):
        RobotSequence:
          name: "..."
          description: "..."
          steps:
            - id: 1
              name: "..."
              action: "move" | "routine"
              target: "..."
              [optional: safety_check, stabilize, action_after, verify, position]
    
    Args:
        plan: List of validated task steps (JSON from sequence_builder)
        sequence_name: Human-readable sequence identifier
        description: Sequence purpose summary
        correlation_id: UUIDv4 for request tracing
    
    Returns:
        YAML-formatted string ready for robot controller
    
    Example:
        >>> plan = [
        ...     {"id": 1, "name": "Move to <home_position>", "action": "move", "target": "<home_position>"},
        ...     {"id": 2, "name": "Execute routine at <position_1>", "action": "routine", 
        ...      "target": "<routine_name>", "position": "<position_1>"}
        ... ]
        >>> yaml_str = convert_to_yaml(plan, "<job_name>", "<job_description>", "abc-123")
    """
    logger.info("Converting JSON plan to YAML", 
               correlation_id=correlation_id, 
               step_count=len(plan))
    
    # Build YAML structure (simple dict mapping)
    robot_sequence = {
        "RobotSequence": {
            "name": sequence_name,
            "description": description,
            "steps": plan
        }
    }
    
    # Convert to YAML with clean formatting
    yaml_output = yaml.dump(
        robot_sequence,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        indent=2
    )
    
    logger.info("YAML conversion complete", 
               correlation_id=correlation_id, 
               yaml_length=len(yaml_output))
    
    return yaml_output
