"""Metis dispatcher built with deepagents, powered by MCP sampling (BYOT).

Exposes one MCP tool: `run_dispatcher(max_tasks=N)`. When an MCP client
invokes it, this server spins up a deepagents agent for each claimed task,
using MCPSamplingChatModel so the agent's reasoning runs on the CLIENT's
LLM via MCP sampling — no dispatcher-side API key required.

Composition:

    main LLM (client) --MCP tool call--> this server
        this server:
            1. Claim next Metis task from SQLite queue
            2. Build deepagents agent with MCPSamplingChatModel(ctx.session)
            3. Invoke agent on task payload.instructions
            4. Deliver result back to the queue (→ other MCP servers' callers)

Prerequisites:
    pip install deepagents "metis[langchain-bridge]"

Run:
    METIS_DB_PATH=~/.metis/metis.db python examples/deepagents-dispatcher/dispatcher.py

Then connect an MCP client that supports sampling (e.g. Claude Code with a
`.mcp.json` entry pointing at this server) and call `run_dispatcher(5)`.

NOT production-ready — it's a narrative example of the composition pattern.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from deepagents import create_deep_agent
from mcp.server.fastmcp import Context, FastMCP

from metis.application.deliver_result import DeliverResultInput, DeliverResultUseCase
from metis.application.poll_task import PollTaskInput, PollTaskUseCase
from metis.infrastructure.database import init_async_database
from metis.infrastructure.sqlite_heartbeat_store import SqliteHeartbeatStore
from metis.infrastructure.sqlite_task_store import SqliteTaskStore
from metis.langchain import MCPSamplingChatModel

DB_PATH = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")
WORKER_ID = "deepagents-dispatcher"
DEFAULT_CAPABILITIES = ["classify", "summarize", "validate", "research"]


_state: dict[str, Any] = {"conn": None, "poll_uc": None, "deliver_uc": None}


@asynccontextmanager
async def lifespan(server: FastMCP):
    conn = await init_async_database(DB_PATH)
    task_store = SqliteTaskStore(conn)
    hb_store = SqliteHeartbeatStore(conn)
    _state["conn"] = conn
    _state["poll_uc"] = PollTaskUseCase(task_store=task_store, heartbeat_store=hb_store)
    _state["deliver_uc"] = DeliverResultUseCase(task_store=task_store)
    try:
        yield
    finally:
        if _state["conn"] is not None:
            await _state["conn"].close()
            _state["conn"] = None


mcp = FastMCP("metis-deepagents-dispatcher", lifespan=lifespan)


@mcp.tool()
async def run_dispatcher(
    ctx: Context,
    max_tasks: int = 5,
    capabilities: list[str] | None = None,
    poll_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Claim up to max_tasks from the Metis queue and process each with a deepagents agent.

    The agent reasons using the CALLER's LLM via MCP sampling (ctx.session).
    Returns a summary of processed tasks.
    """
    poll_uc: PollTaskUseCase = _state["poll_uc"]
    deliver_uc: DeliverResultUseCase = _state["deliver_uc"]

    caps = capabilities or DEFAULT_CAPABILITIES
    processed: list[dict[str, Any]] = []
    model = MCPSamplingChatModel(session=ctx.session)

    for _ in range(max_tasks):
        poll_result = await poll_uc.execute(
            PollTaskInput(
                worker_id=WORKER_ID,
                capabilities=caps,
                timeout_seconds=poll_timeout_seconds,
            )
        )
        if poll_result.is_error or poll_result.value is None:
            break

        task = poll_result.value
        instructions = task.payload.get("instructions") or json.dumps(task.payload)

        # Build a fresh deepagents instance per task — disposable context
        agent = create_deep_agent(
            model=model,
            system_prompt=(
                "You are a Metis dispatcher worker. Complete the task below and "
                "return a JSON object describing the result."
            ),
        )

        try:
            agent_result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": instructions}]}
            )
            final_message = agent_result["messages"][-1]
            raw = getattr(final_message, "content", None) or ""
            # Try to parse JSON; fall back to wrapping as text
            try:
                result_payload = json.loads(raw) if isinstance(raw, str) else {"output": str(raw)}
            except json.JSONDecodeError:
                result_payload = {"output": raw}
        except Exception as e:  # noqa: BLE001 - surface any agent error to the task
            await deliver_uc.execute(
                DeliverResultInput(
                    task_id=task.id.value,
                    result={"error": str(e)},
                )
            )
            processed.append({"task_id": task.id.value, "status": "errored", "error": str(e)})
            continue

        await deliver_uc.execute(
            DeliverResultInput(task_id=task.id.value, result=result_payload)
        )
        processed.append({"task_id": task.id.value, "status": "delivered"})

    return {"processed": processed, "count": len(processed)}


if __name__ == "__main__":
    mcp.run()
