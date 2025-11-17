"""
HumanReview Node
Blocking human-in-the-loop approval gate with timeout support.
See docs/translation/README.md for decision outcomes.
"""

import json
import os
import sys
import time
from typing import Dict, Any
from pathlib import Path
from src.core.translation.state import WorkflowState
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.observability.logging import get_logger

logger = get_logger("human_review")

# Human review timeout configuration (seconds)
HUMAN_REVIEW_TIMEOUT = int(os.getenv("HUMAN_REVIEW_TIMEOUT", "120"))


def human_review_node(state: WorkflowState) -> Dict[str, Any]:
    """
    Present plan to operator for approval with timeout.
    
    This is a BLOCKING node that waits for human input.
    The CLI mode prompts the operator directly with a timeout timer.
    
    Operator decisions:
    - approved: Continue to verification
    - revision: Return to intent parser with comments
    - declined: Abort to Fallback
    - timeout: Abort to Fallback (no response within configured time)
    
    Args:
        state: WorkflowState with plan, correlation_id
    
    Returns:
        Updated state with human_decision, optional human_comments, deadline tracking
    """
    correlation_id = state["correlation_id"]
    plan = state["plan"]
    operator_input = state["operator_input"]
    
    # Set deadline for timeout detection
    deadline = time.time() + HUMAN_REVIEW_TIMEOUT
    
    logger.info("Presenting plan for human review", 
               correlation_id=correlation_id,
               timeout_seconds=HUMAN_REVIEW_TIMEOUT)
    
    # CLI prompt with operator approval
    decision, comments = prompt_operator_cli(correlation_id, operator_input, plan)
    
    logger.info("Human decision recorded", 
               correlation_id=correlation_id, 
               decision=decision)
    
    return {
        "human_decision": decision,
        "human_comments": comments,
        "human_review_deadline": deadline,
        "human_review_started_at": time.time(),
    }


def prompt_operator_cli(correlation_id: str, operator_input: str, plan: list) -> tuple:
    """
    CLI-based operator prompt for plan approval.
    
    Args:
        correlation_id: Run ID
        operator_input: Original command
        plan: JSON task plan
    
    Returns:
        Tuple of (decision, comments)
        decision: 'approved' | 'revision' | 'declined' | 'timeout'
        comments: str or None
    """
    print("\n" + "="*80)
    print("PLAN REVIEW REQUIRED")
    print("="*80)
    print(f"Correlation ID: {correlation_id}")
    print(f"Your command: {operator_input}")
    print("\nGenerated Plan:")
    print()
    for step in plan:
        step_id = step.get('id', '?')
        name = step.get('name', 'Unknown')
        print(f"  {step_id}. {name}")
    
    print("\n" + "="*80)
    print("Options:")
    print("  [a] Approve - Execute this plan")
    print("  [r] Revise - Request changes (provide comments)")
    print("  [d] Decline - Cancel this task")
    print("="*80)
    print(f"Timeout: {HUMAN_REVIEW_TIMEOUT} seconds")
    print("="*80)
    print()  # Add blank line before prompt
    
    end_time = time.time() + HUMAN_REVIEW_TIMEOUT
    
    while True:
        remaining = max(0, int(end_time - time.time()))
        if remaining == 0:
            print("\nHuman review timeout exceeded. Routing to fallback.")
            logger.warning("Human review timeout", correlation_id=correlation_id)
            return ("timeout", None)
        
        print("Your decision [a/r/d]: ", end='')
        sys.stdout.flush()  # Ensure prompt is displayed
        
        choice = sys.stdin.readline().strip().lower()
        
        if choice == "a":
            return ("approved", None)
        
        elif choice == "r":
            remaining = max(0, int(end_time - time.time()))
            if remaining == 0:
                print("Timeout exceeded while waiting for comments.")
                return ("timeout", None)
            
            print()  # Add blank line before prompt
            print("What changes do you want? ", end='')
            sys.stdout.flush()  # Ensure prompt is displayed
            comments = sys.stdin.readline().strip()
            return ("revision", comments)
        
        elif choice == "d":
            return ("declined", None)
        
        else:
            print("Invalid choice. Please enter 'a', 'r', or 'd'.")



def human_review_condition(state: WorkflowState) -> str:
    """
    Conditional edge after HumanReview node.
    
    Returns:
        "verify" (approved) | "sequence_planning" (revision) | "fallback" (declined/timeout)
    """
    decision = state.get("human_decision")
    
    if decision == "approved":
        return "verify"
    elif decision == "revision":
        return "sequence_planning"
    else:
        # declined or timeout
        return "fallback"

