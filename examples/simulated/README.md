# Simulated Dispatcher

A dispatcher that processes tasks without an LLM. Connects directly to the SQLite database, polls for tasks, and returns canned results based on task type.

Useful for:
- Testing the queue round-trip without API costs
- Verifying cross-process SQLite coordination
- Development and debugging of integrating MCP servers

## Usage

```bash
python examples/simulated/dispatcher.py --db C:/temp/metis.db
```

The dispatcher polls every 2 seconds. For each claimed task, it returns a predefined result based on the task type (`classify`, `validate`, `summarize`, `browser_extract`, `conflict_resolution`). Unknown types get a generic completion response.

## How it differs from a real dispatcher

| | Simulated | Real (LLM sub-agent) |
|---|---|---|
| Reasoning | Canned results | Actual LLM inference |
| Tools | None | MCP tools (browse-as-me, file access, etc.) |
| Cost | Free | API token cost |
| Setup | Just run the script | Configure metis-worker MCP, spawn sub-agent |

For the real dispatcher pattern, see `examples/live/dispatcher_prompt.md`.
