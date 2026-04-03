# Architecture

Metis follows a four-layer architecture with unidirectional dependencies.

## Layer Diagram

```
Presentation (MCP tools, CLI)  →  Application (use cases)  →  Domain (entities, protocols)  ←  Infrastructure (SQLite, facade)
```

## Layers

### Domain (`src/metis/domain/`)

Pure Python. Zero external imports. No I/O.

Contains entities (`Task`, `Heartbeat`), value objects (`TaskId`, `TaskStatus`, `TaskPriority`, `WorkerId`), error types (`Result[T]`, `Ok`, `Err`), and protocols (`TaskStore`, `HeartbeatStore`).

**May import:** Nothing outside the domain package.

**May NOT import:** Application, infrastructure, or presentation code. No third-party libraries.

### Application (`src/metis/application/`)

Use cases — one class per user action, each with a single `execute()` method. Thin orchestrators that coordinate domain entities and store protocols.

**May import:** Domain only.

**May NOT import:** Infrastructure or presentation code. No direct SQLite or FastMCP references.

### Infrastructure (`src/metis/infrastructure/`)

Implements domain protocols. Contains `SqliteTaskStore`, `SqliteHeartbeatStore`, `database.py` (schema/connection management), and the `TaskQueue` facade.

**May import:** Domain (to implement protocols and use entities). Third-party libraries (`aiosqlite`, `sqlite3`).

**May NOT import:** Application or presentation code (exception: `TaskQueue` facade lazily imports application use cases to wire the public API).

### Presentation (`src/metis/presentation/`)

Entry points — FastMCP server with `poll()` and `deliver()` tools. Thin: validate input, call use case, format output.

**May import:** Application (use cases) and domain (entities, value objects for type hints).

**May NOT import:** Infrastructure directly (receives wired use cases via lifespan initialization).

## Dependency Rule

Each layer depends only on layers interior to it. The domain is the innermost layer and depends on nothing. Interfaces (protocols) are defined in the domain; implementations live in infrastructure.

## The Deep Module: TaskQueue

`TaskQueue` (`src/metis/infrastructure/task_queue_facade.py`) is the public API facade. It exposes 3 methods and hides the 4-layer internals:

- `enqueue(type, payload, priority, ttl_seconds) -> TaskId` — sync
- `await wait_for_result(task_id, timeout) -> dict | None` — async
- `is_worker_alive(timeout_seconds) -> bool` — sync

Integrating MCP servers import only `from metis import TaskQueue` and never interact with the layers directly.
