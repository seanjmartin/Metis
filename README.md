# Metis

**Free the calling LLM from plumbing — keep it working in a high-level language, not assembler.**

An LLM calls your MCP server's tools. With Metis, the server *contains* the reasoning: the tool handler dispatches work to a background agent, receives a structured result, and returns it. The caller never sees the sub-reasoning, the intermediate artifacts, or the plumbing — it just gets a useful answer to the user's actual question.

Named for the Greek Titaness of wisdom. Zeus consumed Metis to gain her counsel — MCP servers consume Metis to gain reasoning ability.

## Why this exists

Every token the calling LLM spends on plumbing — crafting low-level tool calls, parsing verbose responses, chaining sub-steps, handling retries — is a token not spent on the user's actual problem. And long contexts degrade: attention dilutes, reasoning drifts. Keeping the main conversation clean is not tidiness; it's measurably better behaviour.

The usual answers to "MCP servers need reasoning" both fail that test:

- **Bundle an LLM client in the server.** Now every server needs an API key, billing, model policy, and retry loop — and each call still returns verbose artifacts the caller has to digest.
- **Push orchestration to the caller.** The main LLM becomes a coordinator of low-level steps. Its context fills with mechanics. The user's question gets less cognitive capacity.

Even a main-agent subagent tool (e.g. Claude Code's `Task`) doesn't solve it: the *caller* still has to decide-to-delegate, frame the sub-problem, and route the result. That's still plumbing in the hot path.

Metis takes a different route: **the MCP server itself owns the delegation decision**. From the caller's perspective, the tool is just a tool — it either accepts a natural-language question or a low-level parameter, and returns a finished answer. The reasoning inside is invisible.

```
summarize_notes(titles=[...]) → {"summary": "..."}
```

Behind the scenes, the tool handler enqueues three reasoning tasks, the dispatcher claims them, returns tailored results, and the handler composes the final summary. The main conversation sees one tool call and one useful answer — not the plumbing that produced it.

### What makes this different

- **The dispatcher tailors its response to the question.** A deterministic tool has to choose once: return the full artifact (floods the caller's context with noise) or a fixed-shape summary (often wrong-shaped for the actual question). An intelligent dispatcher reasons about what summary is useful for *this* question and returns it. That's a context-hygiene win a plain tool structurally cannot deliver.
- **One dispatcher serves many MCP servers.** Your user's dispatcher is a shared reasoning pool. Every server you install taps the same pool — no per-server API keys, no per-server model policy.
- **User-owned reasoning — BYOT (bring your own tokens).** The dispatcher runs in the user's session under their subscription, their rate limits, their policy. Your server is thin; the user's LLM does the work. No per-server API keys, no per-server billing.
- **Persistence across client sessions.** Tasks live in SQLite, not in a connection. A task enqueued before a client restart is still waiting when the dispatcher reconnects.

### How it composes with the main agent's tools

Metis is not a replacement for subagent tools like Claude Code's `Task` — they work at different layers:

- **`Task`** lets the main agent orchestrate sub-agents it knows about. Useful when the main agent needs to delegate work it has decided to delegate.
- **Metis** lets MCP servers orchestrate reasoning the main agent doesn't need to know about. Useful when a tool needs to be smart without leaking its internals into the caller's context.

Different concerns, different layers. They compose.

## What this buys you — three vignettes

### 1. The browser-backed tool

*User:* "What's in my portfolio?"

**Without Metis.** The MCP server exposes low-level primitives — `navigate()`, `extract()`, `handle_login()`. The main LLM orchestrates: fetch login page, submit credentials, navigate to portfolio, extract the holdings table, parse each row. Each step floods the main conversation with raw HTML and intermediate JSON. By the time the answer is synthesised, the main LLM is carrying ~15k tokens of bookkeeping — most of which dilutes its attention on the user's *next* question.

**With Metis.** The server exposes one tool: `get_portfolio()`. The tool handler dispatches to the dispatcher, which does all the navigation and parsing in its own disposable context, and returns `{"holdings": [...]}`. The main conversation sees one tool call and one clean answer. The next question arrives against fresh context.

### 2. The smart classifier

*User:* "Save this note titled 'Tax strategy Q3'."

**Without Metis.** Either the server is dumb (saves as "uncategorized") or the main LLM must classify before saving — spending cognitive capacity deciding "finance or personal?" before it can execute the user's actual request. The classification artifacts (reasoning about category, confidence, alternatives) end up in the caller's context, crowding out the user's workflow.

**With Metis.** `save_note(title, content)` is the whole interface. Inside, the handler enqueues a classification task, waits, receives `{"category": "finance", "confidence": 0.94}`, and saves the note. The main LLM was never distracted from the user's actual flow — and the *reasoning* that produced the category stays out of its context entirely.

### 3. Progressive disclosure — one tool, three abstraction levels

*User:* "What did I read about LLM evals last month?"

**Deterministic tool.** `query_notes(sql=...)` forces the main LLM to author SQL against a schema it doesn't know. It guesses, asks for the schema (more tokens), or generates something broken.

**Metis tool, simple case.** `query_notes(question="what did I read about LLM evals?")` — the dispatcher turns the question into SQL, runs it, and returns a prose summary *tailored to this question*. A deterministic post-processor can't tailor that — it has to pick one output shape up front; the dispatcher reasons about what shape is useful *here*.

**Metis tool, full control.** The *same* tool still accepts `sql=` when the LLM needs precise control, or `return_raw=True` for structured rows. The calling LLM pays cognitive cost only at the level it chooses. Agency preserved; context protected.

## Spec-aligned

Metis implements the [MCP async-tasks specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks):

- **Tool handles** return spec-compliant envelopes: `{"task": {"id", "status"}, "result"?, "error"?}` — with the five spec states `working` / `input_required` / `completed` / `failed` / `cancelled` and the terminal-state invariant ([spec §Task Status Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks#task-status-lifecycle))
- **Cancellation** via `cancel(task_id)` — terminal tasks are rejected with JSON-RPC `-32602` per [spec §Task Cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks#task-cancellation)
- **Progress** forwarded through `ctx.report_progress()` on the originating client's progressToken — reuses the existing MCP [progress](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/progress) channel, as [§Task Progress Notifications](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks#task-progress-notifications) requires
- **Elicitation** — dispatchers can ask the user for input mid-task via [`ctx.elicit()`](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation), transparently to the caller (spec's `input_required` status)
- **Sampling** — dispatchers can invoke the client's LLM via [`sampling/createMessage`](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling) for sub-completions, and `enqueue_with_sampling_fallback` degrades gracefully when no dispatcher is running

Spec proposal that became the Tasks primitive: [SEP-1686](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1686).

Metis-distinctive capabilities *not* in the spec (shared dispatcher pool, SQLite persistence, session isolation, capability filtering) are preserved alongside.

```
LLM conversation (clean, focused on user)
  |
  +---> calls MCP Server tool
          |
          +====> dispatcher agent (long-running, user-owned)
          |        - reasons about the problem
          |        - reports progress (→ user's progress bar)
          |        - may ask the user a clarifying question (→ elicitation)
          |        - may call the client's LLM for a sub-completion (→ sampling)
          |        - returns structured result
          |
          +---> MCP server returns intelligent result
```

## Quick start

```bash
pip install -e ".[dev]"
```

### Embed in your MCP server

```python
from metis import TaskQueue

async with TaskQueue(db_path="~/.myserver/metis.db") as queue:
    # Fire-and-forget
    queue.enqueue(type="classify", payload={"text": "..."})

    # Block-and-wait
    task_id = queue.enqueue(type="validate", payload={"content": "..."})
    result = await queue.wait_for_result(task_id, timeout=30, ctx=ctx)
```

Passing `ctx` (the FastMCP `Context` from the tool handler) enables progress forwarding, transparent elicitation, and sampling round-trips.

Or register Metis tools directly in an existing MCP server. Both
`register_worker_tools()` and `register_trigger_tools()` return a handle
whose `.lifespan` **must** be composed into the host server's lifespan —
the tools will raise `RuntimeError` at call time otherwise:

```python
from metis.presentation.worker_tools import register_worker_tools
from metis.presentation.trigger_tools import register_trigger_tools

mcp = FastMCP("my-server")
worker_handle = register_worker_tools(mcp, db_path="~/.myserver/metis.db")
trigger_handle = register_trigger_tools(mcp, db_path="~/.myserver/metis.db")

@asynccontextmanager
async def lifespan(server):
    async with worker_handle.lifespan(server), trigger_handle.lifespan(server):
        yield

mcp._mcp_server.lifespan = lifespan
```

### Run as standalone MCP servers

```bash
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.worker_server
METIS_DB_PATH=~/.metis/metis.db python -m metis.presentation.trigger_server
```

## Design pattern: progressive disclosure

Metis tools can be *optionally smart*. Expose a tool at multiple abstraction levels and let the calling LLM pay cognitive cost only when it wants control:

```python
@mcp.tool()
async def query_notes(
    question: str | None = None,   # natural-language question (cheap)
    sql: str | None = None,        # explicit SQL (full control)
    return_raw: bool = False,      # rows instead of prose summary
) -> dict:
    if sql:
        return {"rows": await db.execute(sql)}
    # Dispatch the NL question to a reasoning agent:
    task_id = queue.enqueue(type="nl_query", payload={"q": question})
    result = await queue.wait_for_result(task_id, timeout=30, ctx=ctx)
    if return_raw:
        return {"rows": result["rows"]}
    return {"summary": result["prose"]}  # context-aware, tailored to the question
```

The calling LLM preserves full agency (it can drop to SQL) but doesn't have to carry the cost unless it wants to. The dispatcher tailors its summary to the specific question — something a deterministic post-processor can't do.

## Spec-aligned capabilities in action

Short snippets showing how each capability is used. All four flow through `ctx` — the FastMCP `Context` from the tool handler — and are transparent to the calling LLM.

### Progress

The dispatcher reports progress at meaningful steps; Metis forwards it to the client's progress bar via `ctx.report_progress()`.

```python
# Dispatcher side (sub-agent worker)
await report_progress(task_id=tid, progress=0.0, message="starting")
await report_progress(task_id=tid, progress=0.5, message="analysing")
await report_progress(task_id=tid, progress=0.9, message="finalising")
await deliver(task_id=tid, result={...})

# Trigger side (your MCP tool handler — automatic)
@mcp.tool()
async def summarize_notes(titles: list[str], ctx: Context) -> dict:
    tid = queue.enqueue(type="summarize", payload={"titles": titles})
    return await queue.wait_for_result(tid, timeout=60, ctx=ctx)  # progress flows through ctx
```

### Cancellation

The caller can cancel a running task; dispatchers should check between sub-steps.

```python
# Caller
cancel_result = await trigger.cancel(task_id="...")
# → {"task": {"id": "...", "status": "cancelled"}}

# Dispatcher between steps
status = await check_cancelled(task_id=tid)
if status["cancelled"]:
    # Stop work and poll again — don't deliver (TASK_ALREADY_TERMINAL rejection)
    break
```

### Elicitation (transparent)

The dispatcher pauses to ask the user a question; the trigger-side `get_result` loop surfaces it via `ctx.elicit()`, writes the reply back, and resumes — all invisible to the caller.

```python
# Dispatcher side
seq = await request_input(
    task_id=tid,
    prompt="The note could be 'work' or 'finance'. Which?",
    schema={"type": "object"},
)
reply = await await_input_response(task_id=tid, seq=seq["seq"], timeout=55)
# reply["response"] → {"response": "finance"}

# Caller sees: one save_note() tool call that eventually returns the classified note
```

### Sampling fallback

When the dispatcher is offline, `enqueue_with_sampling_fallback` completes the task via the client's own LLM so the caller's flow still works.

```python
@mcp.tool()
async def classify(text: str, ctx: Context) -> dict:
    tid = await queue.enqueue_with_sampling_fallback(
        type="classify",
        payload={"instructions": f"Classify: {text}"},
        ctx=ctx,
    )
    result = await queue.wait_for_result(tid, timeout=30, ctx=ctx)
    # result["metis"]["fallback"] == "sampling" if the dispatcher was down
    return result
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

See [docs/DISPATCHER.md](docs/DISPATCHER.md) for dispatcher prompts, long-poll tuning, and spec-aligned capabilities.

## Architecture

Four components:

| Component | Role |
|-----------|------|
| **`metis`** (Python package) | Shared library. `TaskQueue` facade over a SQLite task queue in WAL mode. |
| **`metis-worker`** (MCP server) | Exposes dispatcher tools: `poll`, `deliver`, `probe`, `report_progress`, `check_cancelled`, `request_input`, `await_input_response`, `request_sampling`, `await_sampling_response`. |
| **`metis-trigger`** (MCP server) | Exposes caller tools: `enqueue`, `get_result`, `cancel`, `provide_input`, `check_health`. All return spec-compliant task envelopes. |
| **Dispatcher agent** | Background sub-agent that polls, executes tasks (reports progress, asks questions, samples as needed), and delivers results. |

Both MCP servers share the same SQLite database. No external message broker needed.

The codebase follows a four-layer architecture (`Presentation -> Application -> Domain <- Infrastructure`) with strict dependency rules. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Operational patterns

- **Fire-and-forget** — queue work, return immediately
- **Block-and-wait** — queue work, hold open until result or timeout
- **Long-poll** — `poll(timeout=55)` blocks server-side, minimizing idle dispatcher token cost
- **Transparent elicitation** — dispatcher asks, `get_result` surfaces the prompt to the client, writes the response back, dispatcher resumes — caller sees a single tool call
- **Progress forwarding** — dispatcher's `report_progress` flows through to the client's progressToken
- **Cancellation** — `cancel(task_id)` at any time; dispatcher sees it on `check_cancelled` and stops; terminal-state invariant enforced
- **Sampling fallback** — `enqueue_with_sampling_fallback(ctx=...)` completes the task via the client's LLM when no dispatcher is alive
- **Self-healing** — if the dispatcher is dead, return a signal so the calling LLM can respawn it
- **Disposable contexts** — worker agent contexts are discarded after each task
- **Capability filtering** — tasks declare required capabilities, only matching workers can claim them
- **Session isolation** — optional `session_id` scopes tasks to a user or session, essential for HTTP multi-user servers

## Use cases

- **Tool-call abstraction** — the main LLM says "get my portfolio holdings"; a Metis sub-agent handles the five browser navigations, table parsing, and error recovery behind a single high-level MCP tool. The calling LLM never sees the tool-call syntax or raw responses.
- **Progressive disclosure** — one tool exposes a natural-language interface, a structured interface, and a low-level escape hatch; the calling LLM chooses its level per call.
- **Browser-backed extraction** — navigate websites and return structured data.
- **Security validation** — examine untrusted content in an isolated context (disposable taster pattern).
- **Content quality gates** — validate LLM-generated output before returning it.
- **Complex classification** — route ambiguous input that's too nuanced for rules.
- **Multi-step research** — gather and synthesize information from multiple sources.

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

## Honest tradeoffs

- **Someone has to run the dispatcher.** It's a long-running background agent in the user's session. For personal software this is fine — the user *is* the operator. For multi-tenant server deployments it's a real supervision burden. We're not pretending otherwise.
- **Dispatcher quality gates your output.** If the dispatcher returns weak reasoning, your MCP tool returns weak reasoning. Metis moves *where* the reasoning happens; it doesn't guarantee its quality.
- **Not every tool should use Metis.** Orchestration layers should be the last resort, justified by a specific thing that can't be done in-process. A deterministic tool that already does the right thing doesn't need a reasoning agent behind it.
- **Cognitive load reduction is the primary optimisation.** Other properties (persistence, shared pool, spec alignment) are nice; they're not the headline. If your calling LLM's context isn't under pressure, Metis may be over-engineering.

## Documentation

- [DESIGN.md](docs/DESIGN.md) — full design document and positioning
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — layer responsibilities and dependency rules
- [PATTERNS.md](docs/PATTERNS.md) — canonical vertical slice reference
- [DISPATCHER.md](docs/DISPATCHER.md) — dispatcher architecture, prompts, spec-aligned capabilities, long-poll tuning

## License

MIT
