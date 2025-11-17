"""
Question Node
Answers information queries using LLM + knowledge graph.
Uses LLM to understand query intent and generate natural language responses.
See docs/translation/README.md for query patterns.
"""

import os
import json
from typing import Dict, Any
from src.core.translation.state import WorkflowState
from src.core.llm.client import LLMClient
from src.core.knowledge.neo4j_client import Neo4jClient
from src.core.knowledge.sqlite_client import RobotStateDB, HistoryDB
from src.core.observability.logging import get_logger
from datetime import datetime, timedelta

logger = get_logger("question")


def question_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Process information queries using LLM with knowledge layer context.
    
    Args:
        state: WorkflowState containing:
            - operator_input: Natural language question
            - correlation_id: Run identifier
    
    Returns:
        Dict with response containing natural language answer based on:
            - Neo4j graph data (positions, tools, routines, allowed moves)
            - Robot state (current position, tool)
            - History database (past runs)
    """
    correlation_id = state["correlation_id"]
    operator_input = state["operator_input"]
    
    logger.info("Processing question with LLM", correlation_id=correlation_id)
    
    # Gather all knowledge for LLM context
    neo4j = Neo4jClient()
    neo4j.connect()
    
    state_db = RobotStateDB()
    history_db = HistoryDB()
    
    try:
        # Collect knowledge snapshot
        positions = neo4j.get_all_positions()
        tools = neo4j.get_all_tools()
        routines = neo4j.get_all_routines()
        robot_state = state_db.get_state()
        
        # Build allowed moves map
        allowed_moves = {}
        for pos in positions:
            allowed_moves[pos["name"]] = neo4j.get_allowed_moves(pos["name"])
        
        # Get recent history (limit to prevent token overflow)
        MAX_HISTORY_RECORDS = 15
        
        recent_runs = history_db.get_runs_by_date(datetime.now().strftime("%Y-%m-%d"))
        if not recent_runs:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            recent_runs = history_db.get_runs_by_date(yesterday)
        
        # Limit to most recent N runs
        if recent_runs and len(recent_runs) > MAX_HISTORY_RECORDS:
            recent_runs = recent_runs[:MAX_HISTORY_RECORDS]
        
        # Call LLM to answer question
        response = _answer_question_with_llm(
            operator_input=operator_input,
            positions=positions,
            tools=tools,
            routines=routines,
            robot_state=robot_state,
            allowed_moves=allowed_moves,
            recent_runs=recent_runs if recent_runs else [],
            correlation_id=correlation_id
        )
        
        logger.info("Question answered", correlation_id=correlation_id)
        
        return {
            "response": response,
        }
    
    finally:
        neo4j.close()


def _answer_question_with_llm(
    operator_input: str,
    positions: list,
    tools: list,
    routines: list,
    robot_state: dict,
    allowed_moves: dict,
    recent_runs: list,
    correlation_id: str
) -> str:
    """
    Use LLM to answer operator question with knowledge context.
    
    Args:
        operator_input: Natural language question
        positions: List of available positions from Neo4j
        tools: List of available tools from Neo4j
        routines: List of available routines from Neo4j
        robot_state: Current robot position and tool
        allowed_moves: Dict mapping positions to their allowed next moves
        recent_runs: Recent run history
        correlation_id: For logging
    
    Returns:
        Natural language answer to question
    """
    import time
    
    # Build comprehensive context for LLM
    context = _build_knowledge_context(
        positions, tools, routines, robot_state, allowed_moves, recent_runs
    )
    
    # Build prompt
    prompt = f"""You are a helpful assistant for a context-aware robot control system.

SYSTEM KNOWLEDGE:
{context}

OPERATOR QUESTION:
"{operator_input}"

Your task: Provide a clear, concise answer to the operator's question using the system knowledge above.

