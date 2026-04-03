"""Use case: check if any dispatcher worker is alive.

NOT responsible for:
- Spawning dispatchers (see self-healing protocol in calling MCP server)
- Heartbeat persistence (see HeartbeatStore)
"""

from __future__ import annotations

from metis.domain.errors import Ok, Result
from metis.domain.protocols import HeartbeatStore


class CheckHealthUseCase:
    """Checks whether any dispatcher has sent a recent heartbeat.

    NOT responsible for:
    - Deciding what to do when no worker is alive (see calling MCP server)
    - Managing worker lifecycle (see dispatcher agent)
    """

    def __init__(self, heartbeat_store: HeartbeatStore) -> None:
        self._heartbeat_store = heartbeat_store

    async def execute(self, timeout_seconds: int = 60) -> Result[bool]:
        latest = await self._heartbeat_store.get_latest()
        if latest is None:
            return Ok(False)
        return Ok(latest.is_alive(timeout_seconds=timeout_seconds))
