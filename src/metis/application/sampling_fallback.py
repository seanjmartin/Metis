"""Sampling fallback — synthesise a completed task via client-side sampling.

When the dispatcher is offline but the MCP client supports sampling, the
trigger side can still answer the caller's question by asking the client's
LLM directly via ctx.session.create_message().

This is a degraded mode: the response comes from the caller's conversation,
not a disposable dispatcher context, so it burns caller tokens and lacks
the cross-session persistence Metis normally provides. The task record
gets a `{"metis": {"fallback": "sampling"}}` marker so callers can tell.

NOT responsible for:
- Deciding when to fall back (caller's policy)
- Actually routing to the client (see _sample_via_ctx in task_queue_facade)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SamplingFallbackRequest:
    """A translated MCP sampling request derived from a Metis task payload."""

    messages: list[dict[str, Any]]
    system: str | None
    max_tokens: int


def build_sampling_request(
    task_type: str,
    payload: dict[str, Any],
    default_max_tokens: int = 1024,
) -> SamplingFallbackRequest:
    """Translate a Metis task payload into a sampling request.

    Metis tasks typically have payload.instructions; we wrap it as a user
    message. Callers can override by including explicit `messages` / `system`
    in the payload.
    """
    if "messages" in payload and isinstance(payload["messages"], list):
        messages = payload["messages"]
    else:
        instructions = payload.get("instructions") or ""
        messages = [{"role": "user", "content": instructions}]

    system = payload.get("system")
    if system is None:
        system = (
            f"You are responding to a '{task_type}' task. "
            "Return a structured answer as JSON when possible."
        )

    max_tokens = int(payload.get("max_tokens") or default_max_tokens)

    return SamplingFallbackRequest(
        messages=messages,
        system=system,
        max_tokens=max_tokens,
    )
