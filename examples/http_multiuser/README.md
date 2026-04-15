# Smart Notes (HTTP) — Multi-User Example

Demonstrates Metis session isolation over HTTP. Two users (Alice and Bob) connect to the same MCP server. Each user's tasks are scoped to their session — Alice's dispatcher never processes Bob's tasks.

## Architecture

```
Alice's LLM (Claude Code)              Bob's LLM (VS Code Copilot)
  |  userid: alice                        |  userid: bob
  |                                       |
  +---> save_note("quantum paper", ...)   +---> save_note("grocery list", ...)
  |       enqueue(session_id="alice")     |       enqueue(session_id="bob")
  |                                       |
  +---> dispatcher sub-agent              +---> dispatcher sub-agent
          poll(session_id="alice")                poll(session_id="bob")
          claims only Alice's tasks               claims only Bob's tasks
          deliver() → result                      deliver() → result
  |                                       |
  +---> gets classification result        +---> gets classification result
```

Both dispatchers hit the same server and database. The `userid` header → contextvar → `session_id` on the task keeps them apart.

## Setup

```bash
pip install -e ".[dev]"
pip install uvicorn
python examples/http_multiuser/server.py
```

The server listens on `http://127.0.0.1:8000`.

## Client configuration

### Alice — Claude Code

Add to `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "smart-notes": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "userid": "alice" }
    }
  }
}
```

### Bob — VS Code Copilot

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "smart-notes": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "userid": "bob" }
    }
  }
}
```

## How it works

1. Each HTTP request carries a `userid` header
2. ASGI middleware extracts it into a `contextvars.ContextVar`
3. When `enqueue()` is called, it reads the contextvar and stamps `session_id` on the task
4. When `poll()` is called, it reads the contextvar and filters `claim_next` by `session_id`
5. The dispatcher only sees tasks for its user's session

## Testing session isolation

Without running an LLM, you can verify isolation at the queue level:

```bash
python examples/http_multiuser/test_isolation.py
```

This enqueues tasks for Alice and Bob, then shows that each dispatcher only claims their own tasks.

## Testing with curl

```bash
# Alice saves a note
curl -X POST http://127.0.0.1:8000/mcp \
  -H "userid: alice" \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/call", "params": {"name": "save_note", "arguments": {"title": "Quantum Paper", "content": "Research on quantum entanglement..."}}}'

# Bob saves a note
curl -X POST http://127.0.0.1:8000/mcp \
  -H "userid: bob" \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/call", "params": {"name": "save_note", "arguments": {"title": "Grocery List", "content": "Milk, eggs, bread..."}}}'
```

Without a dispatcher running, both return `metis_dispatcher_required: true`. With dispatchers, each user gets results from their own session only.
