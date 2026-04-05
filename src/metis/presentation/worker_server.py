"""Metis worker MCP server — standalone server using embeddable worker tools.

Run as: python -m metis.presentation.worker_server

NOT responsible for:
- Tool implementation (see worker_tools.py)
- Task lifecycle logic (see domain entities)
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from metis.presentation.worker_tools import register_worker_tools

_db_path = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")
_poll_timeout = int(os.environ.get("METIS_POLL_TIMEOUT", "0"))

# register_worker_tools returns a handle with the lifespan before tools need it,
# so we register on the mcp instance and pass lifespan at construction.
# FastMCP allows setting lifespan after construction via the internal server.
mcp = FastMCP("metis-worker")
_handle = register_worker_tools(mcp, db_path=_db_path, poll_timeout=_poll_timeout)
mcp._mcp_server.lifespan = _handle.lifespan  # type: ignore[union-attr]

# Re-export for backward compatibility and direct-call testing
poll = _handle.poll
deliver = _handle.deliver
probe = _handle.probe
lifespan = _handle.lifespan

if __name__ == "__main__":
    mcp.run()
