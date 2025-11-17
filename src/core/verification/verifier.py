"""
Verification Layer
Deterministic safety checks for task plans.
External module called BY LangGraph nodes (not a node itself).
See docs/verification/README.md for validation logic.
"""

from typing import Dict, List, Any
from src.core.knowledge.neo4j_client import Neo4jClient
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.observability.logging import get_logger

logger = get_logger("verify")


class VerificationResult:
    """
    Structured validation result for planner feedback.
    
    Attributes:
        valid: Overall validation outcome
        missing_positions: Position names not found in graph
        illegal_edges: Invalid moves (no :ONLY_ALLOWED_MOVE_TO edge)
        unsupported_routines: Routine-position combinations without :SUPPORTED_AT
        tool_conflicts: Tool requirements vs current tool state mismatches
        feedback: Human-readable explanation for planner repair
    """
    
    def __init__(self):
        self.valid: bool = True
        self.missing_positions: List[str] = []
        self.illegal_edges: List[tuple] = []
        self.unsupported_routines: List[tuple] = []
        self.tool_conflicts: List[str] = []
        self.feedback: List[str] = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging and LangGraph state."""
        return {
            "valid": self.valid,
            "missing_positions": self.missing_positions,
            "illegal_edges": [{"from": a, "to": b} for a, b in self.illegal_edges],
            "unsupported_routines": [{"routine": r, "position": p} for r, p in self.unsupported_routines],
            "tool_conflicts": self.tool_conflicts,
            "feedback": "\n".join(self.feedback),
        }


def verify_plan(plan: List[Dict[str, Any]], correlation_id: str) -> VerificationResult:
    """
    Validate task plan against graph constraints and robot state.
    
    Validation checks:
    1. All positions exist in Neo4j
    2. All moves traverse :ONLY_ALLOWED_MOVE_TO edges (whitelist)
    3. All routines have :SUPPORTED_AT relationships at target positions
    4. Tool requirements match robot state (if tool_attach/tool_release present)
    
    Args:
        plan: List of task steps (each dict with action, target, optional position)
        correlation_id: UUIDv4 for request tracing
    
    Returns:
        VerificationResult with validation outcome and structured feedback
    
    Example:
        >>> plan = [
        ...     {"action": "move", "target": "<home_position>"},
        ...     {"action": "move", "target": "<position_1>"},
        ...     {"action": "routine", "target": "<routine_name>", "position": "<position_2>"}
        ... ]
        >>> result = verify_plan(plan, "abc-123")
        >>> result.valid
        True
    """
    result = VerificationResult()
    
    logger.info("Starting plan verification", correlation_id=correlation_id, step_count=len(plan))
    
    # Connect to knowledge sources
    neo4j = Neo4jClient()
    neo4j.connect()
    
    state_db = RobotStateDB()
    robot_state = state_db.get_state()
    current_position = robot_state["current_position"]
    current_tool = robot_state["current_tool"]
    
    try:
        # Get graph data for validation
        all_positions = {p["name"]: p["role"] for p in neo4j.get_all_positions()}
        all_routines = {r["name"]: r["required_tool"] for r in neo4j.get_all_routines()}
        
        # Get tool locations to prevent wrong-tool-at-toolstand collisions
        tool_locations = neo4j.get_tool_locations()
        # Build reverse mapping: position_name → tool_name
        position_to_tool = {pos: tool for tool, pos in tool_locations.items()}
        
        # Track simulated state during plan execution
        simulated_position = current_position
        simulated_tool = current_tool
        
        # Validate each step
        for i, step in enumerate(plan, start=1):
            action = step.get("action")
            target = step.get("target")
            position = step.get("position")  # For routines
            
            if action == "move":
                # Check 1: Target position exists
                if target not in all_positions:
                    result.valid = False
                    result.missing_positions.append(target)
                    result.feedback.append(f"Step {i}: Position '{target}' does not exist in graph")
                    logger.warning("Invalid position", correlation_id=correlation_id, step=i, position=target)
                    continue
                
                # Check 2: Tool stand collision prevention
                # Cannot move to a tool position while holding a DIFFERENT tool
                if target in position_to_tool:
                    tool_at_target = position_to_tool[target]
                    if simulated_tool != "none" and simulated_tool != tool_at_target:
                        result.valid = False
                        result.tool_conflicts.append(f"Step {i}: Cannot move to '{target}' (tool stand for '{tool_at_target}') while holding '{simulated_tool}'")
                        result.feedback.append(f"Step {i}: Collision risk - must release '{simulated_tool}' before approaching '{tool_at_target}' tool stand")
                        logger.warning("Tool stand collision risk", correlation_id=correlation_id, step=i, target=target, holding=simulated_tool, stand_tool=tool_at_target)
                        continue
                
                # Check 3: Work position compatibility with current tool
                # If moving to a work position with a tool, ensure at least one routine using that tool is supported there
                if all_positions[target] == "work" and simulated_tool != "none":
                    # Find routines that require this tool
                    routines_for_tool = [r_name for r_name, req_tool in all_routines.items() if req_tool == simulated_tool]
                    
                    # Check if ANY of these routines are supported at target position
                    has_supported_routine = False
                    for routine_name in routines_for_tool:
                        if neo4j.get_routine_metadata(routine_name, target):
                            has_supported_routine = True
                            break
                    
                    if not has_supported_routine:
                        result.valid = False
                        result.tool_conflicts.append(f"Step {i}: Cannot move to '{target}' with tool '{simulated_tool}' - no supported routines for this tool at this position")
                        result.feedback.append(f"Step {i}: Position '{target}' does not support any routines using '{simulated_tool}' (no :SUPPORTED_AT edges)")
                        logger.warning("Incompatible tool at work position", correlation_id=correlation_id, step=i, position=target, tool=simulated_tool, checked_routines=routines_for_tool)
                        continue
                
                # Check 4: Edge whitelist
                if not neo4j.is_move_allowed(simulated_position, target):
                    result.valid = False
                    result.illegal_edges.append((simulated_position, target))
                    result.feedback.append(f"Step {i}: No :ONLY_ALLOWED_MOVE_TO edge from '{simulated_position}' to '{target}'")
                    logger.warning("Illegal edge", correlation_id=correlation_id, step=i, from_pos=simulated_position, to_pos=target)
                else:
                    # Update simulated position
                    simulated_position = target
            
            elif action == "routine":
                # Check 1: Routine exists
                if target not in all_routines:
                    result.valid = False
                    result.feedback.append(f"Step {i}: Routine '{target}' does not exist in graph")
                    logger.warning("Invalid routine", correlation_id=correlation_id, step=i, routine=target)
                    continue
                
                # Check 2: Position exists (for work routines)
                if position and position not in all_positions:
                    result.valid = False
                    result.missing_positions.append(position)
                    result.feedback.append(f"Step {i}: Position '{position}' for routine does not exist")
                    logger.warning("Invalid routine position", correlation_id=correlation_id, step=i, position=position)
                    continue
                
                # Check 3: :SUPPORTED_AT relationship (if position specified)
                if position:
                    metadata = neo4j.get_routine_metadata(target, position)
                    if not metadata:
                        result.valid = False
                        result.unsupported_routines.append((target, position))
                        result.feedback.append(f"Step {i}: Routine '{target}' not supported at '{position}' (no :SUPPORTED_AT edge)")
                        logger.warning("Unsupported routine", correlation_id=correlation_id, step=i, routine=target, position=position)
                
                # Check 4: Tool requirements
                required_tool = all_routines[target]
                if required_tool != "none" and simulated_tool != required_tool:
                    result.valid = False
                    result.tool_conflicts.append(f"Step {i}: Routine '{target}' requires tool '{required_tool}', but robot has '{simulated_tool}'")
                    result.feedback.append(f"Step {i}: Tool mismatch - need '{required_tool}', have '{simulated_tool}'")
                    logger.warning("Tool conflict", correlation_id=correlation_id, step=i, required=required_tool, current=simulated_tool)
                
                # Update simulated tool state (for tool_attach/tool_release)
                if target == "tool_attach":
                    # Safety check: Cannot attach tool if already holding one
                    if simulated_tool != "none":
                        result.valid = False
                        result.tool_conflicts.append(f"Step {i}: Cannot attach tool - robot already holding '{simulated_tool}'")
                        result.feedback.append(f"Step {i}: Must release '{simulated_tool}' before attaching another tool")
                        logger.warning("Tool attach conflict", correlation_id=correlation_id, step=i, current_tool=simulated_tool)
                    else:
                        # Query Neo4j to find which tool is at this position
                        tool_locations = neo4j.get_tool_locations()
                        # Reverse lookup: position → tool name
                        for tool_name, tool_position in tool_locations.items():
                            if tool_position == position:
                                simulated_tool = tool_name
                                break
                elif target == "tool_release":
                    # Safety check: Cannot release if not holding anything
                    if simulated_tool == "none":
                        result.valid = False
                        result.tool_conflicts.append(f"Step {i}: Cannot release tool - robot not holding any tool")
                        result.feedback.append(f"Step {i}: No tool to release")
                        logger.warning("Tool release conflict", correlation_id=correlation_id, step=i)
                    else:
                        simulated_tool = "none"
        
        # Log final result
        if result.valid:
            logger.info("Plan verification passed", correlation_id=correlation_id)
        else:
            logger.warning("Plan verification failed", correlation_id=correlation_id, issues=result.to_dict())
    
    finally:
        neo4j.close()
    
    return result
