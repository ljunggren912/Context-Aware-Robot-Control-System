"""
LangGraph Workflow Definition
Wires together all Translation Layer nodes per Mermaid diagram.
See docs/translation/README.md for complete data flow.
"""

from langgraph.graph import StateGraph, END
from src.core.translation.state import WorkflowState
from src.core.translation.nodes.router import router_node, route_condition
from src.core.translation.nodes.sequence_planning import sequence_planning_node, sequence_planning_condition
from src.core.translation.nodes.human_review import human_review_node, human_review_condition
from src.core.translation.nodes.verify import verify_node, verify_condition
from src.core.translation.nodes.robot import robot_node
from src.core.translation.nodes.question import question_node
from src.core.translation.nodes.fallback import fallback_node
from src.core.observability.logging import get_logger

logger = get_logger("workflow")


def create_workflow() -> StateGraph:
    """
    Build LangGraph workflow for Translation Layer.
    
    Architecture: Hybrid LLM + Graph Algorithms
    
    Node flow:
        Operator Input → Router →
          ├─→ [action] → SequencePlanning (LLM parses intent → Python builds sequence) →
          │              HumanReview → Verify → Robot → END
          ├─→ [question] → Question → END
          └─→ [unknown] → Fallback → END
    
    SequencePlanning details:
        - LLM extracts user intent (desired goal)
        - Python builds concrete sequence using Neo4j pathfinding
        - Replaces previous PlanLLM + SchemaValidate approach
        - Output is always schema-valid (no validation step needed)
    
    Retry loops:
        - Verify fails → SequencePlanning (with feedback)
        - HumanReview revision → SequencePlanning (with comments)
        - Max attempts → Fallback → END
    
    Returns:
        Compiled StateGraph ready for invocation
    """
    logger.info("Creating LangGraph workflow")
    
    # Initialize graph with state schema
    workflow = StateGraph(WorkflowState)
    
    # Add nodes
    workflow.add_node("router", router_node)
    workflow.add_node("sequence_planning", sequence_planning_node)  
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("verify", verify_node)
    workflow.add_node("robot", robot_node)
    workflow.add_node("question", question_node)
    workflow.add_node("fallback", fallback_node)
    
    # Set entry point
    workflow.set_entry_point("router")
    
    # Add conditional edges from Router
    workflow.add_conditional_edges(
        "router",
        route_condition,
        {
            "sequence_planning": "sequence_planning",  # Action commands
            "question": "question",
            "fallback": "fallback",
        }
    )
    
    # Add conditional edges from SequencePlanning
    workflow.add_conditional_edges(
        "sequence_planning",
        sequence_planning_condition,
        {
            "human_review": "human_review",
            "sequence_planning": "sequence_planning",  # Retry on error
            "fallback": "fallback",  # Max attempts
        }
    )
    
    # Add conditional edges from HumanReview
    workflow.add_conditional_edges(
        "human_review",
        human_review_condition,
        {
            "verify": "verify",
            "sequence_planning": "sequence_planning",  # Revision - replan
            "fallback": "fallback",   # Declined/timeout
        }
    )
    
    # Add conditional edges from Verify
    workflow.add_conditional_edges(
        "verify",
        verify_condition,
        {
            "robot": "robot",
            "sequence_planning": "sequence_planning",  # Retry with feedback
            "fallback": "fallback",   # Max attempts
        }
    )
    
    # Terminal edges (all end at END)
    workflow.add_edge("robot", END)
    workflow.add_edge("question", END)
    workflow.add_edge("fallback", END)
    
    # Compile workflow
    compiled_workflow = workflow.compile()
    
    logger.info("Workflow compiled successfully")
    return compiled_workflow


# Singleton instance for import
translation_workflow = create_workflow()
