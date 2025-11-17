"""
Neo4j Knowledge Graph Client
Provides runtime queries for positions, tools, routines, and edge validation.
Graph is the single source of truth for system capabilities.
See docs/knowledge/README.md for data model.
"""

import os
from typing import Dict, List, Optional, Tuple, Any
from neo4j import GraphDatabase, Driver
from src.core.observability.logging import get_logger

logger = get_logger("neo4j_client")


class Neo4jClient:
    """
    Neo4j graph database client for knowledge queries.
    
    Nodes:
    - Position: Locations with roles (home, safe_approach, tool_mount, work)
    - Tool: Available tools for robot operations
    - ToolStand: Tool storage locations
    - Routine: Parameterized actions (system and task-specific routines)
    
    Relationships:
    - :ONLY_ALLOWED_MOVE_TO: Bidirectional motion whitelist
    - :SUPPORTED_AT: Routine metadata (stabilize, action_after, verify)
    - :LOCATED_AT: Tool → ToolStand
    - :TOOL_AVAILABLE_AT: Tool → Position
    """
    
    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None):
        """
        Initialize Neo4j connection from environment or explicit credentials.
        
        Args:
            uri: Neo4j connection URI (default: $NEO4J_URI)
            user: Username (default: $NEO4J_USER)
            password: Password (default: $NEO4J_PASSWORD)
        """
        # Get configuration from environment (NO defaults - fail if missing)
        self.uri = uri or os.getenv("NEO4J_URI")
        if not self.uri:
            raise ValueError("NEO4J_URI not set in .env file")
        
        self.user = user or os.getenv("NEO4J_USER")
        if not self.user:
            raise ValueError("NEO4J_USER not set in .env file")
        
        self.password = password or os.getenv("NEO4J_PASSWORD")
        if not self.password:
            raise ValueError("NEO4J_PASSWORD not set in .env file")
        
        self.driver: Optional[Driver] = None
        logger.info("Neo4j client initialized", uri=self.uri)
    
    def connect(self):
        """Establish connection to Neo4j database."""
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            # Test connection
            self.driver.verify_connectivity()
            logger.info("Neo4j connection established")
        except Exception as e:
            logger.error("Failed to connect to Neo4j", error=str(e))
            raise
    
    def close(self):
        """Close Neo4j connection."""
        if self.driver:
            self.driver.close()
            logger.info("Neo4j connection closed")
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
    
    def get_all_positions(self) -> List[Dict[str, str]]:
        """
        Query all positions in the graph.
        
        Returns:
            List of dicts with keys: name, role, description
        
        Example:
            [
                {"name": "<position_1>", "role": "home", "description": "<description>"},
                {"name": "<position_2>", "role": "safe_approach", "description": "<description>"}
            ]
        """
        query = """
        MATCH (p:Position)
        RETURN p.name AS name, p.role AS role, p.description AS description
        ORDER BY p.name
        """
        
        with self.driver.session() as session:
            result = session.run(query)
            positions = [dict(record) for record in result]
            logger.info("Queried all positions", count=len(positions))
            return positions
    
    def get_all_tools(self) -> List[Dict[str, str]]:
        """
        Query all available tools.
        
        Returns:
            List of dicts with keys: name, description
        
        Example:
            [
                {"name": "<tool_1>", "description": "<description>"},
                {"name": "<tool_2>", "description": "<description>"}
            ]
        """
        query = """
        MATCH (t:Tool)
        RETURN t.name AS name, t.description AS description
        ORDER BY t.name
        """
        
        with self.driver.session() as session:
            result = session.run(query)
            tools = [dict(record) for record in result]
            logger.info("Queried all tools", count=len(tools))
            return tools
    
    def get_all_routines(self) -> List[Dict[str, str]]:
        """
        Query all available routines.
        
        Returns:
            List of dicts with keys: name, description, required_tool
        
        Example:
            [
                {"name": "<routine_1>", "description": "<description>", "required_tool": "<tool_name>"},
                {"name": "<routine_2>", "description": "<description>", "required_tool": "<tool_name>"}
            ]
        """
        query = """
        MATCH (r:Routine)
        RETURN r.name AS name, 
               r.description AS description,
               COALESCE(r.required_tool, 'none') AS required_tool
        ORDER BY r.name
        """
        
        with self.driver.session() as session:
            result = session.run(query)
            routines = [dict(record) for record in result]
            logger.info("Queried all routines", count=len(routines))
            return routines
    
    def get_routine_by_name(self, routine_name: str) -> Optional[Dict[str, Any]]:
        """
        Query specific routine by name.
        
        Args:
            routine_name: Name of the routine
        
        Returns:
            Dict with routine info (name, description, required_tool) or None if not found
        
        Example:
            >>> client.get_routine_by_name("<routine_name>")
            {"name": "<routine_name>", "description": "<description>", "required_tool": "<tool_name>"}
        """
        query = """
        MATCH (r:Routine {name: $routine_name})
        RETURN r.name AS name,
               r.description AS description,
               COALESCE(r.required_tool, 'none') AS required_tool
        """
        
        with self.driver.session() as session:
            result = session.run(query, routine_name=routine_name)
            record = result.single()
            if record:
                routine_info = dict(record)
                logger.info("Queried routine", routine_name=routine_name, found=True)
                return routine_info
            else:
                logger.warning("Routine not found", routine_name=routine_name)
                return None
    
    def get_tool_locations(self) -> Dict[str, str]:
        """
        Query tool locations (which Position each Tool is located at).
        
        Returns:
            Dict mapping tool name to position name
        
        Example:
            {
                "<tool_1>": "<position_name>",
                "<tool_2>": "<position_name>"
            }
        """
        query = """
        MATCH (t:Tool)-[:TOOL_AVAILABLE_AT]->(s:ToolStand)-[:LOCATED_AT]->(p:Position)
        RETURN t.name AS tool, p.name AS position
        ORDER BY t.name
        """
        
        with self.driver.session() as session:
            result = session.run(query)
            locations = {record["tool"]: record["position"] for record in result}
            logger.info("Queried tool locations", count=len(locations))
            return locations
    
    def get_allowed_moves(self, from_position: str) -> List[str]:
        """
        Query positions reachable from current position via :ONLY_ALLOWED_MOVE_TO edges.
        
        Args:
            from_position: Current position name
        
        Returns:
            List of position names that can be directly reached
        
        Example:
            >>> client.get_allowed_moves("<position_name>")
            ["<position_1>", "<position_2>"]
        """
        query = """
        MATCH (current:Position {name: $from_name})-[:ONLY_ALLOWED_MOVE_TO]-(next:Position)
        RETURN next.name AS position
        ORDER BY position
        """
        
        with self.driver.session() as session:
            result = session.run(query, from_name=from_position)
            allowed = [record["position"] for record in result]
            logger.info("Queried allowed moves", from_position=from_position, allowed_count=len(allowed))
            return allowed
    
    def is_move_allowed(self, from_position: str, to_position: str) -> bool:
        """
        Check if direct move between two positions is allowed (edge exists).
        
        Args:
            from_position: Source position name
            to_position: Destination position name
        
        Returns:
            True if :ONLY_ALLOWED_MOVE_TO edge exists (bidirectional)
        
        Example:
            >>> client.is_move_allowed("<position_1>", "<position_2>")
            True
        """
        query = """
        MATCH (a:Position {name: $from_name})-[:ONLY_ALLOWED_MOVE_TO]-(b:Position {name: $to_name})
        RETURN COUNT(*) > 0 AS allowed
        """
        
        with self.driver.session() as session:
            result = session.run(query, from_name=from_position, to_name=to_position)
            allowed = result.single()["allowed"]
            logger.info("Checked edge whitelist", from_position=from_position, to_position=to_position, allowed=allowed)
            return allowed
    
    def get_supported_positions(self, routine_name: str) -> List[str]:
        """
        Get all positions where a routine is supported.
        
        Args:
            routine_name: Routine name
        
        Returns:
            List of position names where routine has :SUPPORTED_AT relationship
        
        Example:
            >>> client.get_supported_positions("<routine_name>")
            ["<position_1>", "<position_2>"]
        """
        query = """
        MATCH (r:Routine {name: $routine_name})-[:SUPPORTED_AT]->(p:Position)
        RETURN p.name AS position_name
        ORDER BY position_name
        """
        
        with self.driver.session() as session:
            result = session.run(query, routine_name=routine_name)
            positions = [record["position_name"] for record in result]
            logger.info("Retrieved supported positions", routine=routine_name, positions=positions)
            return positions
    
    def get_routine_metadata(self, routine_name: str, position_name: str) -> Optional[Dict[str, any]]:
        """
        Query routine metadata at specific position via :SUPPORTED_AT relationship.
        
        Args:
            routine_name: Routine name (e.g., "<routine_name>")
            position_name: Position name (e.g., "<position_name>")
        
        Returns:
            Dict with keys: stabilize, action_after, verify (or None if not supported)
        
        Example:
            >>> client.get_routine_metadata("<routine_name>", "<position_name>")
            {"stabilize": 1.5, "action_after": "<action_name>", "verify": "<verify_routine>"}
        """
        query = """
        MATCH (r:Routine {name: $routine_name})-[s:SUPPORTED_AT]->(p:Position {name: $position_name})
        RETURN s.stabilize AS stabilize,
               s.action_after AS action_after,
               s.verify AS verify
        """
        
        with self.driver.session() as session:
            result = session.run(query, routine_name=routine_name, position_name=position_name)
            record = result.single()
            
            if record:
                metadata = dict(record)
                logger.info("Retrieved routine metadata", routine=routine_name, position=position_name, metadata=metadata)
                return metadata
            else:
                logger.warning("Routine not supported at position", routine=routine_name, position=position_name)
                return None
    
    def get_shortest_path(self, from_position: str, to_position: str) -> Optional[List[str]]:
        """
        Calculate shortest valid path between two positions.
        
        Args:
            from_position: Start position name
            to_position: End position name
        
        Returns:
            Ordered list of position names (including start/end), or None if no path
        
        Example:
            >>> client.get_shortest_path("<position_1>", "<position_2>")
            ["<position_1>", "<position_3>", "<position_2>"]
        """
        query = """
        MATCH path = shortestPath((start:Position {name: $from_name})-[:ONLY_ALLOWED_MOVE_TO*]-(end:Position {name: $to_name}))
        RETURN [node IN nodes(path) | node.name] AS positions
        """
        
        with self.driver.session() as session:
            result = session.run(query, from_name=from_position, to_name=to_position)
            record = result.single()
            
            if record:
                path = record["positions"]
                logger.info("Calculated shortest path", from_position=from_position, to_position=to_position, path=path)
                return path
            else:
                logger.warning("No path found", from_position=from_position, to_position=to_position)
                return None
