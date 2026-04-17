"""Pure translators between LangChain messages and MCP sampling primitives.

Bridges these two representations:

    LangChain (langchain-core):  BaseMessage subclasses
        SystemMessage / HumanMessage / AIMessage / ToolMessage

    MCP sampling (mcp.types):    SamplingMessage + content blocks
        TextContent / ImageContent / ToolUseContent / ToolResultContent
        plus a separate system_prompt parameter on create_message()

NOT responsible for:
- Invoking the MCP session (see MCPSamplingChatModel)
- Tool execution (LangChain / deepagents handle that)
- Streaming (MCP sampling is one-shot)
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_json_schema
from mcp.types import (
    CreateMessageResult,
    SamplingMessage,
    TextContent,
    Tool,
    ToolResultContent,
    ToolUseContent,
)


def langchain_messages_to_mcp(
    messages: list[BaseMessage],
) -> tuple[list[SamplingMessage], str | None]:
    """Translate a LangChain message list into MCP SamplingMessages + system_prompt.

    System messages are extracted into a single system_prompt string (MCP
    sampling separates system from the message list). Consecutive ToolMessages
    are collapsed into one user SamplingMessage whose content is a list of
    ToolResultContent, as the MCP spec requires tool results to travel in
    dedicated user messages with no other content mixed in.
    """
    system_parts: list[str] = []
    out: list[SamplingMessage] = []
    pending_tool_results: list[ToolResultContent] = []

    def _flush_tool_results() -> None:
        if pending_tool_results:
            out.append(
                SamplingMessage(
                    role="user",
                    content=(
                        pending_tool_results[0]
                        if len(pending_tool_results) == 1
                        else list(pending_tool_results)
                    ),
                )
            )
            pending_tool_results.clear()

    for msg in messages:
        if isinstance(msg, SystemMessage):
            _flush_tool_results()
            system_parts.append(_stringify(msg.content))
            continue

        if isinstance(msg, ToolMessage):
            pending_tool_results.append(
                ToolResultContent(
                    type="tool_result",
                    toolUseId=msg.tool_call_id,
                    content=[TextContent(type="text", text=_stringify(msg.content))],
                    isError=(getattr(msg, "status", None) == "error") or None,
                )
            )
            continue

        # Any non-tool message closes the tool-result run.
        _flush_tool_results()

        if isinstance(msg, HumanMessage):
            out.append(
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=_stringify(msg.content)),
                )
            )
        elif isinstance(msg, AIMessage):
            out.append(_ai_message_to_sampling(msg))
        else:
            # Unknown subclass — best-effort: treat by role attribute
            role = "assistant" if getattr(msg, "type", "") == "ai" else "user"
            out.append(
                SamplingMessage(
                    role=role,  # type: ignore[arg-type]
                    content=TextContent(type="text", text=_stringify(msg.content)),
                )
            )

    _flush_tool_results()

    system_prompt = "\n\n".join(p for p in system_parts if p) or None
    return out, system_prompt


def _ai_message_to_sampling(msg: AIMessage) -> SamplingMessage:
    """Translate an AIMessage (possibly with tool_calls) to a SamplingMessage."""
    text = _stringify(msg.content)
    tool_calls = getattr(msg, "tool_calls", None) or []

    if not tool_calls:
        return SamplingMessage(
            role="assistant",
            content=TextContent(type="text", text=text),
        )

    blocks: list[TextContent | ToolUseContent] = []
    if text:
        blocks.append(TextContent(type="text", text=text))
    for call in tool_calls:
        blocks.append(
            ToolUseContent(
                type="tool_use",
                id=call.get("id") or _fallback_call_id(call),
                name=call["name"],
                input=call.get("args") or {},
            )
        )

    return SamplingMessage(
        role="assistant",
        content=blocks[0] if len(blocks) == 1 else blocks,
    )


def mcp_result_to_ai_message(result: CreateMessageResult) -> AIMessage:
    """Translate an MCP CreateMessageResult into a LangChain AIMessage.

    Handles:
    - Plain TextContent -> AIMessage(content=text)
    - Mixed list [TextContent, ToolUseContent, ...] -> AIMessage with tool_calls
    - Tool-only list -> AIMessage(content="", tool_calls=[...])
    """
    content = result.content
    text = ""
    tool_calls: list[dict[str, Any]] = []

    blocks: list[Any] = content if isinstance(content, list) else [content]

    for block in blocks:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "text":
            text += _get_attr_or_key(block, "text") or ""
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "name": _get_attr_or_key(block, "name"),
                    "args": _get_attr_or_key(block, "input") or {},
                    "id": _get_attr_or_key(block, "id"),
                    "type": "tool_call",
                }
            )
        # image / audio / tool_result: ignored in assistant output for now

    return AIMessage(content=text, tool_calls=tool_calls)


def langchain_tools_to_mcp(
    tools: list[BaseTool | type | dict[str, Any]],
) -> list[Tool]:
    """Translate LangChain tool specs into MCP Tool objects for sampling requests."""
    mcp_tools: list[Tool] = []
    for t in tools:
        if isinstance(t, dict):
            # Already-formatted tool dict — trust the caller
            mcp_tools.append(
                Tool(
                    name=t["name"],
                    description=t.get("description"),
                    inputSchema=t.get("inputSchema") or t.get("parameters") or {"type": "object"},
                )
            )
            continue

        if isinstance(t, BaseTool):
            schema: dict[str, Any]
            if t.args_schema is not None:
                raw = convert_to_json_schema(t.args_schema)
                schema = _strip_schema_metadata(raw)
            else:
                schema = {"type": "object", "properties": {}}
            mcp_tools.append(
                Tool(
                    name=t.name,
                    description=t.description or None,
                    inputSchema=schema,
                )
            )
            continue

        # Assume it's a pydantic class or callable — try to convert its schema
        try:
            raw = convert_to_json_schema(t)
            name = getattr(t, "__name__", None) or raw.get("title") or "tool"
            mcp_tools.append(
                Tool(
                    name=name,
                    description=raw.get("description"),
                    inputSchema=_strip_schema_metadata(raw),
                )
            )
        except Exception as e:  # pragma: no cover - defensive
            raise TypeError(f"Cannot convert tool {t!r} to MCP Tool: {e}") from e

    return mcp_tools


def _strip_schema_metadata(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove title/description from top-level of a JSON schema before sending to MCP.

    MCP carries name/description at the Tool level, so the inputSchema shouldn't
    repeat them.
    """
    cleaned = {k: v for k, v in schema.items() if k not in ("title", "description")}
    if "type" not in cleaned:
        cleaned["type"] = "object"
    return cleaned


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # LangChain sometimes uses list[dict] content blocks; flatten to text.
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(json.dumps(item))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _get_attr_or_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _fallback_call_id(call: dict[str, Any]) -> str:
    # LangChain occasionally produces tool_calls without an id; synthesize one
    # deterministically from name+args so round-trips are stable.
    name = call.get("name", "tool")
    args_repr = json.dumps(call.get("args") or {}, sort_keys=True, default=str)
    return f"call-{abs(hash((name, args_repr))) & 0xFFFFFFFF:08x}"
