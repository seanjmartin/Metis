# Integration Example: Smart Notes

A complete example MCP server that uses Metis for intelligent note classification, validation, and summarization. Demonstrates all three integration patterns.

## What it shows

1. **Programmatic `TaskQueue`** — `save_note()` enqueues classify and validate tasks, waits for results
2. **Embedded worker tools** — `register_worker_tools()` adds poll/deliver/probe to the server, so the dispatcher sub-agent connects here directly (no separate metis-worker process)
3. **Graceful degradation** — everything works without a dispatcher, just without intelligence. Returns `metis_dispatcher_required: true` signal when the dispatcher is missing.

## Architecture

```
Main conversation
  |
  +-- calls save_note("Lab results", "Cholesterol 195 mg/dL")
  |     |
  |     +-- enqueue(classify, ...) → SQLite
  |     +-- enqueue(validate, ...) → SQLite
  |     +-- wait_for_result(...)
  |
  +-- Dispatcher sub-agent (background)
        |
        +-- poll() → gets classify task → reasons → deliver()
        +-- poll() → gets validate task → reasons → deliver()
        |
        results flow back through SQLite to save_note()
  |
  +-- save_note returns: {category: "health", safe: true, ...}
```

## Setup

### 1. Install metis

```bash
pip install -e ".[worker]"
```

### 2. Configure MCP

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "smart-notes": {
      "command": "python",
      "args": ["examples/integration/server.py"],
      "cwd": "/path/to/metis",
      "env": { "METIS_DB_PATH": "C:/temp/metis.db" }
    }
  }
}
```

Note: only one MCP server entry. The worker tools are embedded in smart-notes — no separate metis-worker.

### 3. Start the dispatcher

In your conversation, spawn a background sub-agent:

> Spawn a background sub-agent that acts as a Metis dispatcher. It should
> call poll(timeout=55) on the smart-notes MCP server, process tasks
> (classify, validate, summarize), and deliver results. Keep polling
> until 3 consecutive empty polls.

### 4. Use the tools

```
> save_note(title="Lab results", content="Cholesterol 195 mg/dL, recheck in 6 months")
{"saved": true, "category": "health", "validated": true, "safe": true, "risks": []}

> summarize_notes(titles=["Lab results", "Gym membership renewal", "Annual physical"])
{"summary": "Health and fitness tracking — lab work, gym membership, and medical checkups.", "intelligent": true}
```

## Without a dispatcher

If no dispatcher is running, the tools still work:

```
> save_note(title="Lab results", content="Cholesterol 195 mg/dL")
{"saved": true, "category": "uncategorized", "validated": false, "metis_dispatcher_required": true, ...}
```

The `metis_dispatcher_required` signal tells the calling LLM to spawn a dispatcher. Once spawned, subsequent calls get intelligent results.
