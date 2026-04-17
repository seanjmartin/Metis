# Metis dispatcher built with deepagents, BYOT-powered

This example shows the **composition story** we've been building toward:

- **Metis** routes — MCP servers dispatch reasoning tasks onto a shared SQLite-backed queue.
- **deepagents** reasons — each task is processed by a full deepagents agent (planning, filesystem, sub-agents, permissions, the lot).
- **MCP sampling** powers it — no dispatcher-side API key. Tokens bill to the caller's subscription via `ctx.session.create_message()`. BYOT: *bring your own tokens*.

## How it composes

```
Main LLM client (Claude Code / Copilot / ...)
  │
  │ MCP tool call: run_dispatcher(max_tasks=5)
  ▼
This MCP server (examples/deepagents-dispatcher/dispatcher.py)
  │
  │ for each task:
  │   ├── claim from Metis SQLite queue
  │   ├── build deepagents agent(model=MCPSamplingChatModel(ctx.session))
  │   ├── run agent on task.payload.instructions
  │   │       └── every LLM call inside deepagents → ctx.session.create_message()
  │   │               └── routes back to the main LLM client
  │   └── deliver result to Metis queue
  ▼
Other MCP servers' `get_result(...)` calls return with intelligent answers.
```

**Who pays:** the caller. Every sampling round-trip bills to the connected client's LLM subscription. No per-server API keys, no per-server billing.

## Install

This example requires `deepagents` (which brings LangGraph and its deps) and the `langchain-bridge` extra of Metis. Install separately — the core Metis package doesn't depend on either.

```bash
pip install deepagents
pip install -e ".[langchain-bridge]"   # from the metis repo root
```

## Run

```bash
METIS_DB_PATH=~/.metis/metis.db python examples/deepagents-dispatcher/dispatcher.py
```

Then connect an MCP client that supports **sampling** (Claude Code does). Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "metis-deepagents-dispatcher": {
      "command": "python",
      "args": ["examples/deepagents-dispatcher/dispatcher.py"],
      "env": { "METIS_DB_PATH": "~/.metis/metis.db" }
    }
  }
}
```

From the client, invoke the tool:

> `run_dispatcher(max_tasks=5)`

The server will poll Metis for up to 5 tasks, spin up a deepagents agent for each, run it using your LLM via sampling, and deliver results back to the Metis queue. Other MCP servers waiting on those task IDs will unblock with intelligent answers.

## What's happening inside

1. `examples/deepagents-dispatcher/dispatcher.py` is a FastMCP server that exposes **one** tool: `run_dispatcher`.
2. When the client invokes it, the tool opens a [`MCPSamplingChatModel`](../../src/metis/langchain/sampling_chat_model.py) over `ctx.session`.
3. For each claimed task, it calls `create_deep_agent(model=model, ...)` — deepagents builds its full middleware stack (planning, filesystem, subagents, permissions).
4. When deepagents makes an LLM call, the chat model translates LangChain messages → `SamplingMessage`s and calls `ctx.session.create_message(...)`.
5. The MCP client receives the sampling request, runs its LLM, returns the response, and the chat model translates back to LangChain.
6. deepagents continues — planning, calling tools, spawning sub-agents — all powered by the user's subscription.
7. When the agent produces a final message, its content is delivered to the Metis queue as the task result.

## Caveats

- **Client must declare sampling capability.** Claude Code does; many clients don't yet. Verify before running.
- **Tool-use support in sampling** is required for deepagents to work (it calls tools constantly). The 2025-11-25 spec adds this, but client implementations vary — test with your target client.
- **No streaming.** MCP sampling is one-shot; deepagents' streaming UX degrades to completion-only.
- **Rate limits apply.** The user's LLM rate limits govern. A deepagents agent can make many LLM calls per task; budget accordingly.
- **Re-entrancy.** The client is currently waiting on the `run_dispatcher` tool response while the tool issues sampling requests back to that same client. Clients *should* handle this concurrency; implementations vary. If you see deadlocks, that's where to look.
- **This is an example, not a product.** It's here to illustrate the composition; production dispatchers need supervision, retries, progress reporting, cancellation, error handling.

## See also

- [`src/metis/langchain/`](../../src/metis/langchain/) — the bridge itself, with tests.
- [`docs/DISPATCHER.md`](../../docs/DISPATCHER.md) — the canonical Metis dispatcher architecture.
- [deepagents](https://github.com/langchain-ai/deepagents) — LangChain's Claude-Code-inspired agent harness.
