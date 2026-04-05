"""Metis trigger MCP server — standalone server using embeddable trigger tools.

Run as: python -m metis.presentation.trigger_server

NOT responsible for:
- Tool implementation (see trigger_tools.py)
- Task execution (see dispatcher agent via worker tools)
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from metis.presentation.trigger_tools import register_trigger_tools

_db_path = os.environ.get("METIS_DB_PATH", "~/.metis/metis.db")

mcp = FastMCP("metis-trigger")
_handle = register_trigger_tools(mcp, db_path=_db_path)
mcp._mcp_server.lifespan = _handle.lifespan  # type: ignore[union-attr]

# Re-export for backward compatibility and direct-call testing
enqueue = _handle.enqueue
get_result = _handle.get_result
check_health = _handle.check_health
lifespan = _handle.lifespan

if __name__ == "__main__":
    mcp.run()
