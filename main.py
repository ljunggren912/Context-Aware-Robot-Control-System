"""
Context-Aware Robot Control System
Entry point for CLI interface.
See docs/ for architecture documentation.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

def main():
    """
    Main entry point for robot control system.
    Loads configuration and starts CLI interface.
    """

    # Load environment configuration
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    
    # Import CLI after .env loaded (modules may read env vars on import)
    from src.cli.interface import run_cli_session
    
    # Start CLI session
    try:
        run_cli_session()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
