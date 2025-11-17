"""
Router Node
LLM-based intent classification and correlation ID generation.
Uses LLM to intelligently classify natural language input.
Graph-driven: Queries Neo4j for routines, tools, positions to provide context.
See docs/translation/README.md for workflow diagram.
"""

import os
import uuid
import json
from typing import Dict, Any, List
from src.core.translation.state import WorkflowState
from src.core.llm.client import LLMClient
from src.core.observability.logging import get_logger
from src.core.knowledge.sqlite_client import HistoryDB
from src.core.knowledge.neo4j_client import Neo4jClient

logger = get_logger("router")


def _build_system_capabilities() -> str:
    """
    Query Neo4j to build dynamic description of system capabilities.
    Returns formatted string describing available routines, tools, and positions.
    
    Graph-driven approach ensures Router adapts to changes in knowledge base.
    """
    try:
        with Neo4jClient() as client:
            routines = client.get_all_routines()
            tools = client.get_all_tools()
            positions = client.get_all_positions()
            
            capabilities = []
            capabilities.append(f"Available routines: {', '.join(r['name'] for r in routines)}")
            capabilities.append(f"Available tools: {', '.join(t['name'] for t in tools)}")
            capabilities.append(f"Available positions: {', '.join(p['name'] for p in positions)}")
            
            return "\n".join(capabilities)
    
    except Exception as e:
        logger.error("Failed to query graph for capabilities", error=str(e))
        return "System capabilities unavailable"


def _classify_intent_with_llm(operator_input: str, correlation_id: str) -> str:
    """
    Use LLM to classify operator input intent.
    
    Intent categories:
    - action: Command requiring task planning and robot execution
    - question: Information query about system state or capabilities
    - unknown: Unclear, out-of-scope, or ambiguous input
    
    Args:
        operator_input: Natural language input from operator
        correlation_id: Correlation ID for tracing
    
    Returns:
        Intent classification: "action" | "question" | "unknown"
    """
    import time
    
    # Get system capabilities from graph
    capabilities = _build_system_capabilities()
    
    # Build classification prompt
    prompt = f"""You are an intent classifier for a context-aware robot control system.

SYSTEM CAPABILITIES:
{capabilities}

Your task: Classify the operator's input into ONE of these intents:
1. "action" - Commands that require robot movement, tool operations, or routine execution
   Examples: 
   - Direct commands: "Move to station 5", "Process at area B", "Pick up the gripper tool"
   - Polite requests: "Can you scan checkpoint 3?", "Please weld at position 2"
   - Repeat commands: "Do that again", "Repeat the last task", "Can you do the first task again?"
   - Confirmations: "proceed", "yes", "go ahead", "do it" (after a plan review)
   
2. "question" - Queries about system state, capabilities, or history (NO ACTION REQUESTED)
   Examples: 
   - Information: "What stations are available?", "Where is the robot?", "Show me the tools"
   - History: "What did you do?", "Show me the history", "Give me the task list"
   - Follow-ups: "Tell me more", "What about tools?", "And the routines?"
   - Implicit context: "give me the 15 latest", "show me 10 more", "what about the rest?"
   → These reference previous conversation context and are information queries
   
3. "unknown" - ONLY for completely unclear or out-of-scope requests
   Examples: "Hello", "What's the weather?", "asdfgh"

**CRITICAL DISTINCTION:**
- "Can you do X?" or "Do X again" = ACTION (operator wants execution)
- "What is X?" or "Show me X" = QUESTION (operator wants information)
- "proceed" / "yes" / "go ahead" = ACTION (confirmation after review)

OPERATOR INPUT: "{operator_input}"

Respond with ONLY a JSON object in this exact format:
{{
  "intent": "action|question|unknown",
  "reasoning": "Brief explanation"
}}

Do NOT include any other text, commentary, or markdown formatting."""

    start_time = time.time()
    
    try:
        # Use unified LLM client
        llm_client = LLMClient()
        content = llm_client.generate(prompt, correlation_id, temperature=0.0, max_tokens=150)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Parse LLM response
        classification = json.loads(content.strip())
        intent = classification.get("intent", "unknown")
        reasoning = classification.get("reasoning", "")
        
        # Validate intent value
        if intent not in ["action", "question", "unknown"]:
            logger.warning("Invalid intent from LLM, defaulting to unknown", 
                         correlation_id=correlation_id, 
                         invalid_intent=intent)
            intent = "unknown"
        
        logger.info("LLM intent classification complete",
                   correlation_id=correlation_id,
                   intent=intent,
                   reasoning=reasoning,
                   duration_ms=duration_ms)
        
        return intent
    
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM classification JSON",
                    correlation_id=correlation_id,
                    error=str(e),
                    content=content[:200] if 'content' in locals() else "N/A")
        return "unknown"
    
    except Exception as e:
        logger.error("LLM classification failed, using fallback",
                    correlation_id=correlation_id,
                    error=str(e))
        # Fallback to simple keyword matching
        return _fallback_keyword_classification(operator_input)


