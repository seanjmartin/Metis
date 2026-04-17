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

Entry points — FastMCP servers and embeddable tool registration. Thin: validate input, call use case, format output.

Contains:
- `worker_tools.py` — embeddable `register_worker_tools(mcp, db_path, session_id)` for poll/deliver/probe
- `trigger_tools.py` — embeddable `register_trigger_tools(mcp, db_path, session_id)` for enqueue/get_result/check_health
- `worker_server.py` — standalone metis-worker MCP server (uses worker_tools internally)
- `trigger_server.py` — standalone metis-trigger MCP server (uses trigger_tools internally)

**May import:** Application (use cases) and domain (entities, value objects for type hints).

**May NOT import:** Infrastructure directly (receives wired use cases via lifespan initialization).

## Dependency Rule

Each layer depends only on layers interior to it. The domain is the innermost layer and depends on nothing. Interfaces (protocols) are defined in the domain; implementations live in infrastructure.

## The Deep Module: TaskQueue

`TaskQueue` (`src/metis/infrastructure/task_queue_facade.py`) is the public API facade. It exposes 3 methods and hides the 4-layer internals:

- `enqueue(type, payload, priority, ttl_seconds, capabilities_required, session_id) -> TaskId` — sync
- `await wait_for_result(task_id, timeout, ctx=None) -> dict | None` — async; raises `MetisException` (wrapping a typed `MetisError` such as `TaskExpiredError`, `TaskCancelledError`, `TaskFailedError`) on domain failure. `ctx` (a FastMCP `Context`) is used to forward progress and elicitation.
- `await cancel(task_id) -> None` — async; raises `MetisException(TaskAlreadyTerminalError)` if the task is already terminal.
- `await provide_input(task_id, response) -> None` — async; used by the transparent elicitation loop inside `get_result`.
- `is_worker_alive(timeout_seconds) -> bool` — sync

`TaskQueue` supports both sync and async context managers — prefer `async with TaskQueue(...) as queue:` so the underlying sync connection is closed on exit.

## MCP async-tasks spec alignment

The trigger-side MCP tools (`enqueue`, `get_result`, `cancel`, `provide_input`) return the spec-compliant task envelope:

```
{"task": {"id": "<uuid>", "status": "working"|"input_required"|"completed"|"failed"|"cancelled"}}
```

Completed tasks include `"result"` and optionally `"metis": {"input_tokens", "output_tokens"}`. Failures include `"error": {"code", "message"}`. Internal `TaskStatus` values are translated to spec vocabulary by `metis.domain.spec_mapping.internal_to_spec_status()`.

Integrating MCP servers import only `from metis import TaskQueue` and never interact with the layers directly.

## Two MCP Servers, One Database

```
metis-trigger (enqueue side)          metis-worker (dispatcher side)
  enqueue() ──┐                    ┌── poll(timeout=55)
  get_result() │  ← SQLite WAL →  │  deliver()
  check_health()┘                    └── probe()
```

Both servers share the same SQLite database via `METIS_DB_PATH`. The trigger server is for the main conversation (enqueue tasks, get results). The worker server is for the dispatcher sub-agent (poll for work, deliver results).

For standalone deployment, run them as separate processes. For embedded deployment, use `register_worker_tools()` / `register_trigger_tools()` to add Metis tools to an existing MCP server — no separate processes needed.

## Session Isolation

Tasks carry an optional `session_id` that scopes them to a specific user or session. This is essential for HTTP-based MCP servers where multiple users share the same process.

- `enqueue(session_id="alice")` stamps the session on the task
- `claim_next(session_id="alice")` only returns tasks for that session
- `claim_next(session_id=None)` returns any task (backward compatible)

For embedded tools, `session_id` can be a callable that resolves per-request (e.g., reading from a contextvar set by HTTP middleware):

```python
register_worker_tools(mcp, db_path="...", session_id=get_current_session_id)
register_trigger_tools(mcp, db_path="...", session_id=get_current_session_id)
```

The poll response includes `"sid"` when a task has a session_id, so the dispatcher can pass it through on subsequent tool calls.

See [examples/http_multiuser/](../examples/http_multiuser/) for a complete working example.

## Long-Poll

`poll(timeout=N)` blocks server-side, checking SQLite every 1 second and updating the heartbeat every 15 seconds. Returns immediately when a task appears, or `{"s": "e"}` after timeout. This minimizes idle token cost — the dispatcher LLM only sees a response when there's actual work.

Use `probe(duration=N)` on `metis-worker` to discover the MCP client's timeout limit. Set `METIS_POLL_TIMEOUT` to the discovered limit minus 5 seconds.
