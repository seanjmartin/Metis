"""Structural protocols used by the LangChain bridge.

NOT responsible for:
- Any runtime behaviour (these are pure type interfaces)
- Validating MCP spec conformance (see mcp.types for the canonical shapes)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mcp.types import CreateMessageResult, CreateMessageResultWithTools


@runtime_checkable
class SamplingSession(Protocol):
    """Any object exposing an async ``create_message`` that matches MCP sampling.

    The canonical implementation is ``mcp.server.session.ServerSession`` (which
    you get at runtime as ``ctx.session`` inside a FastMCP tool handler). Tests
    pass fakes that implement the same single method.

    Keeping this as a Protocol — rather than importing ``ServerSession`` directly —
    lets the bridge accept adapters, mocks, and forks without inheritance, which
    is what Metis's existing duck-typed ctx handling already assumes.
    """

    async def create_message(
        self,
        **kwargs: Any,
    ) -> CreateMessageResult | CreateMessageResultWithTools:
        """Issue an MCP ``sampling/createMessage`` request and await the result.

        Kwargs mirror ``ServerSession.create_message`` — ``messages``,
        ``max_tokens``, ``system_prompt``, ``tools``, ``tool_choice``,
        ``stop_sequences``, ``temperature``, ``model_preferences``, ``metadata``,
        ``include_context``, ``related_request_id``.
        """
        ...
