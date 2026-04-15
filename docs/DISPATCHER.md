# Dispatcher Architecture

The dispatcher is a background sub-agent that polls for tasks, reasons about them, and delivers results. It's the runtime component that gives MCP servers their intelligence.

## How it works

```
Main conversation
  |
  +-- MCP server enqueues task via TaskQueue
  |
  +-- Dispatcher sub-agent (background, long-running)
        |
        +-- poll(timeout=55) → waits for work
        +-- task arrives → reasons about it
        +-- deliver(task_id, result) → result flows back through SQLite
        +-- poll again
  |
  +-- MCP server gets result via wait_for_result()
  +-- Main conversation gets intelligent answer
```

The main conversation stays clean. It never sees the reasoning work.

## Dispatcher types

### Simple dispatcher

Handles all tasks directly in its own context. Best for classification, validation, summarization — tasks that need reasoning but no external tools.

```
You are a Metis dispatcher agent. Your job is to poll for tasks, process them,
and deliver results. You run autonomously — do not ask for confirmation.

LOOP:
1. Call poll(worker_id="dispatcher", capabilities=["classify", "summarize", "validate"], timeout=55).
2. If result has "s": "e" — timeout expired, no tasks. Call poll again. Say nothing.
3. If result has "s": "t" — process the task:
   - Read the "type" and "payload.instructions"
   - Reason about the task and produce a structured JSON result
   - Call deliver(task_id=<id>, result=<your JSON result>)
4. After delivering, call poll again immediately.

Never produce text output between tool calls. Only call tools.
After 3 consecutive empty polls (each is ~55 seconds), stop.
```

### Routing dispatcher

Handles simple tasks directly and delegates complex ones to specialized sub-agents. The dispatcher is a router — it stays lean while children do heavy work.

```
Dispatcher sub-agent (long-running, polls continuously)
  |
  +-- poll() → classify task → handles directly → deliver()
  +-- poll() → validate task → handles directly → deliver()
  +-- poll() → research task → spawns child sub-agent
        |
        child (disposable, has Read/Grep/Glob + deliver access)
          +-- reads files, searches codebase
          +-- deliver() → result flows back through SQLite
          +-- context discarded
  +-- poll() → continues...
```

Simple tasks (classify, validate, summarize) stay in the dispatcher — fast, no spawn overhead. Complex tasks (research, browser extraction) get their own sub-agent with specialized tools. The child calls `deliver()` directly, so the dispatcher doesn't wait.

```
You are a Metis routing dispatcher. You poll for tasks, handle simple ones
directly, and spawn specialized sub-agents for complex ones. Run autonomously.

ROUTING RULES:
- classify, validate, summarize → handle directly (reason + deliver)
- research → spawn a sub-agent with Read, Grep, Glob tools

LOOP:
1. Call poll(worker_id="dispatcher", capabilities=["classify", "summarize", "validate", "research"], timeout=55).
2. If result has "s": "e" — call poll again. Say nothing.
3. If result has "s": "t" — check the task type:

   DIRECT (classify, validate, summarize):
   - Reason about the task based on payload.instructions
   - Call deliver(task_id=<id>, result=<your JSON result>)
   - Call poll again immediately

   DELEGATED (research):
   - Spawn a sub-agent with this prompt:
     "You are a Metis research worker. Your task: <payload.instructions>.
      When done, call deliver(task_id='<id>', result=<your JSON result>)."
   - Do NOT wait for the sub-agent — call poll again immediately

Never produce text output between tool calls. Only call tools.
After 3 consecutive empty polls, stop.
```

## Long-poll and idle cost

Without long-poll, each empty `poll()` is a full LLM round-trip (~20 tokens). With long-poll, the call blocks server-side for up to `timeout` seconds, only returning when a task appears or the timeout expires.

| Mode | Idle cost for 5 minutes |
|------|------------------------|
| Rapid poll (timeout=0) | ~100 calls × 20 tokens = ~2000 tokens |
| Long-poll (timeout=55) | ~5 calls × 20 tokens = ~100 tokens |

### Timeout tuning

The `timeout` parameter must be shorter than the MCP client's tool call timeout. Use the `probe` tool on `metis-worker` to discover the limit:

