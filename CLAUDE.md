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

# Run the worker server
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.worker_server

# Run simulated dispatcher (for testing)
python examples/simulate_dispatcher.py --db ~/.metis/metis.db
```

## Architecture

Three components:

1. **`metis` (Python package)** — Shared library providing `TaskQueue`. Any MCP server imports this to enqueue work and wait for results. Uses SQLite in WAL mode as the message bus.

2. **`metis-worker` (MCP server)** — Exposes `poll()` and `deliver()` tools to a dispatcher agent.

3. **Dispatcher agent** — Background sub-agent that polls, executes tasks, and delivers results.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layer responsibilities and dependency rules. See [docs/PATTERNS.md](docs/PATTERNS.md) for the canonical vertical slice.

### Operational patterns

- **Fire-and-forget**: Queue work, return immediately. Results available on next call.
- **Block-and-wait**: Queue work, hold tool call open until result or timeout.
- **Self-healing**: Check heartbeat; if dispatcher dead, return `metis_dispatcher_required: true` signal.
- **Disposable contexts**: Worker agent contexts discarded after task completion.

## Layered Architecture

Follow the dependency rule: each layer depends only on layers interior to it.

```
Presentation → Application → Domain ← Infrastructure
```

- **Domain** (`src/metis/domain/`) — Entities, value objects, protocols, errors. Zero external imports. No I/O.
- **Application** (`src/metis/application/`) — Use cases. Depends on domain only. No I/O.
- **Infrastructure** (`src/metis/infrastructure/`) — SQLite stores, database init, `TaskQueue` facade. Depends on domain.
- **Presentation** (`src/metis/presentation/`) — FastMCP server with `poll()`, `deliver()` tools.

## Public API

```python
from metis import TaskQueue

queue = TaskQueue(db_path="~/.myserver/metis.db")
task_id = queue.enqueue(type="classify", payload={...}, ttl_seconds=60)
result = await queue.wait_for_result(task_id, timeout=30)
is_alive = queue.is_worker_alive()
```

`enqueue()` and `is_worker_alive()` are sync. `wait_for_result()` is async. See [ADR 002](docs/adr/002-sync-enqueue-async-wait.md) for why.

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

## Documentation

- [DESIGN.md](docs/DESIGN.md) — Full design document, use cases, open questions
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — Layer responsibilities and dependency rules
- [PATTERNS.md](docs/PATTERNS.md) — Canonical vertical slice reference
- [nellie-integration.md](docs/nellie-integration.md) — How Nellie (primary integration target) would use Metis
- [ai-ready-architecture.md](docs/ai-ready-architecture.md) — Architectural principles this project follows
