"""
Structured JSON Logging for Research Analysis
All logs feed into academic research report.
See docs/observability/README.md for schema and privacy requirements.

Logs are grouped by correlation_id in run-specific files for request tracing.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from hashlib import sha256
import threading


class StructuredLogger:
    """
    Provides structured JSON logging grouped by correlation_id.
    
    Logs are written to run-specific files where all logs for a correlation_id
    are grouped under a parent JSON object.
    
    File structure: logs/runs/YYYY-MM-DD/<correlation_id>.json
    Format: {
        "correlation_id": "...",
        "model_name": "...",
        "graph_version": "...",
        "start_time": "...",
        "end_time": "...",
        "logs": [
            {"ts": "...", "service": "...", "level": "...", "message": "...", ...},
            ...
        ]
    }
    """
    
    # Class-level cache for run data (correlation_id -> log entries)
    _run_cache = {}
    _cache_lock = threading.Lock()
    
    # Service to subfolder mapping
    _SERVICE_FOLDERS = {
        # LLM-related services
        "llm_client": "llm",
        "intent_parser": "llm",
        "router": "llm",
        "sequence_planning": "llm",
        
        # Verification services
        "verify": "verification",
        "verify_node": "verification",
        "yaml_converter": "verification",
        
        # Robot execution
        "robot_node": "robot",
        "robot_shim": "robot",
        "executor": "robot",
        "socket_client": "robot",
        
        # Knowledge/database
        "neo4j_client": "knowledge",
        "sqlite_client": "knowledge",
        
        # Workflow/human interaction
        "cli": "workflow",
        "workflow": "workflow",
        "human_review": "workflow",
        
        # Translation/building
        "sequence_builder": "translation",
        "state": "translation",
    }
    
    def __init__(self, service_name: str, log_dir: Optional[str] = None):
        """
        Initialize structured logger for a specific service.
        
        Args:
            service_name: Component name (router, intent_parser, verify, etc.)
            log_dir: Directory for JSON log files (default: $LOG_DIR from .env)
        """
        self.service_name = service_name
        self.log_dir = Path(log_dir or os.getenv("LOG_DIR", "logs"))
        self.runs_dir = self.log_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine subfolder for this service
        subfolder = self._SERVICE_FOLDERS.get(service_name, "other")
        self.service_log_dir = self.log_dir / subfolder
        self.service_log_dir.mkdir(parents=True, exist_ok=True)
        
        # Service-specific log file in appropriate subfolder
        today = datetime.now().strftime("%Y-%m-%d")
        self.legacy_log_file = self.service_log_dir / f"{service_name}_{today}.jsonl"
        
        # Initialize Python logger for console output
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
        
        # Only show console logs for high-level services (cli, workflow) or errors
        console_log_level = os.getenv("CONSOLE_LOG_LEVEL", "WARNING")
        show_console = service_name in ["cli", "workflow"] or console_log_level == "DEBUG"
        
        if show_console:
            # Console handler (human-readable)
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(
                logging.Formatter("%(message)s")  # Simplified format for CLI
            )
            self.logger.addHandler(console_handler)
    
    def _get_run_file_path(self, correlation_id: str) -> Path:
        """Get the file path for a specific run."""
        today = datetime.now().strftime("%Y-%m-%d")
        date_dir = self.runs_dir / today
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / f"{correlation_id}.json"
    
    def _write_run_log(self, correlation_id: str):
        """Write the complete run log to file."""
        with self._cache_lock:
            if correlation_id not in self._run_cache:
                return
            
            run_data = self._run_cache[correlation_id]
            run_file = self._get_run_file_path(correlation_id)
            
            # Update end time
            run_data["end_time"] = datetime.utcnow().isoformat() + "Z"
            
            # Write to file (atomic write)
            temp_file = run_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(run_data, f, indent=2)
            temp_file.replace(run_file)
    
    def _get_global_fields(self, include_model_info: bool = False) -> Dict[str, Any]:
        """
        Extract global context fields from environment.
        
        Args:
            include_model_info: If True, include model_name and graph_version
                                (only for LLM-related services)
        
        Returns:
            Dictionary with ts, service, and optionally model_name/graph_version
        """
        fields = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
        }
        
        # Only include model info for LLM-related services
        if include_model_info:
            fields["model_name"] = os.getenv("MODEL_NAME", "unknown")
            fields["graph_version"] = os.getenv("GRAPH_VERSION", "unknown")
        
        return fields
    
    def log_json(
        self,
        level: str,
        message: str,
        correlation_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        plan_attempt: Optional[int] = None,
        **extra_fields: Any
    ) -> None:
        """
        Write structured JSON log entry grouped by correlation_id.
        
        Args:
            level: Log level (INFO, WARNING, ERROR)
            message: Human-readable log message
            correlation_id: UUIDv4 for request tracing (REQUIRED for grouped logging)
            duration_ms: Operation duration in milliseconds (optional)
            plan_attempt: Planner retry counter (optional)
            **extra_fields: Additional context-specific fields
        
        Privacy:
            - Hashes input/prompt/taskspec instead of raw content
            - Never logs ROBOT_API_TOKEN
        
        Note:
            model_name and graph_version are only included for LLM-related services
        """
        # Determine if this service uses LLM (should include model info)
        llm_services = {
            "llm_client", "intent_parser", "router", "sequence_planning", 
            "cli", "workflow"
        }
        include_model_info = self.service_name in llm_services
        
        # Build log entry
        log_entry = self._get_global_fields(include_model_info=include_model_info)
        log_entry.update({
            "level": level.upper(),
            "message": message,
        })
        
        # Add optional fields
        if duration_ms is not None:
            log_entry["duration_ms"] = duration_ms
        if plan_attempt is not None:
            log_entry["plan_attempt"] = plan_attempt
        
        # Add extra fields (with privacy filtering)
        for key, value in extra_fields.items():
            # Hash sensitive content instead of logging raw
            if key in ["input_text", "prompt", "taskspec"]:
                log_entry[f"{key}_sha256"] = sha256(str(value).encode()).hexdigest()
            elif key == "ROBOT_API_TOKEN":
                continue  # Never log API token
            else:
                log_entry[key] = value
        
        # If correlation_id provided, write to grouped run file
        if correlation_id:
            with self._cache_lock:
                if correlation_id not in self._run_cache:
                    # Initialize new run
                    self._run_cache[correlation_id] = {
                        "correlation_id": correlation_id,
                        "model_name": os.getenv("MODEL_NAME", "unknown"),
                        "graph_version": os.getenv("GRAPH_VERSION", "unknown"),
                        "start_time": datetime.utcnow().isoformat() + "Z",
                        "end_time": None,
                        "logs": []
                    }
                
                # Add log entry (without redundant correlation_id since it's the parent)
                self._run_cache[correlation_id]["logs"].append(log_entry)
            
            # Write to file after each log (ensures we don't lose data on crashes)
            self._write_run_log(correlation_id)
        
        # Also write to legacy service-specific log file for backward compatibility
        log_entry["correlation_id"] = correlation_id  # Keep for legacy format
        with open(self.legacy_log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
        
        # Write to console (human-readable)
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        extra_msg = " | ".join(f"{k}={v}" for k, v in extra_fields.items() if v is not None)
        full_message = f"{message} | {extra_msg}" if extra_msg else message
        log_method(full_message)
    
    def info(self, message: str, **kwargs):
        """Log INFO level message."""
        self.log_json("INFO", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log WARNING level message."""
        self.log_json("WARNING", message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log ERROR level message."""
        self.log_json("ERROR", message, **kwargs)


def get_logger(service_name: str) -> StructuredLogger:
    """
    Factory function to get a structured logger for a service.
    
    Args:
        service_name: Component name (router, intent_parser, verify, etc.)
    
    Returns:
        StructuredLogger instance configured for the service
    
    Example:
        >>> logger = get_logger("router")
        >>> logger.info("Intent classified", correlation_id="abc-123", intent="action")
    """
    return StructuredLogger(service_name)