Guidelines:
- Answer in natural language (not JSON or technical format)
- Be helpful and conversational
- If asking about positions/tools/routines, list them clearly
- If asking about current state, provide the current position and tool
- If asking about history, respect the operator's requested quantity UP TO THE AVAILABLE DATA
  (e.g., "10 latest" = show 10 if available, "50 latest" = show up to 50 if available)
- If operator asks for more than available (e.g., "show 100" but only 50 in context), show what you have
- If no specific number requested for history, show a reasonable summary (5-10 recent runs)
- **HANDLE IMPLICIT CONTEXT**: If question references "latest", "more", or "the rest" without specifying WHAT,
  assume they're asking about task history (most common follow-up question)
- **DO NOT SHOW TECHNICAL IDs**: Hide run_id/correlation_id UUIDs - they are internal tracking only
  Show task descriptions in plain language (e.g., "move to all positions" not "66197bd2-8213...")
- If the question cannot be answered with available knowledge, politely explain what you CAN help with
- Use bullet points for lists (indented with "  - ")
- Handle synonyms naturally (e.g., "points" = "positions", "spots" = "positions")

**CRITICAL - DO NOT ASK FOLLOW-UP QUESTIONS:**
This is an INFORMATION-ONLY node. You are answering questions, NOT executing tasks.
- DO NOT ask "Would you like me to proceed?"
- DO NOT ask "Should I execute this?"
- DO NOT ask "Do you want me to do X?"
Just provide the factual answer. The operator will give a new command if they want action.

Examples:
WRONG: "The first task was X. Would you like me to proceed?"
RIGHT: "The first task was X. To repeat it, say 'move to all positions'."

Respond with ONLY your answer (no preamble like "Here's the answer:").
"""

    start_time = time.time()
    
    try:
        # Use unified LLM client
        llm_client = LLMClient()
        answer = llm_client.generate(prompt, correlation_id, temperature=0.3, max_tokens=800)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        logger.info("LLM question answering complete",
                   correlation_id=correlation_id,
                   duration_ms=duration_ms)
        
        return answer
    
    except Exception as e:
        logger.error("LLM question answering failed",
                    correlation_id=correlation_id,
                    error=str(e))
        
        # Fallback response
        return ("I'm having trouble processing your question right now. "
                "Please try asking about available positions, tools, routines, "
                "or current robot status.")


def _build_knowledge_context(
    positions: list,
    tools: list,
    routines: list,
    robot_state: dict,
    allowed_moves: dict,
    recent_runs: list
) -> str:
    """
    Build formatted knowledge context for LLM prompt.
    
    Returns:
        Multi-line string with all system knowledge
    """
    lines = []
    
    # Positions
    lines.append("AVAILABLE POSITIONS:")
    for pos in positions:
        lines.append(f"  - {pos['name']} (role: {pos['role']})")
        lines.append(f"    Description: {pos['description']}")
        moves = allowed_moves.get(pos['name'], [])
        if moves:
            lines.append(f"    Can move to: {', '.join(moves)}")
    
    # Tools
    lines.append("\nAVAILABLE TOOLS:")
    for tool in tools:
        lines.append(f"  - {tool['name']}: {tool['description']}")
    
    # Routines
    lines.append("\nAVAILABLE ROUTINES:")
    for routine in routines:
        tool_req = routine['required_tool'] if routine['required_tool'] != 'none' else 'no tool required'
        lines.append(f"  - {routine['name']} ({tool_req})")
        lines.append(f"    Description: {routine['description']}")
    
    # Current state
    lines.append("\nCURRENT ROBOT STATE:")
    lines.append(f"  - Position: {robot_state['current_position']}")
    lines.append(f"  - Tool: {robot_state['current_tool']}")
    lines.append(f"  - Last updated: {robot_state['last_updated']}")
    
    # Recent history
    if recent_runs:
        lines.append("\nRECENT TASK HISTORY:")
        for run in recent_runs:
            lines.append(f"  - {run['run_id']}: \"{run['operator_input']}\" → {run['status']}")
    
    return "\n".join(lines)
