"""LangChain BaseChatModel that routes completions through MCP sampling.

Lets any LangChain-powered framework (including deepagents) run on the MCP
client's LLM via `ServerSession.create_message(...)`, so the server-side
agent does not need its own API key. Tokens bill to the originating
caller's subscription — the BYOT ("bring your own tokens") pattern.

Usage:

    from metis.langchain import MCPSamplingChatModel

    model = MCPSamplingChatModel(session=ctx.session)
    # pass to deepagents / langgraph / any LangChain consumer:
    agent = create_deep_agent(model=model, ...)

NOT responsible for:
- Streaming (MCP sampling is one-shot request/response)
- Tool execution (caller's framework handles that)
- Connecting to the MCP session (caller provides it)
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from metis.langchain._message_conversion import (
    langchain_messages_to_mcp,
    langchain_tools_to_mcp,
    mcp_result_to_ai_message,
)
from metis.langchain._protocols import SamplingSession

DEFAULT_MAX_TOKENS = 4096

IncludeContext = Literal["thisServer", "allServers"]


class MCPSamplingChatModel(BaseChatModel):
    """A LangChain chat model backed by an MCP ``ServerSession``.

    Pass any object matching the :class:`SamplingSession` protocol — an async
    ``create_message(**kwargs)`` method. In practice this is ``ctx.session``
    from a FastMCP tool handler.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: SamplingSession = Field(
        description="Object with an async create_message(**kwargs) method — typically ctx.session.",
    )
    default_max_tokens: int = Field(
        default=DEFAULT_MAX_TOKENS,
        description="max_tokens passed to create_message when the caller doesn't override.",
    )
    include_context: IncludeContext | None = Field(
        default=None,
        description=(
            "MCP sampling includeContext value (spec-enumerated: thisServer|allServers|None)."
        ),
    )

    @property
    def _llm_type(self) -> str:
        return "mcp-sampling"

    def bind_tools(
        self,
        tools: list[BaseTool | type | dict[str, Any]],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable[Any, BaseMessage]:
        """Bind tools by translating to MCP Tool objects and threading via kwargs.

        LangChain's standard pattern: bind_tools returns a Runnable that, when
        invoked, calls _agenerate/_generate with the bound tools already in
        the kwargs, so the model receives them on every call.
        """
        mcp_tools = langchain_tools_to_mcp(tools)
        extra: dict[str, Any] = {"tools": mcp_tools}
        if tool_choice is not None:
            extra["tool_choice"] = tool_choice
        return self.bind(**extra, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        mcp_messages, system_prompt = langchain_messages_to_mcp(messages)

        call_kwargs: dict[str, Any] = {
            "messages": mcp_messages,
            "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
        }
        if system_prompt is not None:
            call_kwargs["system_prompt"] = system_prompt
        if stop is not None:
            call_kwargs["stop_sequences"] = stop
        for passthrough in ("tools", "tool_choice", "temperature", "model_preferences", "metadata"):
            if passthrough in kwargs and kwargs[passthrough] is not None:
                call_kwargs[passthrough] = kwargs[passthrough]
        if self.include_context is not None:
            call_kwargs["include_context"] = self.include_context

        result = await self.session.create_message(**call_kwargs)

        ai_message = mcp_result_to_ai_message(result)
        model_name = getattr(result, "model", None)
        stop_reason = getattr(result, "stopReason", None)
        # Surface on the message itself (standard LangChain pattern — survives aggregation)
        ai_message.response_metadata = {
            "model_name": model_name,
            "stop_reason": stop_reason,
        }
        return ChatResult(
            generations=[ChatGeneration(message=ai_message)],
            llm_output={"model": model_name, "stop_reason": stop_reason},
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync fallback — runs the async path on a fresh event loop.

        MCP sampling is inherently async; sync callers pay the event-loop cost.
        Prefer `ainvoke` / `agenerate` in async contexts.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop, None, **kwargs))
        raise RuntimeError(
            "MCPSamplingChatModel._generate was called from an active event loop. "
            "Use ainvoke() / agenerate() / the async API instead."
        )
