"""Toy MCP server demonstrating Metis integration.

Exposes one tool: analyze(text) that dispatches classification work
to a Metis worker and returns the result. Shows the Nellie-style
integration pattern with graceful degradation.

Usage:
    METIS_DB_PATH=~/.metis/metis.db python examples/toy_server.py

To test the full round-trip, run alongside simulate_dispatcher.py:
    1. python examples/simulate_dispatcher.py --db ~/.metis/metis.db
    2. METIS_DB_PATH=~/.metis/metis.db python examples/toy_server.py
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from metis import TaskQueue

mcp = FastMCP("toy-server")

_queue: TaskQueue | None = None


def _get_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        db_path = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")
        _queue = TaskQueue(db_path=db_path)
    return _queue


@mcp.tool()
async def analyze(text: str) -> dict:
    """Analyze text by dispatching to a Metis worker.

    If a dispatcher is running, returns intelligent classification.
    If no dispatcher is available, returns a degraded response with
    the metis_dispatcher_required signal.
    """
    queue = _get_queue()

    if not queue.is_worker_alive():
        return {
            "result": f"Unclassified: {text[:100]}",
            "classified": False,
            "metis_dispatcher_required": True,
            "message": (
                "Spawn a background sub-agent with metis-worker MCP access "
                "to enable intelligent processing."
            ),
        }

    task_id = queue.enqueue(
        type="classify",
        payload={"text": text},
        ttl_seconds=30,
    )

    result = await queue.wait_for_result(task_id, timeout=20)

    if result is None:
        return {
            "result": f"Timeout classifying: {text[:100]}",
            "classified": False,
        }

    return {
        "result": result,
        "classified": True,
    }


if __name__ == "__main__":
    mcp.run()
