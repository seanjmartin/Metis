# Live Dispatcher Demo

This demonstrates Metis end-to-end: an MCP server enqueues reasoning tasks, and an LLM dispatcher (connected via `metis-worker`) processes them.

## Setup

### 1. Configure metis-worker as an MCP server

Add to your Claude client's MCP configuration (`.mcp.json`, `claude_desktop_config.json`, etc.):

```json
{
  "mcpServers": {
    "metis-worker": {
      "command": "python",
      "args": ["-m", "metis.presentation.worker_server"],
      "env": { "METIS_DB_PATH": "C:/temp/metis.db" }
    }
  }
}
```

### 2. Run the task enqueuer

In a terminal:

```bash
python examples/live/enqueue_tasks.py --db C:/temp/metis.db
```

This enqueues three tasks (classify, summarize, validate) and waits for results.

### 3. Dispatch from the LLM conversation

In your Claude conversation (which has `metis-worker` MCP access), tell the LLM:

> You are a Metis dispatcher. Call the poll tool. If the result has "s": "e",
> call poll again. If it has "s": "t", process the task based on its type
> and the instructions in the payload, then call deliver with the task_id
> and your result as a JSON object. Then poll again.

The LLM will poll for tasks, reason about each one, and deliver structured results. The enqueue script will print the results as they arrive.

## What's happening

```
Terminal 2 (enqueue_tasks.py)          Claude conversation (dispatcher)
  |                                      |
  +-- enqueue(classify, ...)             |
  +-- enqueue(summarize, ...)            |
  +-- enqueue(validate, ...)             |
  +-- wait_for_result(...)               |
  |                                      +-- poll() -> gets classify task
  |                                      +-- (reasons about it)
  |                                      +-- deliver(task_id, result)
  +-- prints classify result!            |
  |                                      +-- poll() -> gets summarize task
  |                                      +-- (reasons about it)
  |                                      +-- deliver(task_id, result)
  +-- prints summarize result!           |
  ...                                    ...
```

The main conversation never sees the reasoning work — it just gets intelligent results from what looks like a regular tool call.

## Sub-agent dispatcher (recommended)

In production, the dispatcher should be a **background sub-agent** — not the main conversation. This keeps the main conversation free to do other work while the dispatcher polls and processes tasks autonomously.

### How it works

1. The main conversation spawns a background sub-agent with `metis-worker` MCP access
2. The sub-agent runs the poll-process-deliver loop independently
3. The main conversation continues — it's not blocked or polluted
4. MCP servers enqueue tasks via `TaskQueue` and get results back through SQLite
5. The sub-agent's context is disposable — if it dies, the self-healing protocol restarts it

### Dispatcher prompt

See [dispatcher_prompt.md](dispatcher_prompt.md) for the reference prompt. The key principles:

- **Poll immediately** on start and after every deliver
- **No deliberation on empty polls** — just poll again (keeps idle cost minimal)
- **Structured JSON results** — follow the instructions in each task's payload
- **No text output** — only tool calls (minimizes token usage)

### Observed performance

In testing with 3 tasks (classify, summarize, validate):
- 14,411 total tokens for the full run
- 10 tool calls (poll/deliver cycle)
- 36 seconds total runtime
- Main conversation context: zero pollution
