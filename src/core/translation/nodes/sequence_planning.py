"""
Sequence Planning Node (Hybrid Architecture: LLM + Graph Algorithms)

Two-stage planning approach:
  1. LLM parses intent (what the user wants)
  2. SequenceBuilder plans execution (how to achieve it)

This separation of concerns improves reliability by letting each component 
focus on what it does best.
"""

import os
from typing import Dict, Any
from src.core.translation.state import WorkflowState
from src.core.translation.nodes.intent_parser import parse_intent_node
from src.core.translation.sequence_builder import SequenceBuilder
from src.core.observability.logging import get_logger

logger = get_logger("sequence_planning")


def sequence_planning_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Two-stage planning process:
    
    Stage 1: Use LLM to parse high-level intent from natural language
    Stage 2: Use graph algorithms to build concrete action sequence
    
    This architecture separates concerns:
      - LLM: Natural language understanding (what the user wants)
      - SequenceBuilder: Path planning and tool logic (how to achieve it)
    
    Args:
        state: WorkflowState containing:
            - operator_input: Natural language command
            - correlation_id: Run identifier
            - human_comments: Optional revision feedback
            - plan_attempt: Current attempt number
    
    Returns:
        Dict with plan (concrete step sequence) and intent, or validation_errors if failed
    """
    correlation_id = state["correlation_id"]
    human_comments = state.get("human_comments")
    plan_attempt = state.get("plan_attempt", 1)
    
    logger.info("Starting two-stage planning",
               correlation_id=correlation_id,
               attempt=plan_attempt,
               has_human_feedback=bool(human_comments))
    
    # Stage 1: Parse intent with LLM (includes human feedback if this is a revision)
    intent_result = parse_intent_node(state)
    intent = intent_result["intent"]
    
    # Check if LLM understood the command
    if intent.get("goal") == "unknown":
        logger.warning("Could not parse intent",
                      correlation_id=correlation_id)
        return {
            "plan": [],
            "validation_errors": "Could not understand operator command. Please rephrase."
        }
    
    # Stage 2: Build concrete sequence using graph algorithms
    builder = SequenceBuilder()
    try:
        plan = builder.build_sequence(intent, correlation_id)
        
        logger.info("Sequence planning completed",
                   correlation_id=correlation_id,
                   intent=intent,
                   step_count=len(plan))
        
        # Log the full plan for visibility
        logger.info("Generated plan details",
                   correlation_id=correlation_id,
                   plan=plan)
        
        return {
            "plan": plan,
            "intent": intent,
        }
    
    except Exception as e:
        logger.error("Sequence building failed",
                    correlation_id=correlation_id,
                    intent=intent,
                    error=str(e))
        return {
            "plan": [],
            "validation_errors": f"Failed to build sequence: {str(e)}",
            "plan_attempt": plan_attempt + 1,
        }
    
    finally:
        builder.close()


def sequence_planning_condition(state: WorkflowState) -> str:
    """
    Routing logic after sequence planning.
    
    Routes based on whether a valid plan was generated and current attempt count.
    
    Returns:
        - "human_review": Valid plan generated, proceed to human approval
        - "sequence_planning": Planning failed, retry if attempts remain
        - "fallback": Planning failed and max attempts reached
    """
    plan = state.get("plan", [])
    validation_errors = state.get("validation_errors")
    plan_attempt = state.get("plan_attempt", 1)
    max_attempts = int(os.getenv("MAX_PLAN_ATTEMPTS", "3"))
    
    # Check if planning failed (validation errors and empty plan)
    if validation_errors and len(plan) == 0:
        if plan_attempt < max_attempts:
            # Retry planning with error feedback
            logger.info("Planning failed, will retry",
                       correlation_id=state["correlation_id"],
                       attempt=plan_attempt,
                       error=validation_errors)
            return "sequence_planning"
        else:
            # Max attempts reached, route to fallback
            logger.warning("Planning failed, max attempts reached",
                          correlation_id=state["correlation_id"],
                          attempts=plan_attempt)
            return "fallback"
    else:
        # Plan generated successfully
        return "human_review"
