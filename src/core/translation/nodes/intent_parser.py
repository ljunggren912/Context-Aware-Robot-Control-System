"""
Intent Parser Node (Simplified LLM)
LLM focuses ONLY on understanding what the user wants, not HOW to achieve it.
"""

import os
import json
from typing import Dict, Any
from src.core.translation.state import WorkflowState
from src.core.knowledge.neo4j_client import Neo4jClient
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.llm.client import LLMClient
from src.core.observability.logging import get_logger

logger = get_logger("intent_parser")


def parse_intent_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Parse operator input into structured intent using LLM.
    
    Args:
        state: WorkflowState containing:
            - operator_input: Command to parse
            - correlation_id: Run identifier
            - human_comments: Optional revision feedback
            - validation_errors: Optional previous planning errors
    
    Returns:
        Dict with parsed intent in standardized format:
            {"goal": "move", "position": "Station_5"}
            {"goal": "execute_routine", "routine": "scan_routine", "position": "Area_B"}
            {"goal": "attach_tool", "tool": "Gripper"}
            {"goal": "release_tool"}
            {"goal": "sequence", "steps": [...]}
    """
    correlation_id = state["correlation_id"]
    operator_input = state["operator_input"]
    human_comments = state.get("human_comments")
    validation_errors = state.get("validation_errors")
    
    logger.info("Parsing intent from operator input",
               correlation_id=correlation_id,
               input=operator_input,
               has_revision=bool(human_comments),
               has_errors=bool(validation_errors))
    
    context = _build_minimal_context()
    
    prompt = _build_intent_prompt(operator_input, context, human_comments, validation_errors)
    
    # Use unified LLM client (no max_tokens - allow full sequence output)
    llm_client = LLMClient()
    intent_json = llm_client.generate(prompt, correlation_id, temperature=0.1)
    
    try:
        clean_json = _extract_json_from_response(intent_json)
        intent = json.loads(clean_json)
        logger.info("Intent parsed successfully",
                   correlation_id=correlation_id,
                   intent=intent)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse intent JSON",
                    correlation_id=correlation_id,
                    error=str(e),
                    raw_response=intent_json[:200])  # Log first 200 chars
        intent = {"goal": "unknown"}
    
    return {
        "intent": intent,
    }


def _extract_json_from_response(text: str) -> str:
    """
    Extract JSON from LLM response that might have markdown or commentary.
    
    Handles:
    - ```json ... ```
    - ```{...}```
    - Plain {...}
    - "Here's the JSON: {...}"
    """
    if not text:
        return "{}"
    
    text = text.strip()
    
    # Remove markdown code blocks
    if text.startswith("```"):
        # Find the actual JSON content between ``` markers
        lines = text.split("\n")
        start_idx = 0
        end_idx = len(lines)
        
        # Skip first line if it's ```json or ```
        if lines[0].startswith("```"):
            start_idx = 1
        
        # Find closing ```
        for i in range(len(lines) - 1, start_idx, -1):
            if lines[i].strip() == "```":
                end_idx = i
                break
        
        text = "\n".join(lines[start_idx:end_idx]).strip()
    
    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    
    if start != -1 and end != -1 and start < end:
        return text[start:end + 1]
    
    # If no JSON found, return empty object
    return "{}"


def _build_minimal_context() -> Dict[str, Any]:
    """
    Build context for intent parsing including positions, tools, routines, and recent history.
    
    Returns:
        Dict containing robot state, available resources, and last completed task
    """
    neo4j = Neo4jClient()
    neo4j.connect()
    
    try:
        positions = neo4j.get_all_positions()
        tools = neo4j.get_all_tools()
        routines = neo4j.get_all_routines()
        
        state_db = RobotStateDB()
        robot_state = state_db.get_state()
        
        # Get recent history for "repeat" commands
        from src.core.knowledge.sqlite_client import HistoryDB
        from datetime import datetime, timedelta
        history_db = HistoryDB()
        
        recent_runs = history_db.get_runs_by_date(datetime.now().strftime("%Y-%m-%d"))
        if not recent_runs:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            recent_runs = history_db.get_runs_by_date(yesterday)
        
        # Get last completed run
        last_run = None
        if recent_runs:
            for run in reversed(recent_runs):  # Most recent first
                if run['status'] == 'completed':
                    last_run = {
                        'command': run['operator_input'],
                        'run_id': run['run_id']
                    }
                    break
        
        return {
            "robot_position": robot_state["current_position"],
            "robot_tool": robot_state["current_tool"],
            "positions": [{"name": p["name"], "role": p["role"]} for p in positions],
            "tools": [{"name": t["name"]} for t in tools],
            "routines": [{"name": r["name"], "required_tool": r.get("required_tool")} for r in routines],
            "last_run": last_run,
        }
    finally:
        neo4j.close()


def _build_intent_prompt(operator_input: str, context: Dict[str, Any], human_comments: str = None, validation_errors: str = None) -> str:
    """
    Build LLM prompt for intent extraction.
    
    Args:
        operator_input: Operator command to parse
        context: Robot state and available resources
        human_comments: Optional revision feedback from operator
        validation_errors: Optional previous planning errors
    
    Returns:
        Formatted prompt string for LLM
    """
    prompt = f"""You are an intent parser for a robot system. Convert the operator's command into a structured intent.

