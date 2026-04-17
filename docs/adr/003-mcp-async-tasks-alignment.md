# ADR 003: MCP Async-Tasks Spec Alignment (2025-11-25)

## Status

Accepted — shipped in v0.2.0.

## Context

The MCP specification released 2025-11-25 standardised **async tasks**, **cancellation**, **progress notifications**, **elicitation**, and **sampling** as protocol-level primitives. Before this release, Metis had shipped its own task-queue semantics with its own vocabulary (`pending` / `claimed` / `complete` / `consumed` / `expired`) and its own tool envelopes (`{"status": "complete", ...}`).

When the spec landed, we faced three questions:

1. Do we align with the spec at all, or keep our own shapes and let the spec absorb adjacent territory over time?
2. If we align, do we refactor the internal state machine to the spec's vocabulary natively, or translate at the presentation boundary?
3. How do we surface the new capabilities (cancel, progress, elicitation, sampling) without bolting them onto the existing `get_result` call shape?

Metis is pre-1.0 with no pinned external consumers, so breaking changes were cheap.

## Decision

Align fully at the presentation boundary; preserve internal richness.

1. **Keep the internal state machine.** `CONSUMED` (read-receipt) and `EXPIRED` (TTL failure distinct from application failure) carry meaning the spec doesn't capture. Adding `CANCELLED`, `FAILED`, and `INPUT_REQUIRED` extends the internal model without collapsing it.
2. **Translate at the edge.** A pure function `metis.domain.spec_mapping.internal_to_spec_status()` maps internal `TaskStatus` to the five spec strings. All trigger tools use it in their response envelopes.
3. **Clean break on response shape.** `get_result` and `enqueue` now return the spec-compliant envelope `{"task": {"id", "status"}, "result"?, "error"?, "metis": {...}}`. No parallel old-shape; no deprecation cycle. Version bumped to `0.2.0`.
4. **Transparent elicitation loop.** When a task enters `INPUT_REQUIRED` mid-wait, `TaskQueue.wait_for_result(..., ctx)` calls `ctx.elicit()` on the originating client, writes the response back, and continues waiting — invisible to the caller.
5. **Progress forwarded through the existing progressToken channel.** A background drain coroutine tails the `task_progress` table and invokes `ctx.report_progress(...)` — per the spec's requirement that `progressToken` stays valid for the task lifetime.
6. **Cancellation with terminal-state guard + authorization context.** `cancel(task_id)` returns the spec JSON-RPC `-32602` code when invoked on a terminal task. Terminal tasks never transition further — enforced both in the entity and at the SQL layer. When a caller supplies a `session_id`, a cross-session cancel is rejected as `TaskNotFoundError` so task IDs from other sessions are not enumerable (spec §Security: "receivers MUST bind tasks to the authorization context"). The trigger-side MCP tool threads this automatically from the per-request session context; programmatic callers pass `session_id=` explicitly or leave it `None` in single-tenant deployments.
7. **Keep Metis-distinctive features unaligned.** `CONSUMED` state, `capabilities_required`, `session_id`, shared dispatcher pool, SQLite persistence, self-heal protocol — none are in the spec, all preserved.

## Alternatives considered and rejected

- **Parallel universe.** Ignore the spec, keep shipping our own shapes. Rejected: as MCP clients adopt async tasks natively, Metis-backed tools would look non-standard and miss native polling/cancel support on the client side.
- **Native refactor.** Rename `CONSUMED`→`completed` (fetched), fold `EXPIRED` into `failed`, lose the distinction. Rejected: the `CONSUMED` read-receipt is used internally to prevent double-delivery; losing it would require inventing a parallel mechanism (a `fetched` boolean flag) that's uglier than the translation layer.
- **Dual response shape.** Return both old and new shapes for one release. Rejected: doubles the test surface, makes the spec-compliant path second-class forever, and there are no external pinned consumers to protect.
- **Explicit elicitation round-trip.** `get_result` returns `{status: input_required, prompt, schema}` and the caller is responsible for responding and re-polling. Rejected: transparent is more spec-aligned and more ergonomic; the implicit loop is the win.

## Rationale

The translation layer is ~15 lines (`spec_mapping.py`). It's a trivial, well-tested boundary. Every other cost (refactor churn, lost internal semantics, parallel shapes) was higher.

Aligning now positions Metis as *a standards-compliant implementation of async tasks with an unusually powerful backend* (shared dispatcher pool, SQLite persistence, session isolation) rather than *a parallel universe trying to replace the spec*. It lets Metis ride the standard's adoption curve rather than fight it.

Breaking `get_result` cleanly was the right call because Metis is pre-1.0. If it had been post-1.0 with pinned callers, we'd have shipped dual-shape with a deprecation window.

## Trade-offs

- **Internal/spec vocabulary mismatch.** Developers reading tests see `TaskStatus.CONSUMED` but clients see `"completed"`. Mitigated by the single-function translation layer and docstrings on every tool that mentions the mapping.
- **Breaking change for anyone who pinned v0.1.**  Accepted as the cost of pre-1.0 freedom.
- **Transparent elicitation can deadlock client implementations** that don't handle sampling/elicitation during an outstanding tool call. Documented as a known limitation; we'll add guards when we see real failures.

## Consequences

- Three new internal states (`CANCELLED`, `FAILED`, `INPUT_REQUIRED`) with their own transitions.
- New columns on `tasks` (`error_code`, `error_message`, `cancelled_at`, `input_prompt`, `input_schema`, `input_response`, `input_seq`) + new `task_progress` table. Migrations applied idempotently to legacy DBs.
- `TaskQueue.wait_for_result(..., ctx)` now orchestrates progress + elicitation instead of being a simple poll.
- Five new worker tools for the dispatcher (`report_progress`, `check_cancelled`, `request_input`, `await_input_response`, `request_sampling`, `await_sampling_response`).
- 49 new tests (197 → 222 later with the bridge; 197 at v0.2.0 release).

## References

- [MCP async-tasks specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- [SEP-1686: Tasks proposal](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1686)
- `src/metis/domain/spec_mapping.py` — the translation boundary
- `src/metis/domain/entities.py` — the extended state machine and transitions
