# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Metis is an intelligence-on-demand library for MCP servers. It lets MCP servers dispatch reasoning tasks to background LLM agents via a SQLite-backed task queue, so the main conversation stays clean and focused.

Named for the Greek Titaness of wisdom — MCP servers "consume" Metis to gain reasoning ability.

## Build & Test Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run tests by layer
pytest tests/domain/           # Pure logic, no I/O
pytest tests/application/      # Use cases with real SQLite
pytest tests/infrastructure/   # SQLite store operations
pytest tests/presentation/     # MCP server contract tests

# Run a single test
pytest tests/application/test_roundtrip.py -v

# Lint
ruff check src/ tests/
ruff format --check src/ tests/

# Run the worker server (standalone)
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.worker_server

# Run the trigger server (standalone)
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.trigger_server

# Run simulated dispatcher (for testing)
python examples/simulated/dispatcher.py --db ~/.metis/metis.db

# Probe MCP client timeout (via metis-worker's probe tool)
# Call probe(duration=25), probe(duration=50), etc. to find the limit
# Set METIS_POLL_TIMEOUT to (last successful - 5 seconds)
```

See [docs/DISPATCHER.md](docs/DISPATCHER.md) for Claude Code and VS Code Copilot MCP configuration.

## Architecture

Four components:

1. **`metis` (Python package)** — Shared library providing `TaskQueue` and embeddable tool registration. Any MCP server imports this to enqueue work and wait for results. Uses SQLite in WAL mode as the message bus.

2. **`metis-worker` (MCP server)** — Exposes `poll()`, `deliver()`, and `probe()` tools to a dispatcher agent. Supports long-polling via `timeout` parameter.

3. **`metis-trigger` (MCP server)** — Exposes `enqueue()`, `get_result()`, and `check_health()` tools for the main conversation or testing.

4. **Dispatcher agent** — Background sub-agent that polls, executes tasks (possibly spawning its own children for complex work), and delivers results.

Both MCP servers share the same SQLite database via `METIS_DB_PATH`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layer responsibilities and dependency rules. See [docs/PATTERNS.md](docs/PATTERNS.md) for the canonical vertical slice.

### Operational patterns

- **Fire-and-forget**: Queue work, return immediately. Results available on next call.
- **Block-and-wait**: Queue work, hold tool call open until result or timeout.
- **Long-poll**: `poll(timeout=55)` blocks server-side, only returning when a task appears or timeout expires. Minimizes idle dispatcher token cost.
- **Self-healing**: Check heartbeat; if dispatcher dead, return `metis_dispatcher_required: true` signal.
- **Disposable contexts**: Worker agent contexts discarded after task completion.
- **Hybrid routing**: Dispatcher handles simple tasks directly, spawns sub-agents for complex work (browser, research).

## Layered Architecture

Follow the dependency rule: each layer depends only on layers interior to it.

```
Presentation → Application → Domain ← Infrastructure
```

- **Domain** (`src/metis/domain/`) — Entities, value objects, protocols, errors. Zero external imports. No I/O.
- **Application** (`src/metis/application/`) — Use cases. Depends on domain only. No I/O.
- **Infrastructure** (`src/metis/infrastructure/`) — SQLite stores, database init, `TaskQueue` facade. Depends on domain.
- **Presentation** (`src/metis/presentation/`) — FastMCP servers (`worker_server.py`, `trigger_server.py`) and embeddable tool registration (`worker_tools.py`, `trigger_tools.py`).

## Public API

### Programmatic (for MCP server code)

```python
from metis import TaskQueue

async with TaskQueue(db_path="~/.myserver/metis.db") as queue:
    task_id = queue.enqueue(
        type="classify",
        payload={...},
        ttl_seconds=60,
        capabilities_required=["browse-as-me"],  # only workers with this capability can claim
        session_id="user-alice",  # scope to a session (optional, for multi-user)
    )
    result = await queue.wait_for_result(task_id, timeout=30)
    is_alive = queue.is_worker_alive()