**Current Robot State:**
- Position: {context['robot_position']}
- Tool: {context['robot_tool']}

**Available Positions:**
{json.dumps(context['positions'], indent=2)}

**Available Tools:**
{json.dumps(context['tools'], indent=2)}

**Available Routines:**
{json.dumps(context['routines'], indent=2)}"""

    # Add last run context if available
    if context.get('last_run'):
        prompt += f"""

**Last Completed Task:**
Command: "{context['last_run']['command']}"
Run ID: {context['last_run']['run_id']}

NOTE: If the operator says "again", "repeat", "do it again", "run the latest task again", etc., 
you should parse it as the SAME intent as the last completed task above."""

    prompt += f"""

**Operator Command:** {operator_input}

**IMPORTANT - How to handle "full" or "all" keywords:**
When the operator says "full" or "all" (e.g., "do a full scan", "process all stations"), you MUST create steps for EVERY work position listed above.
For example, if Available Positions shows Station_A, Station_B, and Station_C, then "full scan" means:
- Scan at Station_A
- Scan at Station_B  
- Scan at Station_C

Do NOT pick just one position when "full" or "all" is specified.
"""
    
    # Add human feedback if this is a revision
    if human_comments:
        prompt += f"""

**CRITICAL - Human Revision Request:**
The operator reviewed your previous plan and wants changes.

Original command: "{operator_input}"
Human feedback: "{human_comments}"

Your task: Re-parse the ORIGINAL command ("{operator_input}") but APPLY the human's feedback.

Examples:
- Original: "visit all positions" + Feedback: "dont attach tools" → Parse as simple movement to all positions (NO tool operations)
- Original: "go to pos 1" + Feedback: "also go to pos 2" → Parse as sequence visiting both positions
- Original: "inspect pos 3" + Feedback: "use the camera" → Parse as camera inspection at pos 3

IMPORTANT: The feedback MODIFIES the original command. You must understand what the original command meant, then adjust it based on feedback.
"""
    
    if validation_errors:
        prompt += f"""

**IMPORTANT - Previous Planning Error:**
The last attempt to build a plan failed with this error:
"{validation_errors}"

Please adjust your intent parsing to avoid this error. Consider:
- Are you using correct position/tool/routine names from the lists above?
- Are you following the format rules correctly?
- Does the requested action make sense given the available resources?
"""
    
    prompt += """

**Your Task:** Parse the command into ONE of these intent types.

**═══════════════════════════════════════════════════════════════════════════**
**MOST CRITICAL RULE - READ THIS FIRST:**
**═══════════════════════════════════════════════════════════════════════════**

DO NOT ADD TOOL OPERATIONS UNLESS THE OPERATOR EXPLICITLY SAYS SO.

The system AUTOMATICALLY handles tool picking, dropping, and changing.
YOU ONLY parse what the operator EXPLICITLY requested.

Commands like these are MOVEMENT ONLY (NO tools):
- "visit all positions" → ONLY movement, NO tool_attach
- "go to position 1" → ONLY movement, NO tool_attach  
- "move to position 2" → ONLY movement, NO tool_attach
- "tour the work area" → ONLY movement, NO tool_attach

Tool operations are ONLY when explicitly mentioned:
- "grab the welder" → YES, tool attach
- "pick up camera" → YES, tool attach
- "return the tool" → YES, tool release

**═══════════════════════════════════════════════════════════════════════════**

WRONG EXAMPLES (DO NOT DO THIS):
User: "visit all positions"
WRONG: {{"goal": "sequence", "steps": [
  {{"action": "move", "position": "<position_1>"}},
  {{"action": "routine", "routine": "tool_attach", "position": "<position_1>"}},  ← WRONG! Not requested!
  ...
]}}
CORRECT: {{"goal": "sequence", "steps": [
  {{"action": "move", "position": "<position_1>"}},
  {{"action": "move", "position": "<position_2>"}},
  {{"action": "move", "position": "<position_3>"}}
]}}

User: "go to position 1"
WRONG: Including tool_attach
CORRECT: {{"goal": "move", "position": "<position_1>"}}

User: "move to position 1 and back home"
WRONG: Including tool operations
CORRECT: {{"goal": "sequence", "steps": [
  {{"action": "move", "position": "<position_1>"}}, 
  {{"action": "move", "position": "<home_position>"}}
]}}

**═══════════════════════════════════════════════════════════════════════════**

**CRITICAL PARSING PRECEDENCE** (check in this order):

1. **POSITION EXPLICITLY SPECIFIED** → Limit to that position ONLY
   - Keywords: "pos", "position", "at", followed by identifier
   - "routine at station 5" → ONLY station 5, even if other stations exist
   - "weld and inspect pos 1" → ONLY pos 1 for BOTH actions
   - "scan area 3" → ONLY area 3
   
