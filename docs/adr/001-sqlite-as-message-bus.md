# ADR 001: SQLite as Message Bus

## Status

Accepted

## Context

Metis needs a communication channel between two separate processes: the domain MCP server (which enqueues tasks) and the metis-worker MCP server (which the dispatcher agent polls). Options considered:

1. **SQLite in WAL mode** — file-based database, concurrent read/write support
2. **Named pipes** — OS-level IPC, lower latency
3. **Filesystem watches** — file-per-task, inotify/FSEvents for notification
4. **External message broker** — Redis, RabbitMQ, etc.

## Decision

SQLite in WAL (Write-Ahead Logging) mode.

## Rationale

- **Zero infrastructure**: No external services to install, configure, or keep running. A single `.db` file is the entire bus.
- **Cross-process safety**: WAL mode supports concurrent readers and a single writer without corruption. `busy_timeout` pragma handles brief contention.
- **Atomic operations**: `UPDATE...RETURNING` in a single statement prevents double-claiming of tasks — critical for correctness with multiple dispatchers.
- **Persistence**: Tasks survive process restarts. Unclaimed tasks from a crashed dispatcher can be picked up by a replacement.
- **Debuggability**: Standard SQL queries inspect queue state. `sqlite3 metis.db "SELECT * FROM tasks"` shows everything.
- **Portability**: Works on all platforms (Windows, macOS, Linux) without platform-specific IPC.

## Trade-offs

- **Latency**: Polling-based (100ms intervals), not push-based. Acceptable for reasoning tasks that take seconds to execute.
- **Throughput ceiling**: SQLite write throughput is limited to ~1000 writes/sec. Acceptable — Metis dispatches reasoning tasks, not high-frequency messages.
- **File locking on Windows**: WAL mode behavior differs slightly. Mitigated with `busy_timeout=5000ms`.

## Consequences

- All inter-process communication goes through the shared SQLite file
- Both processes must agree on the database path (configured via `METIS_DB_PATH` or constructor argument)
- Schema migrations must be handled carefully (currently: `CREATE TABLE IF NOT EXISTS`)