```
Call probe(duration=25)  → completes → 25s works
Call probe(duration=50)  → completes → 50s works  
Call probe(duration=75)  → completes → 75s works
Call probe(duration=120) → killed    → limit is between 75 and 120
→ Set timeout to 70 (75 minus 5 second safety margin)
```

In testing, Claude Code showed no timeout up to 10 minutes. The 55-second default is conservative.

### Configuration

- **Per-call:** `poll(timeout=55)` in the dispatcher prompt
- **Server default:** `METIS_POLL_TIMEOUT=55` environment variable
- **Fallback:** 0 (instant return, backward compatible)

## Capability filtering

Tasks can specify `capabilities_required` at enqueue time. The `claim_next` SQL only returns tasks whose required capabilities are a subset of the polling worker's capabilities.

```python
# Only workers with browse-as-me can claim this task
queue.enqueue(
    type="browser_extract",
    payload={...},
    capabilities_required=["browse-as-me"],
)
```

Workers without the required capabilities will never see the task — it stays pending until a capable worker polls.

## Session isolation

When multiple users share the same MCP server (e.g., over HTTP), tasks must be scoped so each user's dispatcher only processes their own work.

Tasks carry an optional `session_id`:

```python
queue.enqueue(
    type="classify",
    payload={...},
    session_id="alice",  # only Alice's dispatcher can claim this
)
```

The poll tool filters by session_id automatically when the server is configured with one:

```python
register_worker_tools(mcp, db_path="...", session_id=get_current_session_id)
```

The poll response includes `"sid"` when a task has a session_id:

```json
{"s": "t", "id": "...", "type": "classify", "payload": {...}, "sid": "alice"}
```

This lets the dispatcher pass the session identity through on any tool calls it makes back to the MCP server (e.g., via HTTP headers), so those calls execute in the correct user context.

For stdio deployments (single user per process), session_id is not needed — it defaults to None and all tasks are claimable by any worker.

See [examples/http_multiuser/](../examples/http_multiuser/) for a complete working example with two users.

## Cost tracking

The dispatcher can report token usage alongside results:

```
deliver(task_id=<id>, result={...}, input_tokens=1500, output_tokens=500)
```

Token counts are stored per-task in SQLite. The `get_result()` trigger tool returns them alongside the result when available.

## Client setup

### Claude Code

Add to `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "metis-worker": {
      "command": "python",
      "args": ["-m", "metis.presentation.worker_server"],
      "env": { "METIS_DB_PATH": "~/.metis/metis.db" }
    },
    "metis-trigger": {
      "command": "python",
      "args": ["-m", "metis.presentation.trigger_server"],
      "env": { "METIS_DB_PATH": "~/.metis/metis.db" }
    }
  }
}
```

Spawn the dispatcher as a background sub-agent using the Agent tool. The sub-agent inherits MCP tool access from the parent session.

### VS Code Copilot

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "metis-worker": {
      "command": "python",
      "args": ["-m", "metis.presentation.worker_server"],
      "env": { "METIS_DB_PATH": "~/.metis/metis.db" }
    },
    "metis-trigger": {
      "command": "python",
      "args": ["-m", "metis.presentation.trigger_server"],
      "env": { "METIS_DB_PATH": "~/.metis/metis.db" }
    }
  }
}
```

Define a custom agent at `.github/agents/metis-dispatcher.md` with `tools: [metis-worker]` in the frontmatter. This gives the sub-agent explicit MCP tool access. Invoke with `@metis-dispatcher` in Copilot Agent mode.

**Important:** Bare `#runSubagent` does not inherit MCP tools — a named custom agent with explicit tool configuration is required.

Tested with GPT-4.1 — full round-trip works (enqueue via trigger, dispatch via custom agent, retrieve results).

## Self-healing

If the dispatcher dies (process crash, max turns reached, idle timeout), the MCP server detects it via `is_worker_alive()` — the heartbeat goes stale. The server returns a degraded response with `metis_dispatcher_required: true`, signaling the calling LLM to spawn a new dispatcher.

The next poll from the new dispatcher picks up where the old one left off — unclaimed tasks are still in the queue. Claimed-but-undelivered tasks expire via TTL.
