"""Unit tests for LangChain <-> MCP message translators."""

from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    TextContent,
    ToolUseContent,
)

from metis.langchain._message_conversion import (
    langchain_messages_to_mcp,
    langchain_tools_to_mcp,
    mcp_result_to_ai_message,
)


class TestLangchainMessagesToMcp:
    def test_plain_user_message(self) -> None:
        out, system = langchain_messages_to_mcp([HumanMessage(content="hello")])
        assert system is None
        assert len(out) == 1
        assert out[0].role == "user"
        assert out[0].content.type == "text"
        assert out[0].content.text == "hello"

    def test_system_prompt_is_extracted_not_in_messages(self) -> None:
        out, system = langchain_messages_to_mcp(
            [
                SystemMessage(content="you are helpful"),
                HumanMessage(content="hi"),
            ]
        )
        assert system == "you are helpful"
        assert len(out) == 1
        assert out[0].role == "user"

    def test_multiple_system_messages_are_joined(self) -> None:
        out, system = langchain_messages_to_mcp(
            [
                SystemMessage(content="rule one"),
                SystemMessage(content="rule two"),
                HumanMessage(content="hi"),
            ]
        )
        assert system == "rule one\n\nrule two"
        assert len(out) == 1

    def test_ai_message_without_tool_calls_is_assistant_text(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                HumanMessage(content="q"),
                AIMessage(content="a"),
            ]
        )
        assert out[1].role == "assistant"
        assert out[1].content.text == "a"

    def test_ai_message_with_tool_calls_emits_tool_use_blocks(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                HumanMessage(content="use tools"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "add", "args": {"a": 1, "b": 2}, "id": "call-1"},
                        {"name": "mul", "args": {"a": 3, "b": 4}, "id": "call-2"},
                    ],
                ),
            ]
        )
        assistant = out[1]
        assert assistant.role == "assistant"
        assert isinstance(assistant.content, list)
        assert len(assistant.content) == 2
        assert all(b.type == "tool_use" for b in assistant.content)
        assert assistant.content[0].id == "call-1"
        assert assistant.content[0].name == "add"
        assert assistant.content[0].input == {"a": 1, "b": 2}

    def test_ai_message_with_text_and_tool_calls_includes_both(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                AIMessage(
                    content="I'll use a tool.",
                    tool_calls=[{"name": "t", "args": {}, "id": "c1"}],
                ),
            ]
        )
        content = out[0].content
        assert isinstance(content, list)
        assert content[0].type == "text"
        assert content[0].text == "I'll use a tool."
        assert content[1].type == "tool_use"

    def test_single_tool_message_becomes_user_message_with_tool_result(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "add", "args": {"a": 1, "b": 2}, "id": "c1"}],
                ),
                ToolMessage(content="3", tool_call_id="c1"),
            ]
        )
        tool_msg = out[1]
        assert tool_msg.role == "user"
        assert tool_msg.content.type == "tool_result"
        assert tool_msg.content.toolUseId == "c1"

    def test_multiple_tool_messages_collapse_into_one_user_message(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "a", "args": {}, "id": "c1"},
                        {"name": "b", "args": {}, "id": "c2"},
                    ],
                ),
                ToolMessage(content="1", tool_call_id="c1"),
                ToolMessage(content="2", tool_call_id="c2"),
            ]
        )
        # Assistant message + ONE collapsed user message carrying both tool results
        assert len(out) == 2
        tool_msg = out[1]
        assert tool_msg.role == "user"
        assert isinstance(tool_msg.content, list)
        assert len(tool_msg.content) == 2
        assert {b.toolUseId for b in tool_msg.content} == {"c1", "c2"}

    def test_tool_message_error_status_sets_is_error(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                ToolMessage(content="boom", tool_call_id="c1", status="error"),
            ]
        )
        assert out[0].content.isError is True

    def test_ai_message_without_id_gets_synthetic_id(self) -> None:
        out, _ = langchain_messages_to_mcp(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "t", "args": {"x": 1}, "id": None}],
                ),
            ]
        )
        block = out[0].content
        assert block.type == "tool_use"
        assert block.id is not None and block.id.startswith("call-")


class TestMcpResultToAiMessage:
    def test_plain_text_result(self) -> None:
        result = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text="hi there"),
            model="test-model",
            stopReason="endTurn",
        )
        msg = mcp_result_to_ai_message(result)
        assert msg.content == "hi there"
        assert msg.tool_calls == []

    def test_tool_use_result_populates_tool_calls(self) -> None:
        result = CreateMessageResultWithTools(
            role="assistant",
            content=[
                TextContent(type="text", text="thinking..."),
                ToolUseContent(
                    type="tool_use",
                    id="call-99",
                    name="search",
                    input={"q": "python"},
                ),
            ],
            model="test-model",
            stopReason="toolUse",
        )
        msg = mcp_result_to_ai_message(result)
        assert msg.content == "thinking..."
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["name"] == "search"
        assert msg.tool_calls[0]["args"] == {"q": "python"}
        assert msg.tool_calls[0]["id"] == "call-99"

    def test_tool_only_response_has_empty_content_and_tool_calls(self) -> None:
        result = CreateMessageResultWithTools(
            role="assistant",
            content=[
                ToolUseContent(type="tool_use", id="c1", name="t", input={}),
            ],
            model="m",
            stopReason="toolUse",
        )
        msg = mcp_result_to_ai_message(result)
        assert msg.content == ""
        assert len(msg.tool_calls) == 1


class TestLangchainToolsToMcp:
    def test_converts_structured_tool(self) -> None:
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        mcp_tools = langchain_tools_to_mcp([add])
        assert len(mcp_tools) == 1
        t = mcp_tools[0]
        assert t.name == "add"
        assert t.description == "Add two numbers."
        assert t.inputSchema["type"] == "object"
        assert set(t.inputSchema["properties"].keys()) == {"a", "b"}

    def test_converts_raw_dict_tool(self) -> None:
        raw = {
            "name": "custom",
            "description": "something",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
        mcp_tools = langchain_tools_to_mcp([raw])
        assert mcp_tools[0].name == "custom"
        assert mcp_tools[0].inputSchema["properties"]["q"]["type"] == "string"

    def test_strips_title_from_input_schema(self) -> None:
        @tool
        def greet(name: str) -> str:
            """Say hi."""
            return f"hi {name}"

        mcp_tools = langchain_tools_to_mcp([greet])
        assert "title" not in mcp_tools[0].inputSchema
