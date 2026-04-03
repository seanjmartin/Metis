# Patterns

This document walks through the canonical vertical slice: the enqueue-poll-deliver-wait round-trip. New features should follow this template.

## The Round-Trip: Enqueue → Poll → Deliver → Wait

### 1. Domain: Entity and Value Objects

`Task` entity owns its lifecycle transitions. Status changes are validated in the entity, not in stores or use cases.

```python
# domain/entities.py
task = Task(id=TaskId.generate(), type="classify", payload={...})
task.claim(worker_id)      # PENDING -> CLAIMED (validates transition)
task.complete(result)       # CLAIMED -> COMPLETE
task.consume()              # COMPLETE -> CONSUMED
```

### 2. Domain: Protocol (Repository Interface)

```python
# domain/protocols.py
class TaskStore(Protocol):
    async def insert(self, task: Task) -> None: ...
    async def claim_next(self, capabilities, worker_id) -> Task | None: ...
```

Interface defined in domain. Implementation in infrastructure.

### 3. Infrastructure: Store Implementation

```python
# infrastructure/sqlite_task_store.py
class SqliteTaskStore:
    async def claim_next(self, capabilities, worker_id) -> Task | None:
        # Atomic UPDATE...RETURNING prevents double-claiming
```

Carries its tech in the name: `Sqlite`TaskStore.

### 4. Application: Use Case

```python
# application/enqueue_task.py
class EnqueueTaskUseCase:
    def __init__(self, task_store: TaskStore) -> None: ...
    async def execute(self, input: EnqueueTaskInput) -> Result[TaskId]: ...
```

One class, one `execute()` method, typed input/output, returns `Result[T]`.

### 5. Presentation: MCP Tool

```python
# presentation/worker_server.py
@mcp.tool()
async def poll(worker_id, capabilities) -> dict:
    result = await _poll_use_case.execute(PollTaskInput(...))
    if result.value is None:
        return {"s": "e"}  # Minimal tokens
    return {"s": "t", "id": ..., "type": ..., "payload": ...}
```

Thin: validate → call use case → format output.

### 6. Public Facade: Deep Module

```python
# For integrating MCP servers — hides all layers
from metis import TaskQueue

queue = TaskQueue(db_path="~/.myserver/metis.db")
task_id = queue.enqueue(type="classify", payload={...})
result = await queue.wait_for_result(task_id, timeout=60)
```

## Pattern Summary

| Concern | Pattern | Example |
|---------|---------|---------|
| Data access | Repository (interface in domain, impl in infrastructure) | `TaskStore` / `SqliteTaskStore` |
| Business workflows | Use Case (one class, one `execute()`, typed I/O) | `EnqueueTaskUseCase` |
| Entry points | Tool (thin: validate → use case → format) | `poll()`, `deliver()` |
| Errors | `Result[T]` for business errors, exceptions for infra | `Ok(task_id)`, `Err(TaskNotFoundError(...))` |
| Public API | Deep module facade | `TaskQueue` |