2. **MULTIPLE POSITIONS LISTED** → Limit to listed positions ONLY  
   - "routine at station 1 and 2" → ONLY those two stations
   - "inspect areas 1, 2, and 3" → ONLY those three areas
   
3. **"FULL" or "ALL" KEYWORD** → Apply to EVERY SINGLE work position available
   - **THIS IS THE MOST COMMON MISTAKE**: When you see "full" or "all", you MUST create a step for EACH work position
   - "do a full inspection" → Create one step for EVERY work position listed in Available Positions
   - "scan all areas" → Create one step for EVERY work position 
   - "process all stations" → Create a step for Station_A, Station_B, Station_C, etc. (ALL of them)
   - **CRITICAL**: Do NOT pick just one position when "full" is specified - include ALL work positions
   
4. **NO POSITION AND NO "FULL"** → Use "unknown" goal (need clarification)

**EXAMPLE - "FULL" KEYWORD WITH MULTIPLE POSITIONS:**
Available Positions: [{{"name": "Station_A", "role": "work"}}, {{"name": "Station_B", "role": "work"}}, {{"name": "Station_C", "role": "work"}}]
User: "do a full scan"

CORRECT output (includes ALL stations):
{{
  "goal": "sequence", 
  "steps": [
    {{"action": "routine", "routine": "scanner_routine", "position": "Station_A"}},
    {{"action": "routine", "routine": "scanner_routine", "position": "Station_B"}},
    {{"action": "routine", "routine": "scanner_routine", "position": "Station_C"}}
  ]
}}

WRONG output (only includes one station):
{{"goal": "routine", "routine": "scanner_routine", "position": "Station_B"}}  ← Missing Station_A and Station_C!

**Intent Type Formats:**

1. **Simple Movement**
   Format: {{"goal": "move", "position": "<position_name>"}}
   Generic Examples: "go to station 5", "move to home", "navigate to checkpoint A"

2. **Execute Routine**
   Format: {{"goal": "execute_routine", "routine": "<routine_name>", "position": "<position_name>"}}
   Generic Examples: "scan at station 2", "process at area B", "measure at checkpoint 3"

3. **Attach Tool**
   Format: {{"goal": "attach_tool", "tool": "<tool_name>"}}
   Generic Examples: "grab the scanner", "pick up gripper", "attach drill"

4. **Release Tool**
   Format: {{"goal": "release_tool"}}
   Generic Examples: "return the tool", "put back scanner", "release gripper"

5. **Release Tool and Return Home**
   Format: {{"goal": "release_tool_and_home"}}
   Generic Examples: "return tool and go home", "put back drill and go to home position"

6. **Multi-Step Sequence**
   Format: {{"goal": "sequence", "steps": [
       {{"action": "routine", "routine": "<routine_name>", "position": "<position_name>"}},
       {{"action": "routine", "routine": "<routine_name>", "position": "<position_name>"}},
       {{"action": "release_tool_and_home"}}
   ]}}
   
   Generic Examples (demonstrating precedence):
   - "scan station 2 then drill it" → ONLY station 2 (position specified)
   - "process areas 1 and 3" → ONLY areas 1 and 3 (multiple listed)
   - "do a full scan" → ALL work positions (full keyword)
   - "scan and drill station 5" → ONLY station 5 for BOTH actions (position specified)

**CRITICAL RULES:**
- Use EXACT names from available positions/tools/routines lists above
- **NEVER EVER include tool attach/release/change steps UNLESS explicitly requested by the operator**
- **The system automatically handles tool operations - you only parse what the operator asked for**
- **ONLY include the actual work routines or movements that the operator explicitly requested**
- Examples of what to NEVER include (unless operator says so): tool_attach, tool_release, pick_tool, drop_tool, tool changes, navigation to tool stands
- **Simple movement commands = ONLY movement, NO TOOL OPERATIONS**
- For compound commands ("and then", "after that") → use "sequence" goal
- Return ONLY valid JSON, no explanatory text
- If command is ambiguous or unclear, use {{"goal": "unknown"}}
- **REMEMBER**: "full" or "all" means you must create a step for EVERY work position - not just one

**Example - What to include vs exclude:**
User: "do a full inspection"
CORRECT: {{"goal": "sequence", "steps": [
  {{"action": "routine", "routine": "<routine_name>", "position": "<position_1>"}},
  {{"action": "routine", "routine": "<routine_name>", "position": "<position_2>"}},
  {{"action": "routine", "routine": "<routine_name>", "position": "<position_3>"}}
]}}
WRONG: Including any tool_attach, tool_release, or moves to tool stands

User: "go to position 1 and back home"
CORRECT: {{"goal": "sequence", "steps": [
  {{"action": "move", "position": "<position_1>"}},
  {{"action": "move", "position": "<home_position>"}}
]}}
WRONG: Including tool_attach, tool_release, or any routine steps (operator only asked for movement!)

**Return JSON only:**
"""
    return prompt
