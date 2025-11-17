# Context-Aware Robot Control System
## About

This project was developed as part of a 7.5 credit project course in the **Master's in AI & Automation** program at **University West, Trollhättan**. The aim was to create a proof-of-concept system demonstrating natural language-to-robot action translation, enabling operators to control industrial robots using conversational commands.

## Prerequisites

- Python 3.10+
- Neo4j database (local or remote)
- OpenAI API key or Ollama installation

## Technical Documentation

- **[00 System Overview](docs/00_SYSTEM_OVERVIEW.md)** – Architecture and complete workflow
- **[01 Router and Intent Parsing](docs/01_ROUTER_AND_INTENT_PARSING.md)** – Intent classification and goal extraction
- **[02 Sequence Planning](docs/02_SEQUENCE_PLANNING.md)** – Deterministic step planning with graph algorithms
- **[03 Human Review](docs/03_HUMAN_REVIEW.md)** – Operator approval gate
- **[04 Verification](docs/04_VERIFICATION.md)** – Safety validation and YAML conversion
- **[05 Execution](docs/05_EXECUTION.md)** – Robot control and state updates
- **[06 Knowledge Layer](docs/06_KNOWLEDGE_LAYER.md)** – Neo4j and SQLite database schemas

## Installation

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Configuration**

Required environment variables in `.env`:

```dotenv
# Neo4j Knowledge Graph
# ----------------------------------------------------------------------------
NEO4J_URI=neo4j://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password_here

# ----------------------------------------------------------------------------
# SQLite Databases
# ----------------------------------------------------------------------------
SQLITE_STATE_DB=./data/robot_state.db
SQLITE_HISTORY_DB=./data/history.db

# ----------------------------------------------------------------------------
# Robot Execution Mode
# - "simulation": Safe for testing, logs to actions.yaml, 0.5s per step
# - "socket": Production mode
# ----------------------------------------------------------------------------
ROBOT_EXECUTION_MODE=simulation
ROBOT_SOCKET_HOST=127.0.0.1
ROBOT_SOCKET_PORT=5000

# ----------------------------------------------------------------------------
# LLM Configuration
# - MODEL_PROVIDER: "openai" or "ollama"
# ----------------------------------------------------------------------------

MODEL_PROVIDER=openai
MODEL_NAME=gpt-4o-mini
OPENAI_API_KEY=sk-your-openai-api-key-here

# For Ollama (if switching to local):
#OLLAMA_MODEL=gpt-oss-20b-60k:latest
#OLLAMA_URL=http://localhost:11434

# ----------------------------------------------------------------------------
# Planning & Retry Logic
# ----------------------------------------------------------------------------
MAX_PLAN_ATTEMPTS=3

# Human review timeout (seconds)
HUMAN_REVIEW_TIMEOUT=120
```

See `.env.example` for complete configuration template.


3. **Seed Neo4j database**:
   
   **WARNING**: ==The docs/seed_full.cypher script purges the entire Neo4j database before seeding!==
   
   Load the script using your preferred method:
   - Neo4j Browser: Copy/paste the contents into the query editor
   - Neo4j Desktop: Open and run the file directly
   - Command line: `cypher-shell -u neo4j -p your_password < docs/seed_full.cypher`
   - Python driver or other Neo4j clients

   **Customization Note**: You can modify the domain-specific content (positions, tools, routines) to match your robot setup, but you **must maintain valid node types, properties, and relationships**:
   - **Node Types**: `Position`, `Tool`, `ToolStand`, `Routine` with their required properties
   - **Relationships**: `ONLY_ALLOWED_MOVE_TO`, `TOOL_AVAILABLE_AT`, `LOCATED_AT`, `SUPPORTED_AT`
   - **Position Roles**: `home`, `safe_approach`, `tool_mount`, `work`
   
   See [06_KNOWLEDGE_LAYER.md](docs/06_KNOWLEDGE_LAYER.md) for the complete schema specification.

4. **Run the system**:
```bash
python main.py
```

## Example Commands

Once running, try these commands:

- `What positions are available?` – Query workspace
- `Move to Home` – Navigate robot
- `Get the camera and inspect position 1` – Multi-step task
- `Do a full weld then go home` – Complex operation
- `Do that again` – Replay last task
- `exit` – Quit



## Project Structure

```
Context Aware Robot/
├── main.py                         # Entry point
├── requirements.txt                # Python dependencies
├── .env.example                    # Configuration template
├── .gitignore                      # Git ignore rules
├── docs/                           # Technical documentation
│   ├── 00_SYSTEM_OVERVIEW.md       # Architecture overview
│   ├── 01_ROUTER_AND_INTENT_PARSING.md
│   ├── 02_SEQUENCE_PLANNING.md
│   ├── 03_HUMAN_REVIEW.md
│   ├── 04_VERIFICATION.md
│   ├── 05_EXECUTION.md
│   ├── 06_KNOWLEDGE_LAYER.md
│   └── seed_full.cypher            # Neo4j database seed
├── src/                            # Source code
│   ├── cli/
│   │   └── interface.py            # Terminal interface
│   └── core/
│       ├── knowledge/
│       │   ├── neo4j_client.py     # Graph database client
│       │   └── sqlite_client.py    # State/history DB client
│       ├── llm/
│       │   └── client.py           # LLM provider abstraction
│       ├── observability/
│       │   └── logging.py          # Structured JSON logging
│       ├── robot/
│       │   ├── executor.py         # Sequence execution
│       │   ├── socket_client.py    # Robot communication
│       │   └── socket_client_class.py
│       ├── translation/
│       │   ├── workflow.py         # LangGraph orchestration
│       │   ├── state.py            # Workflow state schema
│       │   ├── sequence_builder.py # Deterministic planner
│       │   └── nodes/              # Workflow nodes
│       │       ├── router.py
│       │       ├── intent_parser.py
│       │       ├── sequence_planning.py
│       │       ├── human_review.py
│       │       ├── verify.py
│       │       ├── robot.py
│       │       ├── question.py
│       │       └── fallback.py
│       └── verification/
│           ├── verifier.py         # Safety validation
│           └── yaml_converter.py   # Plan → YAML conversion
├── data/                           # SQLite databases (created at runtime)
│   ├── robot_state.db
│   └── history.db
├── logs/                           # JSON logs (created at runtime)
└── actions.yaml                    # Robot sequence output (created at runtime)
```

