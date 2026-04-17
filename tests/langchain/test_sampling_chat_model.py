"""Integration tests for MCPSamplingChatModel using a fake MCP session."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    TextContent,
    ToolUseContent,
)

from metis.langchain import MCPSamplingChatModel


class FakeServerSession:
    """Stand-in for mcp.server.session.ServerSession used by MCPSamplingChatModel.

    Captures every create_message call and returns scripted responses.
    """

    def __init__(
        self,
        responses: list[CreateMessageResult | CreateMessageResultWithTools] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def enqueue(self, result: CreateMessageResult | CreateMessageResultWithTools) -> None:
        self._responses.append(result)

    async def create_message(self, **kwargs: Any):  # noqa: ANN202
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("FakeServerSession ran out of scripted responses")
        return self._responses.pop(0)


def _text_result(text: str, model: str = "fake-model") -> CreateMessageResult:
    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=text),
        model=model,
        stopReason="endTurn",
    )


class TestPlainCompletion:
    async def test_round_trip(self) -> None:
        session = FakeServerSession(responses=[_text_result("hello!")])
        model = MCPSamplingChatModel(session=session)

        result = await model.ainvoke([HumanMessage(content="say hi")])

        assert isinstance(result, AIMessage)
        assert result.content == "hello!"
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["max_tokens"] == 4096
        assert len(call["messages"]) == 1
        assert call["messages"][0].role == "user"

    async def test_system_message_becomes_system_prompt(self) -> None:
        session = FakeServerSession(responses=[_text_result("ok")])
        model = MCPSamplingChatModel(session=session)

        await model.ainvoke(
            [
                SystemMessage(content="be terse"),
                HumanMessage(content="q"),
            ]
        )

        call = session.calls[0]
        assert call["system_prompt"] == "be terse"
        assert len(call["messages"]) == 1
        assert call["messages"][0].role == "user"

    async def test_stop_sequences_passed_through(self) -> None:
        session = FakeServerSession(responses=[_text_result("ok")])
        model = MCPSamplingChatModel(session=session)

        await model.ainvoke([HumanMessage(content="q")], stop=["STOP", "END"])

        assert session.calls[0]["stop_sequences"] == ["STOP", "END"]

    async def test_default_max_tokens_override(self) -> None:
        session = FakeServerSession(responses=[_text_result("ok")])
        model = MCPSamplingChatModel(session=session, default_max_tokens=256)

        await model.ainvoke([HumanMessage(content="q")])

        assert session.calls[0]["max_tokens"] == 256

    async def test_response_metadata_includes_model_and_stop_reason(self) -> None:
        session = FakeServerSession(responses=[_text_result("ok", model="custom-m")])
        model = MCPSamplingChatModel(session=session)

        result: AIMessage = await model.ainvoke([HumanMessage(content="q")])

        assert result.response_metadata["model_name"] == "custom-m"
        assert result.response_metadata["stop_reason"] == "endTurn"


class TestToolCalling:
    async def test_bind_tools_forwards_mcp_tools(self) -> None:
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        session = FakeServerSession(responses=[_text_result("done")])
        bound = MCPSamplingChatModel(session=session).bind_tools([add])

        await bound.ainvoke([HumanMessage(content="q")])

        mcp_tools = session.calls[0]["tools"]
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "add"

    async def test_tool_use_response_surfaces_as_tool_calls(self) -> None:
        response = CreateMessageResultWithTools(
            role="assistant",
            content=[
                TextContent(type="text", text="calling the tool"),
                ToolUseContent(
                    type="tool_use",
                    id="call-a",
                    name="search",
                    input={"q": "python"},
                ),
            ],
            model="m",
            stopReason="toolUse",
        )
        session = FakeServerSession(responses=[response])
        model = MCPSamplingChatModel(session=session)

        result = await model.ainvoke([HumanMessage(content="search something")])

        assert result.content == "calling the tool"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "search"
        assert result.tool_calls[0]["args"] == {"q": "python"}
        assert result.tool_calls[0]["id"] == "call-a"

    async def test_full_tool_call_then_result_round_trip(self) -> None:
        """Emulate the two-step flow: assistant emits tool_use, caller supplies ToolMessage."""
        first = CreateMessageResultWithTools(
            role="assistant",
            content=[
                ToolUseContent(type="tool_use", id="c1", name="add", input={"a": 1, "b": 2}),
            ],
            model="m",
            stopReason="toolUse",
        )
        final = _text_result("the answer is 3")
        session = FakeServerSession(responses=[first, final])
        model = MCPSamplingChatModel(session=session)

        # Turn 1 — ask; get a tool_use back
        ai1: AIMessage = await model.ainvoke([HumanMessage(content="what's 1+2?")])
        assert len(ai1.tool_calls) == 1

        # Turn 2 — reply with a ToolMessage carrying the result
        ai2: AIMessage = await model.ainvoke(
            [
                HumanMessage(content="what's 1+2?"),
                ai1,
                ToolMessage(content="3", tool_call_id="c1"),
            ]
        )
        assert ai2.content == "the answer is 3"

        # Second call should have translated the ToolMessage to a user tool_result block
        second_call = session.calls[1]
        messages = second_call["messages"]
        assert messages[1].role == "assistant"
        assert messages[2].role == "user"
        assert messages[2].content.type == "tool_result"
        assert messages[2].content.toolUseId == "c1"


class TestSyncFallback:
    def test_invoke_from_sync_context_runs_async(self) -> None:
        session = FakeServerSession(responses=[_text_result("sync ok")])
        model = MCPSamplingChatModel(session=session)

        result = model.invoke([HumanMessage(content="q")])

        assert result.content == "sync ok"
