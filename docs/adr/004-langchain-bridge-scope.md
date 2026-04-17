# ADR 004: LangChain Bridge — Placement, Scope, and Session Typing

## Status

Accepted — shipped alongside the core spec alignment.

## Context

A recurring theme through Metis's design is **BYOT** (bring your own tokens): the MCP client's LLM reasons; server-side code doesn't bring an API key. MCP sampling (`ctx.session.create_message(...)`) is the protocol mechanism that makes this possible.

As the ecosystem of "agent frameworks" matured — LangChain, LangGraph, and specifically `langchain-ai/deepagents` (LangChain's Claude-Code-inspired agent harness) — a natural composition emerged:

> Metis routes (queue, dispatcher pool, cross-server sharing).
> deepagents (or any LangChain consumer) reasons (planning, filesystem, subagents, tools).
> MCP sampling powers it (BYOT — the user's LLM does the work).

The missing piece was an adapter: a LangChain `BaseChatModel` whose `_agenerate` translates to `ServerSession.create_message(...)`. Without it, every LangChain-powered dispatcher needs its own API key, defeating BYOT.

## Decision

Ship an in-tree LangChain bridge at `src/metis/langchain/`, as an **optional install** (`metis[langchain-bridge]`), with these design choices:

1. **Fifth sibling, outside the four-layer architecture.**  
   `src/metis/langchain/` lives alongside `domain/`, `application/`, `infrastructure/`, `presentation/` — *not* inside any of them. The four core layers hold business logic; the bridge is an external-framework adapter with no business logic. Putting it under `infrastructure/` would falsely imply it's part of Metis's persistence story; under `presentation/` would falsely imply it's a protocol transport. It's neither. ARCHITECTURE.md documents this explicitly.

2. **Duck-typed session via a `Protocol`.**  
   `MCPSamplingChatModel.session: SamplingSession` — a runtime-checkable `Protocol` with one method (`async create_message(**kwargs)`). The canonical implementation is `mcp.server.session.ServerSession`, but fakes, adapters, and forks all work without inheritance. Mirrors the duck-typed ctx handling elsewhere in Metis (the facade's `_sample_via_ctx` already operates this way).

3. **No streaming.**  
   MCP sampling is request/response; there is no streaming primitive in the spec. LangGraph's streaming UX degrades to completion-only when this model is used. Documented; no attempt at a fake streaming layer.

4. **No `deepagents` or `langgraph` hard dependency.**  
   The bridge depends only on `langchain-core` (in the `langchain-bridge` extra). Users who want deepagents install it separately. The example at `examples/deepagents-dispatcher/` documents the additional install.

5. **In-tree now, extractable later.**  
   The bridge is genuinely useful outside Metis — any MCP server that exposes tools to a LangChain-powered backend benefits. We considered a separate package (`langchain-mcp-sampling`) but shipping in-tree is faster and lets us iterate before stabilising. If it proves reusable outside, extract to a standalone package and keep a re-export shim here.

6. **`bind_tools()` overridden explicitly.**  
   LangChain's default raises `NotImplementedError`. We override to translate LangChain tool specs → `mcp.types.Tool` and thread them via `kwargs` so deepagents' heavy tool-use reliance works.

7. **Sync fallback raises inside a running event loop.**  
   `_generate` runs `asyncio.run(_agenerate(...))` when called from sync context, but raises if called from an active loop (where it would deadlock). Clear error beats a mysterious hang.

## Alternatives considered and rejected

- **`session: Any`.** Simplest. Rejected: violates the project's strong-typing principle (no untyped holes at public-API boundaries).
- **`session: ServerSession`.** Most specific. Rejected: forces inheritance, breaks tests that use fakes, locks the bridge to one MCP SDK implementation.
- **Bridge under `infrastructure/`.** Miscategorises it as persistence. Rejected.
- **Separate package from day one.** Versioning overhead before we know the surface is right. Rejected — extract later if useful.
- **Full streaming shim** that buffers completion text and emits per-chunk callbacks. Rejected: pretends to offer semantics sampling doesn't have; degrades silently when the underlying model can't stream.

## Rationale

The bridge is a deep module by Ousterhout's measure: a narrow interface (one class, one `_agenerate` override, a `bind_tools`) hides meaningful complexity (message translation, tool translation, response parsing, sync/async bridging, `response_metadata` propagation). Users compose it with any LangChain consumer without needing to understand any of this.

Placing it at `src/metis/langchain/` makes the "external adapter" nature visible at the folder level. Anyone reading the repo tree sees that the four core layers are intact and this is a clearly-separated integration.

The `SamplingSession` Protocol is the minimum typing discipline that preserves flexibility. It documents the expected contract, enables static checking, and still works with any object exposing the right method.

## Trade-offs

- **LangChain ecosystem gravity.** Users installing `langchain-bridge` pull `langchain-core` (~tenacity, langsmith, orjson, jsonpatch, etc.). We don't avoid it; we make it opt-in.
- **API drift risk.** LangChain 1.x → future major versions could break `BaseChatModel` internals. We pin `langchain-core>=0.3` rather than `>=1.0` so early-1.x and late-0.3 both work; will tighten if we see breakage.
- **Client capability requirements.** Clients must declare `sampling` capability; deepagents relies on tool use which requires `sampling.tools`. Many clients don't support these yet. Documented in `examples/deepagents-dispatcher/README.md`.
- **Re-entrancy with outstanding tool calls.** If the dispatcher's sampling request happens while the client is waiting on the tool call that launched it, client implementations must handle concurrency. Flagged as a known caveat.

## Consequences

- New folder `src/metis/langchain/` with three modules (`__init__.py`, `sampling_chat_model.py`, `_message_conversion.py`, `_protocols.py`).
- New optional extra `langchain-bridge` in `pyproject.toml`.
- 25 new tests under `tests/langchain/`.
- Example `examples/deepagents-dispatcher/` illustrates the composition.
- `docs/ARCHITECTURE.md` gains a short section naming the bridge as a non-core sibling.

## References

- `src/metis/langchain/sampling_chat_model.py` — the `BaseChatModel` subclass
- `src/metis/langchain/_message_conversion.py` — pure translators
- `src/metis/langchain/_protocols.py` — the `SamplingSession` Protocol
- `examples/deepagents-dispatcher/` — the BYOT composition example
- [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) — LangChain's Claude-Code-inspired agent harness
- [MCP sampling spec (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling)
