"""
Sequence Builder

Converts high-level intents into concrete step sequences using graph algorithms.

This module implements deterministic planning - no LLM guessing, only:
  - Neo4j graph queries for pathfinding
  - Logical tool management rules
  - State simulation to insert necessary steps

The separation between intent parsing (LLM) and sequence building (graph algorithms)
improves reliability and makes the system easier to test and debug.
"""

from typing import Dict, List, Any, Optional
from src.core.knowledge.neo4j_client import Neo4jClient
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.observability.logging import get_logger

logger = get_logger("sequence_builder")


class SequenceBuilder:
    """
    Builds concrete step sequences from high-level intents using graph algorithms.
    
    Architecture:
      - LLM parses natural language into structured intent (WHAT the user wants)
      - This class plans execution details (HOW to achieve it)
      - Uses Neo4j for pathfinding between positions
      - Implements deterministic tool change logic
      - Simulates robot state to insert necessary intermediate steps
    """
    
    def __init__(self):
        self.neo4j = Neo4jClient()
        self.neo4j.connect()
        self.state_db = RobotStateDB()
    
    def close(self):
        """Close database connections."""
        self.neo4j.close()
    
    def build_sequence(self, intent: Dict[str, Any], correlation_id: str) -> List[Dict[str, Any]]:
        """
        Convert high-level intent into concrete step sequence.
        
        This method expands a high-level intent (e.g., "execute routine at position X")
        into all necessary atomic steps, including:
          - Navigation paths between positions
          - Tool changes when required
          - Actual work routines
        
        Args:
            intent: High-level goal from LLM with structure:
                - {"goal": "move", "position": "<position_name>"}
                - {"goal": "execute_routine", "routine": "<routine>", "position": "<position>"}
                - {"goal": "attach_tool", "tool": "<tool_name>"}
                - {"goal": "release_tool"}
                - {"goal": "sequence", "steps": [<list of high-level steps>]}
            correlation_id: Run identifier for logging
        
        Returns:
            List of concrete steps with sequential IDs:
            [
                {"id": 1, "name": "Move to X", "action": "move", "target": "Position_X"},
                {"id": 2, "name": "Routine at Y", "action": "routine", "target": "routine_name", 
                 "position": "Position_Y"}
            ]
        
        Algorithm:
            1. Convert intent into list of high-level steps
            2. Initialize simulated robot state (position, tool)
            3. For each high-level step:
               a. Insert tool changes if needed (release old, attach new)
               b. Insert navigation path to target position
               c. Add the actual work step (routine/tool operation)
            4. Return sequence with sequential IDs starting from 1
        """
        goal = intent.get("goal")
        
        # Convert all intent types to list of steps
        if goal == "move":
            steps = [{"action": "move", "position": intent["position"]}]
        
        elif goal == "execute_routine":
            steps = [{"action": "routine", "routine": intent["routine"], "position": intent["position"]}]
        
        elif goal == "attach_tool":
            steps = [{"action": "attach_tool", "tool": intent["tool"]}]
        
        elif goal == "release_tool":
            steps = [{"action": "release_tool"}]
        
        elif goal == "sequence":
            steps = intent.get("steps", [])
        
        else:
            raise ValueError(f"Unknown goal type: {goal}")
        
        logger.info("Building step sequence",
                   correlation_id=correlation_id,
                   high_level_steps=len(steps))
        
        # Initialize simulated robot state for planning
        robot_state = self.state_db.get_state()
        simulated_position = robot_state["current_position"]
        simulated_tool = robot_state["current_tool"]
        
        all_steps = []
        step_id = 1
        
        # Process each high-level step and expand into atomic steps
        for high_level_step in steps:
            action = high_level_step.get("action")
            
            # Handle routine execution (LLM may use "routine" or "execute_routine")
            if action in ["routine", "execute_routine"]:
                # Execute routine at position
                routine_name = high_level_step["routine"]
                target_position = high_level_step["position"]
                
                # Fetch routine information from knowledge graph
                routine_info = self.neo4j.get_routine_by_name(routine_name)
                if not routine_info:
                    logger.warning("Unknown routine in sequence",
                                  correlation_id=correlation_id,
                                  routine=routine_name)
                    continue
                
                # Validate routine is supported at this position
                supported_positions = self.neo4j.get_supported_positions(routine_name)
                if target_position not in supported_positions:
                    logger.error("Routine not supported at position - rejecting step",
                               correlation_id=correlation_id,
                               routine=routine_name,
                               requested_position=target_position,
                               supported_positions=supported_positions)
                    raise ValueError(
                        f"Routine '{routine_name}' is not supported at position '{target_position}'. "
                        f"Valid positions: {supported_positions}"
                    )
                
                required_tool = routine_info.get("required_tool")
                
                # Step 1: Tool change if needed
                if required_tool and required_tool != "none" and simulated_tool != required_tool:
                    # Need to change tools
                    if simulated_tool != "none":
                        # First, release current tool at its storage location
                        tool_loc = self.neo4j.get_tool_locations().get(simulated_tool)
                        if tool_loc:
                            # Navigate to tool storage location if not already there
                            if simulated_position != tool_loc:
                                path = self.neo4j.get_shortest_path(simulated_position, tool_loc)
                                if path:
                                    for pos in path[1:]:
                                        all_steps.append({
                                            "id": step_id,
                                            "name": f"Move to {pos}",
                                            "action": "move",
                                            "target": pos
                                        })
                                        step_id += 1
                                        simulated_position = pos
                            
                            # Add tool release step with metadata from knowledge graph
                            release_step = {
                                "id": step_id,
                                "name": f"Release {simulated_tool}",
                                "action": "routine",
                                "target": "tool_release",
                                "position": tool_loc,
                                "tool": simulated_tool
                            }
                            metadata = self.neo4j.get_routine_metadata("tool_release", tool_loc)
                            if metadata:
                                if "stabilize" in metadata and metadata["stabilize"]:
                                    release_step["stabilize"] = metadata["stabilize"]
                                if "action_after" in metadata and metadata["action_after"]:
                                    release_step["action_after"] = metadata["action_after"]
                                if "verify" in metadata and metadata["verify"]:
                                    release_step["verify"] = metadata["verify"]
                            
                            all_steps.append(release_step)
                            step_id += 1
                            simulated_tool = "none"
                    
                    # Second, attach required tool from its storage location
                    tool_loc = self.neo4j.get_tool_locations().get(required_tool)
                    if tool_loc:
                        if simulated_position != tool_loc:
                            path = self.neo4j.get_shortest_path(simulated_position, tool_loc)
                            if path:
                                for pos in path[1:]:
                                    all_steps.append({
                                        "id": step_id,
                                        "name": f"Move to {pos}",
                                        "action": "move",
                                        "target": pos
                                    })
                                    step_id += 1
                                    simulated_position = pos
                        
                        # Add tool attach step with metadata from knowledge graph
                        attach_step = {
                            "id": step_id,
                            "name": f"Attach {required_tool}",
                            "action": "routine",
                            "target": "tool_attach",
                            "position": tool_loc,
                            "tool": required_tool 
                        }
                        metadata = self.neo4j.get_routine_metadata("tool_attach", tool_loc)
                        if metadata:
                            if "stabilize" in metadata and metadata["stabilize"]:
                                attach_step["stabilize"] = metadata["stabilize"]
                            if "action_after" in metadata and metadata["action_after"]:
                                attach_step["action_after"] = metadata["action_after"]
                            if "verify" in metadata and metadata["verify"]:
                                attach_step["verify"] = metadata["verify"]
                        
                        all_steps.append(attach_step)
                        step_id += 1
                        simulated_tool = required_tool
                
                # Step 2: Navigate to target position for routine execution
                if simulated_position != target_position:
                    path = self.neo4j.get_shortest_path(simulated_position, target_position)
                    if path:
                        for pos in path[1:]:
                            all_steps.append({
                                "id": step_id,
                                "name": f"Move to {pos}",
                                "action": "move",
                                "target": pos
                            })
                            step_id += 1
                            simulated_position = pos
                
                # Step 3: Execute the routine at target position
                # Fetch routine-specific metadata (stabilize time, verification, etc.)
                metadata = self.neo4j.get_routine_metadata(routine_name, target_position)
                routine_step = {
                    "id": step_id,
                    "name": f"{routine_name.replace('_', ' ').title()} at {target_position}",
                    "action": "routine",
                    "target": routine_name,
                    "position": target_position
                }
                
                # Include metadata fields if they exist in knowledge graph
                if metadata:
                    if "stabilize" in metadata and metadata["stabilize"]:
                        routine_step["stabilize"] = metadata["stabilize"]
                    if "action_after" in metadata and metadata["action_after"]:
                        routine_step["action_after"] = metadata["action_after"]
                    if "verify" in metadata and metadata["verify"]:
                        routine_step["verify"] = metadata["verify"]
                
                all_steps.append(routine_step)
                step_id += 1
            
            elif action == "move":
                # Simple movement
                target_position = high_level_step["position"]
                if simulated_position != target_position:
                    path = self.neo4j.get_shortest_path(simulated_position, target_position)
                    if path:
                        for pos in path[1:]:
                            all_steps.append({
                                "id": step_id,
                                "name": f"Move to {pos}",
                                "action": "move",
                                "target": pos
                            })
                            step_id += 1
                            simulated_position = pos
            
            elif action == "attach_tool":
                # Attach specific tool
                tool_name = high_level_step["tool"]
                tool_loc = self.neo4j.get_tool_locations().get(tool_name)
                if tool_loc:
                    if simulated_position != tool_loc:
                        path = self.neo4j.get_shortest_path(simulated_position, tool_loc)
                        if path:
                            for pos in path[1:]:
                                all_steps.append({
                                    "id": step_id,
                                    "name": f"Move to {pos}",
                                    "action": "move",
                                    "target": pos
                                })
                                step_id += 1
                                simulated_position = pos
                    
                    # Fetch metadata for tool_attach
                    attach_step = {
                        "id": step_id,
                        "name": f"Attach {tool_name}",
                        "action": "routine",
                        "target": "tool_attach",
                        "position": tool_loc,
                        "tool": tool_name
                    }
                    metadata = self.neo4j.get_routine_metadata("tool_attach", tool_loc)
                    if metadata:
                        if "stabilize" in metadata and metadata["stabilize"]:
                            attach_step["stabilize"] = metadata["stabilize"]
                        if "action_after" in metadata and metadata["action_after"]:
                            attach_step["action_after"] = metadata["action_after"]
                        if "verify" in metadata and metadata["verify"]:
                            attach_step["verify"] = metadata["verify"]
                    
                    all_steps.append(attach_step)
                    step_id += 1
                    simulated_tool = tool_name
            
            elif action == "release_tool":
                # Release current tool
                if simulated_tool != "none":
                    tool_loc = self.neo4j.get_tool_locations().get(simulated_tool)
                    if tool_loc:
                        if simulated_position != tool_loc:
                            path = self.neo4j.get_shortest_path(simulated_position, tool_loc)
                            if path:
                                for pos in path[1:]:
                                    all_steps.append({
                                        "id": step_id,
                                        "name": f"Move to {pos}",
                                        "action": "move",
                                        "target": pos
                                    })
                                    step_id += 1
                                    simulated_position = pos
                        
                        # Fetch metadata for tool_release
                        release_step = {
                            "id": step_id,
                            "name": f"Release {simulated_tool}",
                            "action": "routine",
                            "target": "tool_release",
                            "position": tool_loc,
                            "tool": simulated_tool
                        }
                        metadata = self.neo4j.get_routine_metadata("tool_release", tool_loc)
                        if metadata:
                            if "stabilize" in metadata and metadata["stabilize"]:
                                release_step["stabilize"] = metadata["stabilize"]
                            if "action_after" in metadata and metadata["action_after"]:
                                release_step["action_after"] = metadata["action_after"]
                            if "verify" in metadata and metadata["verify"]:
                                release_step["verify"] = metadata["verify"]
                        
                        all_steps.append(release_step)
                        step_id += 1
                        simulated_tool = "none"
        
        logger.info("Step sequence built",
                   correlation_id=correlation_id,
                   high_level_steps=len(steps),
                   concrete_steps=len(all_steps))
        
        # Log the complete sequence for visibility
        logger.info("Complete step sequence",
                   correlation_id=correlation_id,
                   steps=all_steps)
        
        return all_steps