def _fallback_keyword_classification(operator_input: str) -> str:
    """
    Simple keyword-based classification as fallback when LLM unavailable.
    Used only when LLM fails - not the primary classification method.
    
    Args:
        operator_input: Operator input (lowercase)
    
    Returns:
        Intent: "action" | "question" | "unknown"
    """
    import re
    operator_input = operator_input.lower()
    
    # Follow-up phrases (treat as questions)
    followup_phrases = [
        "more information", "tell me more", "give me more", "what else", 
        "more details", "explain more", "and what about", "what about",
        "the latest", "the last", "show me", "give me the", "give me"
    ]
    if any(phrase in operator_input for phrase in followup_phrases):
        return "question"
    
    # Check for number + implicit context (e.g., "give me 15")
    # Pattern: "give/show me <number>" with no clear action verb
    if re.search(r'(give|show)\s+me\s+\d+', operator_input):
        return "question"  # Assume asking for N items from history
    
    # Strong question indicators
    question_words = ["what", "where", "which", "how many", "list", "show", "tell me", "explain", "give me"]
    if any(word in operator_input for word in question_words):
        return "question"
    
    # Common action verbs
    action_words = ["move", "go", "weld", "inspect", "attach", "pick", "release", "change"]
    if any(word in operator_input for word in action_words):
        return "action"
    
    return "unknown"


def router_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Classify operator input intent and generate correlation ID.
    
    Intent categories:
    - action: Command requiring task planning (e.g., "Weld at position 1")
    - question: Information query (e.g., "What positions are available?")
    - unknown: Unclear or out-of-scope input
    
    Special cases:
    - "Do that again" / "repeat last task" → Load latest completed run from history
    
    Args:
        state: WorkflowState with operator_input
    
    Returns:
        Updated state with correlation_id, intent, and optionally plan (for replay)
    
    Routing:
    - intent="action" → Sequence Planning node
    - intent="question" → Question node
    - intent="unknown" → Fallback node
    """
    operator_input = state["operator_input"].strip().lower()
    
    # Generate correlation ID for tracing
    correlation_id = str(uuid.uuid4())
    
    logger.info("Router processing input", correlation_id=correlation_id)
    
    # Check for UUID-based replay (e.g., "run task 66197bd2..." or "do the one named 66197bd2...")
    import re
    uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    uuid_match = re.search(uuid_pattern, operator_input)
    
    if uuid_match:
        target_run_id = uuid_match.group(0)
        logger.info("Detected UUID-based replay intent", correlation_id=correlation_id, target_run_id=target_run_id)
        
        # Load specific run from history
        history_db = HistoryDB()
        target_run = history_db.get_run_by_id(target_run_id)
        
        if target_run and target_run["status"] == "completed":
            import json
            plan = json.loads(target_run["sequence_json"])
            
            return {
                "correlation_id": correlation_id,
                "operator_input": f"Replay task: {target_run['operator_input']}",
                "intent": "action",
                "plan": plan,
                "plan_attempt": 1,
            }
        else:
            logger.warning("Run ID not found or not completed", correlation_id=correlation_id, target_run_id=target_run_id)
            return {
                "correlation_id": correlation_id,
                "operator_input": state["operator_input"],
                "intent": "unknown",
                "response": f"Task {target_run_id[:8]}... not found or not completed."
            }
    
    # Check for general replay intent (no specific UUID)
    replay_phrases = ["do that again", "repeat last", "run the same", "do it again"]
    if any(phrase in operator_input for phrase in replay_phrases):
        logger.info("Detected replay intent", correlation_id=correlation_id)
        
        # Load latest completed run from history
        history_db = HistoryDB()
        last_run = history_db.get_latest_completed_run()
        
        if last_run:
            import json
            plan = json.loads(last_run["sequence_json"])
            
            return {
                "correlation_id": correlation_id,
                "operator_input": state["operator_input"],
                "intent": "action",
                "plan": plan,
                "plan_attempt": 1,  # Still requires human approval
            }
        else:
            logger.warning("No completed runs found for replay", correlation_id=correlation_id)
            return {
                "correlation_id": correlation_id,
                "operator_input": state["operator_input"],
                "intent": "unknown",
                "response": "No previous tasks found to repeat. Please describe a new task."
            }
    
    # Classify intent using LLM (graph-aware natural language understanding)
    intent = _classify_intent_with_llm(state["operator_input"], correlation_id)
    
    logger.info("Intent classified", correlation_id=correlation_id, intent=intent)
    
    # STATELESS DESIGN: No busy checking - every command is independent
    # Robot execution is optional simulation, doesn't block planning
    return {
        "correlation_id": correlation_id,
        "operator_input": state["operator_input"],
        "intent": intent,
        "plan_attempt": 1,
    }


def route_condition(state: WorkflowState) -> str:
    """
    Conditional edge function for routing after Router node.
    
    Returns:
        Node name to route to: "sequence_planning" | "question" | "fallback"
    """
    intent = state.get("intent")
    
    if intent == "action":
        return "sequence_planning"
    elif intent == "question":
        return "question"
    else:
        return "fallback"
