"""
CLI Interface
Terminal-based operator interaction.
See .github/copilot-instructions.md for interface requirements.
"""

import sys
from typing import Optional
from src.core.translation.workflow import translation_workflow
from src.core.translation.state import WorkflowState
from src.core.knowledge.sqlite_client import RobotStateDB
from src.core.observability.logging import get_logger

logger = get_logger("cli")


def print_banner():
    """Display CLI startup banner."""
    print("\n" + "="*80)
    print("  CONTEXT-AWARE ROBOT CONTROL SYSTEM")
    print("  Natural Language → Task Sequences")
    print("="*80 + "\n")


def print_response(state: WorkflowState):
    """
    Display workflow response to operator.
    
    Args:
        state: Final workflow state
    """
    response = state.get("response")
    
    if response:
        print("\n" + "-"*80)
        print("RESPONSE:")
        print("-"*80)
        print(response)
        print("-"*80 + "\n")
    else:
        print("\n[No response generated]\n")


def run_cli_session():
    """
    Main CLI loop for operator interaction.
    
    Process:
    1. Prompt for operator input
    2. Invoke LangGraph workflow
    3. Display response
    4. Repeat until exit
    """
    print_banner()
    logger.info("CLI session started")
    
    print("CLI session started")
    print("Type 'exit' or 'quit' to exit, Ctrl+C to interrupt\n")
    
    try:
        while True:
            try:
                operator_input = input("robot> ").strip()
            except EOFError:
                break
            
            if operator_input.lower() in ["exit", "quit", "q"]:
                break
            
            if not operator_input:
                continue
            
            initial_state: WorkflowState = {
                "operator_input": operator_input,
            }
            
            try:
                print("Processing...", end="", flush=True)
                
                final_state = translation_workflow.invoke(initial_state)
                
                print("\r" + " " * 20 + "\r", end="")
                
                print_response(final_state)
            
            except Exception as e:
                print("\r" + " " * 20 + "\r", end="")
                print(f"\nError: {str(e)}")
                print("Please try again or contact system administrator.\n")
    
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        print("\n")
    
    finally:
        print("Shutting down...")
        logger.info("CLI session ended")


if __name__ == "__main__":
    run_cli_session()
