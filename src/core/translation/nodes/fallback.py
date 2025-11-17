"""
Fallback Node
Handles unknown intents, errors, and max retry failures.
See docs/translation/README.md for error handling.
"""

from typing import Dict, Any
from src.core.translation.state import WorkflowState
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.observability.logging import get_logger
import os

logger = get_logger("fallback")


def fallback_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Handle failures and provide helpful feedback to operator.
    
    Fallback scenarios:
    1. Unknown intent (not action/question)
    2. Max plan attempts exceeded (validation/verification failures)
    3. Human declined/timeout
    4. System errors
    
    Args:
        state: WorkflowState with context from previous nodes
    
    Returns:
        Updated state with response (error message + guidance)
    """
    correlation_id = state["correlation_id"]
    operator_input = state["operator_input"]
    intent = state.get("intent", "unknown")
    plan_attempt = state.get("plan_attempt", 1)
    validation_errors = state.get("validation_errors")
    human_decision = state.get("human_decision")
    verification_result = state.get("verification_result")
    
    max_attempts = int(os.getenv("MAX_PLAN_ATTEMPTS", "3"))
    
    logger.warning("Fallback triggered", 
                  correlation_id=correlation_id, 
                  intent=intent, 
                  plan_attempt=plan_attempt)
    
    # Determine failure reason and craft response
    
    # Scenario 1: Unknown intent
    if intent == "unknown":
        response = f"I couldn't understand your request: '{operator_input}'\n\n"
        response += "I can help with:\n"
        response += "  - Robot movement commands (e.g., 'Move to position y')\n"
        response += "  - Routine execution (e.g., 'Do routine x at position y')\n"
        response += "  - Tool changes (e.g., 'Attach tool xyxyxy')\n"
        response += "  - Information queries (e.g., 'What positions are available?')\n"
        response += "  - Task replay (e.g., 'Do that again')\n\n"
        response += "Please rephrase your command or ask a question."
        
        logger.info("Unknown intent fallback", correlation_id=correlation_id)
    
    # Scenario 2: Max planning attempts exceeded
    elif plan_attempt > max_attempts:
        response = f"Failed to generate a valid plan after {max_attempts} attempts.\n\n"
        
        if validation_errors:
            response += f"Schema validation errors:\n{validation_errors}\n\n"
        
        if verification_result and not verification_result.get("valid"):
            response += "Verification failures:\n"
            if verification_result.get("missing_positions"):
                response += f"  - Missing positions: {', '.join(verification_result['missing_positions'])}\n"
            if verification_result.get("illegal_edges"):
                response += f"  - Illegal moves: {verification_result['illegal_edges']}\n"
            if verification_result.get("unsupported_routines"):
                response += f"  - Unsupported routines: {verification_result['unsupported_routines']}\n"
            response += "\n"
        
        response += "Suggestions:\n"
        response += "  - Simplify your command (e.g., 'Move to <position_name>')\n"
        response += "  - Check available positions with 'What positions are available?'\n"
        response += "  - Ensure you're requesting valid position-to-position moves\n"
        
        logger.error("Max attempts exceeded", correlation_id=correlation_id)
    
    # Scenario 3: Human declined/timeout
    elif human_decision in ["declined", "timeout"]:
        if human_decision == "declined":
            response = "Task cancelled by operator."
        else:
            response = "Task timed out waiting for approval."
        
        logger.info("Human rejection fallback", correlation_id=correlation_id, decision=human_decision)
    
    # Scenario 4: Generic system error
    else:
        response = "An unexpected error occurred while processing your request.\n\n"
        response += f"Correlation ID: {correlation_id}\n"
        response += "Please try again or contact system administrator if the problem persists."
        
        logger.error("Generic fallback", correlation_id=correlation_id)
    
    return {
        "response": response,
    }
