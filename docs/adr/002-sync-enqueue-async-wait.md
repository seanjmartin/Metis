# ADR 002: Sync Enqueue, Async Wait

## Status

Accepted

## Context

The `TaskQueue` public API needs two operations: enqueue a task and wait for its result. Integrating MCP servers may call these from either sync or async contexts.

## Decision

- `enqueue()` is **synchronous** (uses stdlib `sqlite3`)
- `wait_for_result()` is **async** (uses `aiosqlite` for the polling loop)
- `is_worker_alive()` is **synchronous** (simple read query)

## Rationale

**Sync enqueue** allows fire-and-forget from any context. MCP tool handlers in FastMCP can be either sync or async. Making enqueue synchronous means any MCP server can dispatch work without async gymnastics — just `queue.enqueue(type=..., payload=...)` and move on.

**Async wait** is necessary because the polling loop (`sleep` between checks) must not block the event loop. This matches the block-and-wait pattern where the MCP tool handler is already async.

## Trade-offs

- **Dual connection management**: The facade maintains a sync `sqlite3.Connection` for enqueue/health checks and creates a separate async `aiosqlite.Connection` for wait_for_result. This adds complexity but keeps the public API ergonomic.
- **No sync wait option**: If an integrator needs to wait synchronously, they must use `asyncio.run()`. This is uncommon — most MCP servers that need results are already async.

## Consequences

- `TaskQueue.__init__` lazily creates only the sync connection
- `wait_for_result()` creates and closes its own async connection per call
- Both connections share the same SQLite database file with WAL mode handling concurrency