```

`enqueue()` and `is_worker_alive()` are sync. `wait_for_result()` is async. See [ADR 002](docs/adr/002-sync-enqueue-async-wait.md) for why.

`wait_for_result()` returns `dict | None` (`None` on timeout) and raises `MetisException` with a typed `.error` attribute (e.g. `TaskExpiredError`, `TaskNotFoundError`) on domain failures. `TaskQueue` supports both `async with` and `with` — or call `.close()` explicitly.

The `deliver()` tool accepts optional `input_tokens` and `output_tokens` for per-task cost tracking.

### Embeddable (for hosting tools in an existing MCP server)

```python
from metis.presentation.worker_tools import register_worker_tools
from metis.presentation.trigger_tools import register_trigger_tools

# Add dispatcher tools to your MCP server
mcp = FastMCP("my-server", lifespan=my_lifespan)
worker_handle = register_worker_tools(mcp, db_path="~/.myserver/metis.db")

# Or add trigger tools for conversational testing
trigger_handle = register_trigger_tools(mcp, db_path="~/.myserver/metis.db")
```

For HTTP multi-user servers, pass a callable that resolves the current session:

```python
worker_handle = register_worker_tools(
    mcp, db_path="~/.myserver/metis.db", session_id=get_current_session_id
)
trigger_handle = register_trigger_tools(
    mcp, db_path="~/.myserver/metis.db", session_id=get_current_session_id
)
```

See [examples/http_multiuser/](examples/http_multiuser/) for a complete multi-user example.

This eliminates the need for separate `metis-worker` / `metis-trigger` processes.

## Code Conventions

### Patterns to follow

| Concern | Pattern |
|---------|---------|
| Data access | Repository — interface in domain, implementation in infrastructure |
| Business workflows | Use Case — one class, one `execute()` method, typed input/output |
| Entry points | Tool — thin: validate input → call use case → format output |
| Errors | `Result[T]` for business errors, exceptions for infrastructure failures |

### Typing and naming

- No `Any` or untyped holes — every parameter and return has a named type
- Domain IDs are branded types (`TaskId`, `WorkerId`), never raw `str`
- Enums over magic strings (`TaskStatus.PENDING`, not `"pending"`)
- Files named after primary export (`enqueue_task.py`, not `utils.py`)
- Methods are verbs; entities are nouns
- Booleans use `is_`/`has_`/`can_` prefixes
- Infrastructure carries its tech: `SqliteTaskStore`, not `TaskStore`

### Module docstrings include "NOT responsible for"

```python
class TaskQueue:
    """Enqueues tasks and waits for results via SQLite.

    NOT responsible for:
    - Executing tasks (see dispatcher agent)
    - Exposing MCP tools (see worker_server)
    """
```

### Testing by layer

| Layer | Test type | Characteristics |
|-------|-----------|----------------|
| Domain | Unit tests | Pure logic, no I/O, fast |
| Application | Integration tests | Use cases with real SQLite |
| Infrastructure | Infrastructure tests | Real SQLite store operations |
| Presentation | Contract tests | Input parsing and output formatting only |

Test names describe behavior: `test_should_reject_expired_task`.

## Key Design Decisions

- **SQLite WAL as message bus** — see [ADR 001](docs/adr/001-sqlite-as-message-bus.md)
- **Sync enqueue / async wait** — see [ADR 002](docs/adr/002-sync-enqueue-async-wait.md)
- **Atomic `claim_next`** — `UPDATE...RETURNING` prevents double-claiming by concurrent dispatchers
- **Metis is always optional** — every integration point has a deterministic fallback
- **Long-poll** — `poll(timeout=N)` blocks server-side to minimize idle token cost
- **Embeddable tools** — `register_worker_tools()` / `register_trigger_tools()` let MCP servers host Metis tools directly

## Documentation

- [DESIGN.md](docs/DESIGN.md) — Full design document, use cases, positioning
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — Layer responsibilities and dependency rules
- [PATTERNS.md](docs/PATTERNS.md) — Canonical vertical slice reference
- [DISPATCHER.md](docs/DISPATCHER.md) — Dispatcher architecture, prompts, long-poll tuning, routing
- [ai-ready-architecture.md](docs/ai-ready-architecture.md) — Architectural principles this project follows
