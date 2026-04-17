"""LangChain bridge — route any LangChain BaseChatModel call through MCP sampling.

Lets deepagents / LangGraph / any LangChain-powered framework run without a
direct LLM API key, by delegating completions to the MCP client's LLM via
``ServerSession.create_message()``.

Installed as an optional extra:

    pip install metis[langchain-bridge]

Usage:

    from metis.langchain import MCPSamplingChatModel

    # inside a FastMCP tool handler:
    model = MCPSamplingChatModel(session=ctx.session)

NOT responsible for:
- Metis task queue logic (see metis.TaskQueue)
- MCP tool registration (see metis.presentation.worker_tools / trigger_tools)
"""

from metis.langchain.sampling_chat_model import MCPSamplingChatModel

__all__ = ["MCPSamplingChatModel"]
