# Metis

**Agentic capabilities for MCP servers, powered by the LLM that's already using them.**

An LLM calls your MCP server's tools. Metis lets your MCP server harness that same LLM — dispatching reasoning tasks to background sub-agents that think, use tools, and return results. The main conversation never sees the work happening. It just gets intelligent answers from what looks like a regular tool call.

Named for the Greek Titaness of wisdom. Zeus consumed Metis to gain her counsel — MCP servers consume Metis to gain reasoning ability.

## The problem

MCP servers are deterministic. When they encounter something that needs judgment — ambiguous input, unstructured content, multi-step navigation — they either push the complexity back into the main conversation (cluttering it) or don't handle it at all.

Even when the tools themselves are straightforward, the cost adds up. The main LLM has to craft tool calls, interpret verbose responses, and handle errors — each response flooding its context with tokens that have nothing to do with the user's actual question. Multiply that across a multi-step workflow and the main conversation becomes dominated by plumbing. Every token spent on mechanics is cognitive capacity not spent on the user's actual problem.

The LLM driving the conversation has reasoning ability, but it's focused on the user's question. Asking it to manage low-level tool mechanics is like making a developer code in assembler when a high-level language exists. What if your MCP server could raise the abstraction level — so the calling LLM spends its cognitive capacity on reasoning and flow, not plumbing?

## How Metis solves it

```
LLM conversation (clean, focused on user)
  |
  +---> calls MCP Server tool
          |
          +====> LLM sub-agent (disposable context, spun up by the same LLM)
          |        - reasons about the problem
          |        - may use tools (browser, files, APIs)
          |        - returns structured result
          |        - context discarded
          |
          +---> MCP server returns intelligent result
```

Your MCP server enqueues a task. A background dispatcher agent — running as a sub-agent of the same LLM — picks it up, reasons about it (possibly spawning its own children for complex work), and delivers a result. The main conversation stays clean.

## Quick start

```bash
pip install -e ".[dev]"
```

### Embed in your MCP server

```python
from metis import TaskQueue

queue = TaskQueue(db_path="~/.myserver/metis.db")

# Fire-and-forget
queue.enqueue(type="classify", payload={"text": "..."})

# Block-and-wait
task_id = queue.enqueue(type="validate", payload={"content": "..."})
result = await queue.wait_for_result(task_id, timeout=30)
```

Or register Metis tools directly in an existing MCP server:

```python
from metis.presentation.worker_tools import register_worker_tools
from metis.presentation.trigger_tools import register_trigger_tools

mcp = FastMCP("my-server")
register_worker_tools(mcp, db_path="~/.myserver/metis.db")
register_trigger_tools(mcp, db_path="~/.myserver/metis.db")
```

### Run as standalone MCP servers

```bash
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.worker_server
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.trigger_server
```

## Client configuration

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

The dispatcher runs as a background sub-agent via the Agent tool, which inherits MCP tool access from the parent session.

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

The dispatcher requires a custom agent defined at `.github/agents/metis-dispatcher.md` with `tools: [metis-worker]` in the frontmatter — bare `#runSubagent` does not inherit MCP tools. Invoke with `@metis-dispatcher` in Copilot Agent mode.

See [docs/DISPATCHER.md](docs/DISPATCHER.md) for dispatcher prompts, long-poll tuning, and routing strategies.

## Architecture

Four components:

| Component | Role |
|-----------|------|
| **`metis`** (Python package) | Shared library. `TaskQueue` facade over a SQLite task queue in WAL mode. |
| **`metis-worker`** (MCP server) | Exposes `poll()`, `deliver()`, `probe()` to the dispatcher agent. |
| **`metis-trigger`** (MCP server) | Exposes `enqueue()`, `get_result()`, `check_health()` for the main conversation. |
| **Dispatcher agent** | Background sub-agent that polls, executes tasks, and delivers results. |

Both MCP servers share the same SQLite database. No external message broker needed.

The codebase follows a four-layer architecture (`Presentation -> Application -> Domain <- Infrastructure`) with strict dependency rules. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Operational patterns

- **Fire-and-forget** — queue work, return immediately
- **Block-and-wait** — queue work, hold open until result or timeout
- **Long-poll** — `poll(timeout=55)` blocks server-side, minimizing idle dispatcher token cost
- **Self-healing** — if the dispatcher is dead, return a signal so the calling LLM can respawn it
- **Disposable contexts** — worker agent contexts are discarded after each task
- **Capability filtering** — tasks declare required capabilities, only matching workers can claim them
- **Session isolation** — optional `session_id` scopes tasks to a user or session, essential for HTTP multi-user servers

## Use cases

- **Tool-call abstraction** — the main LLM says "get my portfolio holdings"; a Metis sub-agent handles the five browser navigations, table parsing, and error recovery behind a single high-level MCP tool. The calling LLM never sees the tool-call syntax or raw responses.
- **Browser-backed extraction** — navigate websites and return structured data
- **Security validation** — examine untrusted content in an isolated context (disposable taster pattern)
- **Content quality gates** — validate LLM-generated output before returning it
- **Complex classification** — route ambiguous input that's too nuanced for rules
- **Multi-step research** — gather and synthesize information from multiple sources

## Development

```bash
pytest                        # all tests
pytest tests/domain/          # pure logic, no I/O
pytest tests/application/     # use cases with real SQLite
pytest tests/infrastructure/  # SQLite store operations
pytest tests/presentation/    # MCP server contract tests
ruff check src/ tests/        # lint
ruff format --check src/ tests/
```

## Examples

- [examples/integration/](examples/integration/) — single-user MCP server with embedded Metis (stdio)
- [examples/http_multiuser/](examples/http_multiuser/) — multi-user HTTP server with session isolation ([testing guide](#testing-the-http-example-with-claude-code))
- [examples/simulated/](examples/simulated/) — simulated dispatcher for testing without an LLM
- [examples/live/](examples/live/) — live dispatcher with real LLM round-trips

## Testing the HTTP example with Claude Code

The HTTP multi-user example can be tested end-to-end with a real Claude Code session acting as both the user and the dispatcher.

**1. Start the server:**

```bash
python examples/http_multiuser/server.py
```

**2. Create a test workspace** with a `.mcp.json` pointing at the server:

```json
{
  "mcpServers": {
    "smart-notes": {
      "command": "mcp-proxy",
      "args": [
        "http://localhost:8000/mcp",
        "--transport", "streamablehttp",
        "--headers", "userid", "alice"
      ]
    }
  }
}
```

This uses `mcp-proxy` to bridge Claude Code's stdio transport to the HTTP server, passing `userid: alice` on every request.

**3. Open the test workspace in VS Code** with Claude Code. Ask it to save a note:

> Save a note titled "Test" with content "Hello world"

The first call returns `metis_dispatcher_required: true` with inline instructions telling the LLM how to spawn a dispatcher. The LLM reads the instructions, spawns a background sub-agent that polls for tasks, then retries `save_note`. The sub-agent claims the classify and validate tasks, processes them, delivers results, and `save_note` returns with an intelligent classification.

**4. Automated tests** (no LLM needed):

```bash
# Queue-level session isolation
python examples/http_multiuser/test_isolation.py

# HTTP end-to-end via MCP client SDK
python examples/http_multiuser/test_http_e2e.py
```

## Documentation

- [DESIGN.md](docs/DESIGN.md) — full design document and positioning
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — layer responsibilities and dependency rules
- [PATTERNS.md](docs/PATTERNS.md) — canonical vertical slice reference
- [DISPATCHER.md](docs/DISPATCHER.md) — dispatcher architecture, prompts, and long-poll tuning

## License

MIT
