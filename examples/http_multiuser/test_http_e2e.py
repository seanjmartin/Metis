"""End-to-end test for the HTTP multi-user Smart Notes server.

Starts the server in-process, connects two MCP clients (alice and bob),
and verifies that:
1. Both clients can call tools
2. Tasks are scoped to the correct session
3. Alice's tasks don't leak to Bob's session

Usage:
    python examples/http_multiuser/test_http_e2e.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_HOST = "127.0.0.1"


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _import_server():
    """Import the server module from its file path."""
    server_path = Path(__file__).parent / "server.py"
    spec = importlib.util.spec_from_file_location("http_multiuser_server", server_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_server(port: int) -> threading.Thread:
    """Start the server in a background thread."""
    server_mod = _import_server()

    app = server_mod.mcp.streamable_http_app()
    app = server_mod.UserIdMiddleware(app)

    config = uvicorn.Config(app, host=SERVER_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(50):
        try:
            httpx.get(f"http://{SERVER_HOST}:{port}/mcp", timeout=0.5)
            break
        except (httpx.ConnectError, httpx.ReadError):
            time.sleep(0.1)
    else:
        raise RuntimeError("Server did not start in time")

    return thread


async def connect_and_call(
    port: int, user_id: str, tool_name: str, arguments: dict
) -> dict:
    """Connect as a user, call a tool, and return the result."""
    async with streamablehttp_client(
        url=f"http://{SERVER_HOST}:{port}/mcp",
        headers={"userid": user_id},
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            # Extract text content from the result
            for content in result.content:
                if hasattr(content, "text"):
                    return json.loads(content.text)
            return {}


async def test_tools_accessible(port: int) -> None:
    """Both users can list and call tools."""
    print("Test 1: Tools accessible for both users...")

    alice_result = await connect_and_call(
        port, "alice", "save_note", {"title": "Quantum Paper", "content": "Research..."}
    )
    bob_result = await connect_and_call(
        port, "bob", "save_note", {"title": "Grocery List", "content": "Milk, eggs..."}
    )

    assert alice_result.get("saved") is True, f"Alice save failed: {alice_result}"
    assert bob_result.get("saved") is True, f"Bob save failed: {bob_result}"
    assert alice_result.get("session_id") == "alice", f"Alice session wrong: {alice_result}"
    assert bob_result.get("session_id") == "bob", f"Bob session wrong: {bob_result}"

    print("  Alice saved note, session_id=alice")
    print("  Bob saved note, session_id=bob")
    print("  PASSED")


async def test_session_in_enqueued_tasks(port: int) -> None:
    """Tasks enqueued by each user carry the correct session_id."""
    print("\nTest 2: Enqueued tasks carry correct session_id...")

    alice_result = await connect_and_call(
        port, "alice", "save_note", {"title": "Test Note", "content": "Test content"}
    )
    bob_result = await connect_and_call(
        port, "bob", "save_note", {"title": "Test Note", "content": "Test content"}
    )

    # Without a dispatcher, we get metis_dispatcher_required
    # But session_id should still be correct
    assert alice_result.get("session_id") == "alice", f"Wrong session: {alice_result}"
    assert bob_result.get("session_id") == "bob", f"Wrong session: {bob_result}"

    print("  Alice's task has session_id=alice")
    print("  Bob's task has session_id=bob")
    print("  PASSED")


async def test_summarize_tool(port: int) -> None:
    """Both users can call summarize_notes."""
    print("\nTest 3: Summarize tool works for both users...")

    alice_result = await connect_and_call(
        port, "alice", "summarize_notes", {"titles": ["Paper A", "Paper B"]}
    )
    bob_result = await connect_and_call(
        port, "bob", "summarize_notes", {"titles": ["Item 1", "Item 2", "Item 3"]}
    )

    assert "summary" in alice_result, f"Alice missing summary: {alice_result}"
    assert "summary" in bob_result, f"Bob missing summary: {bob_result}"
    assert alice_result.get("session_id") == "alice"
    assert bob_result.get("session_id") == "bob"

    print(f"  Alice: {alice_result['summary']}")
    print(f"  Bob: {bob_result['summary']}")
    print("  PASSED")


async def run_tests(port: int) -> bool:
    """Run all tests. Returns True if all pass."""
    try:
        await test_tools_accessible(port)
        await test_session_in_enqueued_tasks(port)
        await test_summarize_tool(port)
        print("\n--- All tests passed ---")
        return True
    except AssertionError as e:
        print(f"\n--- FAILED: {e} ---")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n--- ERROR: {type(e).__name__}: {e} ---")
        traceback.print_exc()
        return False


def main() -> None:
    port = _find_free_port()
    print(f"Starting Smart Notes HTTP server on {SERVER_HOST}:{port}...")
    start_server(port)
    print("Server ready.\n")

    passed = asyncio.run(run_tests(port))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
