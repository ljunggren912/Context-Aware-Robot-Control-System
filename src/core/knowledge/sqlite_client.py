"""
SQLite Database Clients
Manages robot state (current status) and history (run logs).
See docs/knowledge/README.md for schema definitions.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from src.core.observability.logging import get_logger

logger = get_logger("sqlite_client")


class RobotStateDB:
    """
    Single-row table tracking current robot state.
    
    STATELESS DESIGN: No target_position/target_tool locking.
    System queries current state for planning context only.
    Robot execution is simulation/optional - doesn't block planning.
    
    Schema:
        - current_position (TEXT): Where robot is now
        - current_tool (TEXT): Attached tool or "none"
        - last_updated (TEXT): ISO8601 timestamp
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize robot state database.
        
        Args:
            db_path: Path to SQLite file (default: $SQLITE_STATE_DB or data/robot_state.db)
        """
        self.db_path = db_path or os.getenv("SQLITE_STATE_DB", "data/robot_state.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        logger.info("RobotStateDB initialized", db_path=self.db_path)
    
    def _initialize_schema(self):
        """Create table and seed default state if empty."""
        with sqlite3.connect(self.db_path) as conn:
            # Create table (simplified - no target state locking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS robot_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_position TEXT NOT NULL,
                    current_tool TEXT NOT NULL,
                    last_updated TEXT NOT NULL
                )
            """)
            
            # Check if empty and seed
            cursor = conn.execute("SELECT COUNT(*) FROM robot_state")
            if cursor.fetchone()[0] == 0:
                conn.execute("""
                    INSERT INTO robot_state (id, current_position, current_tool, last_updated)
                    VALUES (1, 'Home', 'none', ?)
                """, (datetime.utcnow().isoformat(),))
                logger.info("Seeded default robot state", position="Home", tool="none")
    
    def get_state(self) -> Dict[str, str]:
        """
        Retrieve current robot state.
        
        Used by sequence builder to determine starting position/tool for planning.
        State is NOT locked - every command is independent.
        
        Returns:
            Dict with keys: current_position, current_tool, last_updated
        
        Example:
            >>> db.get_state()
            {
                "current_position": "Home", 
                "current_tool": "none",
                "last_updated": "2025-01-15T12:34:56"
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT current_position, current_tool, last_updated FROM robot_state WHERE id = 1")
            row = cursor.fetchone()
            state = dict(row)
            logger.info("Retrieved robot state", state=state)
            return state
    
    def update_position(self, position: str):
        """
        Update current robot position.
        
        Args:
            position: New position name
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE robot_state
                SET current_position = ?, last_updated = ?
                WHERE id = 1
            """, (position, datetime.utcnow().isoformat()))
            logger.info("Updated robot position", position=position)
    
    def update_tool(self, tool: str):
        """
        Update currently attached tool.
        
        Args:
            tool: Tool name or "none"
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE robot_state
                SET current_tool = ?, last_updated = ?
                WHERE id = 1
            """, (tool, datetime.utcnow().isoformat()))
            logger.info("Updated robot tool", tool=tool)


class HistoryDB:
    """
    Run history and step tracking for replay and analysis.
    
    Schema:
        runs:
            - run_id (TEXT PRIMARY KEY): UUIDv4 correlation_id
            - operator_input (TEXT): Original natural language command
            - sequence_json (TEXT): Validated JSON plan (before YAML conversion)
            - status (TEXT): pending | running | completed | failed
            - started_at (TEXT): ISO8601 start time
            - finished_at (TEXT): ISO8601 end time (NULL if running)
        
        run_steps:
            - step_id (INTEGER PRIMARY KEY)
            - run_id (TEXT FOREIGN KEY): Links to runs table
            - position (TEXT): Target position
            - action (TEXT): move | routine
            - state (TEXT): pending | running | completed | error
            - error (TEXT): Error message if failed (NULL otherwise)
            - started_at (TEXT): ISO8601 start time
            - finished_at (TEXT): ISO8601 end time (NULL if running)
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize history database.
        
        Args:
            db_path: Path to SQLite file (default: $SQLITE_HISTORY_DB or data/history.db)
        """
        self.db_path = db_path or os.getenv("SQLITE_HISTORY_DB", "data/history.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        logger.info("HistoryDB initialized", db_path=self.db_path)
    
    def _initialize_schema(self):
        """Create tables for runs and run_steps."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    operator_input TEXT NOT NULL,
                    sequence_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_steps (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    position TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('move', 'routine')),
                    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'error')),
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs (run_id)
                )
            """)
            logger.info("History schema initialized")
    
    def create_run(self, run_id: str, operator_input: str, sequence_json: str) -> None:
        """
        Create new run entry with pending status.
        
        Args:
            run_id: UUIDv4 correlation_id
            operator_input: Original operator command
            sequence_json: Validated JSON plan (before YAML conversion)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO runs (run_id, operator_input, sequence_json, status, started_at)
                VALUES (?, ?, ?, 'pending', ?)
            """, (run_id, operator_input, sequence_json, datetime.utcnow().isoformat()))
            logger.info("Created run entry", run_id=run_id)
    
    def update_run_status(self, run_id: str, status: str) -> None:
        """
        Update run status (running | completed | failed).
        
        Args:
            run_id: Run correlation ID
            status: New status value
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE runs
                SET status = ?, finished_at = ?
                WHERE run_id = ?
            """, (status, datetime.utcnow().isoformat(), run_id))
            logger.info("Updated run status", run_id=run_id, status=status)
    
    def add_step(self, run_id: str, position: str, action: str) -> int:
        """
        Add step to run with pending state.
        
        Args:
            run_id: Run correlation ID
            position: Target position name
            action: move | routine
        
        Returns:
            step_id (auto-incremented)
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO run_steps (run_id, position, action, state, started_at)
                VALUES (?, ?, ?, 'pending', ?)
            """, (run_id, position, action, datetime.utcnow().isoformat()))
            step_id = cursor.lastrowid
            logger.info("Added run step", run_id=run_id, step_id=step_id, position=position, action=action)
            return step_id
    
    def update_step_state(self, step_id: int, state: str, error: Optional[str] = None) -> None:
        """
        Update step state (running | completed | error).
        
        Args:
            step_id: Step identifier
            state: New state value
            error: Error message if state is "error"
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE run_steps
                SET state = ?, error = ?, finished_at = ?
                WHERE step_id = ?
            """, (state, error, datetime.utcnow().isoformat(), step_id))
            logger.info("Updated step state", step_id=step_id, state=state, error=error)
    
    def get_latest_completed_run(self) -> Optional[Dict[str, str]]:
        """
        Retrieve most recent successfully completed run for replay.
        
        Returns:
            Dict with keys: run_id, operator_input, sequence_json, finished_at (or None)
        
        Example:
            >>> db.get_latest_completed_run()
            {
                "run_id": "abc-123",
                "operator_input": "Weld at position 1",
                "sequence_json": "[{...}]",
                "finished_at": "2025-01-15T14:30:00"
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT run_id, operator_input, sequence_json, finished_at
                FROM runs
                WHERE status = 'completed'
                ORDER BY finished_at DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            
            if row:
                run = dict(row)
                logger.info("Retrieved latest completed run", run_id=run["run_id"])
                return run
            else:
                logger.warning("No completed runs found")
                return None
    
    def get_run_by_id(self, run_id: str) -> Optional[Dict[str, str]]:
        """
        Retrieve specific run by ID for replay.
        
        Args:
            run_id: Run correlation ID (UUID)
        
        Returns:
            Dict with keys: run_id, operator_input, sequence_json, status, finished_at (or None)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT run_id, operator_input, sequence_json, status, finished_at
                FROM runs
                WHERE run_id = ?
            """, (run_id,))
            row = cursor.fetchone()
            
            if row:
                run = dict(row)
                logger.info("Retrieved run by ID", run_id=run_id, status=run["status"])
                return run
            else:
                logger.warning("Run not found", run_id=run_id)
                return None
    
    def get_runs_by_date(self, date: str) -> List[Dict[str, str]]:
        """
        Query runs on specific date.
        
        Args:
            date: Date string in YYYY-MM-DD format
        
        Returns:
            List of run dicts with keys: run_id, operator_input, status, started_at
        
        Example:
            >>> db.get_runs_by_date("2025-01-15")
            [{"run_id": "abc-123", "operator_input": "...", "status": "completed", ...}]
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT run_id, operator_input, status, started_at, finished_at
                FROM runs
                WHERE DATE(started_at) = DATE(?)
                ORDER BY started_at DESC
            """, (date,))
            runs = [dict(row) for row in cursor.fetchall()]
            logger.info("Queried runs by date", date=date, count=len(runs))
            return runs
    
    def get_failed_positions(self, run_id: str) -> List[str]:
        """
        Find which positions failed during a run.
        
        Args:
            run_id: Run correlation ID
        
        Returns:
            List of position names with error state
        
        Example:
            >>> db.get_failed_positions("abc-123")
            ["<position_1>", "<position_2>"]
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT position
                FROM run_steps
                WHERE run_id = ? AND state = 'error'
                ORDER BY step_id
            """, (run_id,))
            positions = [row[0] for row in cursor.fetchall()]
            logger.info("Queried failed positions", run_id=run_id, positions=positions)
            return positions
