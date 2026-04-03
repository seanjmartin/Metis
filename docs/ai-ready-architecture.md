# AI-Ready Codebase: Principles & Practices

> Based on Matt Pocock's "Your codebase is NOT ready for AI", John Ousterhout's *A Philosophy of Software Design*, and additional research on AI-assisted development patterns.

---

## The Core Insight

**Good architecture for humans = good architecture for AI.** Clear boundaries, deep modules, consistent patterns, and strong types let AI reason confidently about what's safe to change. The solution to unreliable AI-generated code is not a better model — it's a better codebase.

---

## 1. Deep Modules

A module is *deep* when its public interface is significantly smaller than its implementation. The interface is the iceberg tip; the implementation is the submerged mass.

```
DEEP: PaymentProcessor.charge(amount, customerId)
  → hides: retry logic, idempotency, provider failover, audit logging

SHALLOW: addNullValueForAttribute(attribute)
  → callers might as well call data.set(attribute, null) directly
```

**The rule:** If a module's name + signature fully describes its implementation, it is too shallow. Accumulate related complexity behind one cohesive interface rather than distributing it across dozens of tiny utilities.

**Why it matters for AI:** AI reasons from interfaces. A narrow interface means AI can use a module confidently without reading thousands of lines. AI can also extend or replace an implementation without changing callers.

---

## 2. Layered Architecture

```
Presentation → Application → Domain ← Infrastructure
```

| Layer | Contains | Depends on | I/O? |
|-------|----------|-----------|------|
| **Domain** | Entities, value objects, protocols, events, business rules | Nothing | No |
| **Application** | Use cases (one per user action), orchestration | Domain only | No |
| **Infrastructure** | Protocol implementations, DB, APIs, file I/O | Domain | Yes |
| **Presentation** | Controllers, MCP tools, CLI commands | Application + Domain | Minimal |

**Dependency rule:** Each layer depends only on layers interior to it. Domain has zero external imports. Interfaces defined in domain; implementations in infrastructure.

**Why:** AI can freely change infrastructure (swap a DB driver, add an endpoint) without risking domain logic. A new feature always follows the same vertical path, which AI can generate reliably.

**Enforce via:** folder structure (`/domain`, `/application`, `/infrastructure`, `/presentation`), import linting, and code review.

---

## 3. Pattern Consistency

Every feature follows the same structural blueprint. When AI sees one feature, it can reproduce the pattern for any other.

| Concern | Pattern |
|---------|---------|
| Data access | Repository — interface in domain, implementation in infrastructure |
| Business workflows | Use Case — one class, one `execute()` method, typed input/output |
| External I/O | Adapter — wraps external SDK behind internal interface |
| Entry points | Controller/Tool — thin: validate input → call use case → format output |
| Errors | `Result[T]` for business errors, exceptions for infrastructure failures |
| Side effects | Domain events — published by use cases, consumed by infrastructure |

**Why:** AI generates new features by pattern-matching existing ones. Inconsistency means it can't match, and will invent something or copy a bad example.

---

## 4. Strong Typing & Explicit Contracts

Types are machine-readable documentation that AI parses without reading prose.

- **No `any`/untyped holes** — every input and output has a named type
- **Domain IDs are not primitives** — `UserId`, not `str`. Prevents AI from passing wrong ID types
- **Schemas at boundaries** — Pydantic/Zod validates all external inputs, mirroring domain types
- **Enums over magic strings** — `status: Literal["active", "suspended"]`, never undocumented values
- **Explicit return types** on all public methods — AI sees the contract at a glance

**Why:** Types anchor AI reasoning. It won't hallucinate fields that aren't in the type. Branded types prevent cross-entity substitution bugs.

---

## 5. Intention-Revealing Naming

- Files named after their primary export: `RememberUseCase.py`, not `utils.py`
- Methods are verbs; entities are nouns: `charge()`, `User`, `VaultPath`
- Booleans use `is_`/`has_`/`can_` prefixes
- Infrastructure carries its tech: `SqliteTokenStore`, `GwsAdapter`

**Why:** AI reads names as tokens. `process_data()` forces reading the body; `calculate_prorated_refund()` does not. `utils.py` gives zero signal; `format_currency.py` is self-describing.

---

## 6. Documentation as Architecture

Documentation lives with the code, not in a remote wiki.

**Essential artifacts:**
- `ARCHITECTURE.md` — layer responsibilities and dependency rules
- `PATTERNS.md` — canonical example of each pattern (the reference vertical slice)
- `DOMAIN_GLOSSARY.md` — ubiquitous language definitions
- `CLAUDE.md` / project rules — coding conventions AI tools auto-include
- ADRs — *why* specific patterns were chosen (not just what)

**Module-level docs should include "NOT responsible for":**
```python
class PaymentProcessor:
    """Charges customers via Stripe with retry and audit logging.

    NOT responsible for:
    - Creating customer records (see CustomerRepository)
    - Sending receipts (see ReceiptService)
    """
```

This tells AI exactly what *not* to add here, preventing scope creep.

---

## 7. Testing Strategy

Tests are executable specifications. AI reads them to understand what behavior to preserve.

| Layer | Test type | Characteristics |
|-------|-----------|----------------|
| Domain | Unit tests | Pure logic, no I/O, fast. 100% coverage of business rules |
| Application | Integration tests | Use cases with in-memory protocol implementations |
| Infrastructure | Infrastructure tests | Real I/O (files, DB, APIs) |
| Presentation | Contract tests | Input parsing and output formatting only |

- Test names describe behavior: `test_should_reject_expired_token`
- Use test builders for complex domain objects (fluent API)
- Mock only dependencies, never the module under test

---

## Anti-Patterns to Eliminate

| Anti-Pattern | Fix |
|---|---|
| Business logic in controllers/tools | Extract to use case |
| God service / god class | Split by responsibility into deep modules |
| `utils.py` / `helpers.py` | Name by function; move to owning module |
| Implicit status via null checks | Typed union or enum |
| Direct DB/file calls in controllers | Enforce repository/protocol pattern |
| Magic config strings | Typed config schema validated at startup |
| Shallow wrapper functions | Remove or promote to deep modules |
| Comments explaining "what" | Use intention-revealing names instead |

---

## Incremental Adoption (Existing Codebases)

Don't rewrite. Use the **Strangler Fig** pattern: build new structure alongside old, migrate callers, delete old code.

1. **Audit** — identify mixed concerns, shallow modules, implicit contracts, wide interfaces, pattern inconsistency
2. **Pick one vertical slice** — highest change frequency, most AI-touched, limited blast radius
3. **Extract domain** — list every business rule as a named behavior, create entity/use case encapsulating them
4. **Wire alongside** — new code runs parallel to old (feature flag or parallel route), leave old in place
5. **Migrate callers** — replace old calls with new use case calls, verify with tests
6. **Delete old code** — once all callers migrated
7. **Expand** — next vertical slice, using the first as template

**Key principle:** Each migration leaves the codebase working. Never have half-old, half-new features.

---

## Design for Deletion

The economics of AI-assisted development favor:

- **Highly decoupled systems** — modules that are easy to delete and rebuild
- **Clear boundaries** that isolate change impact
- **Standardized patterns** that make regeneration reliable
- **Duplication over coupling** — coupling is more expensive than duplication when AI can regenerate code

The best AI-ready codebase is one where any module can be confidently removed and rebuilt without cascading failures.

---

*Sources: Matt Pocock (2025–2026), John Ousterhout "A Philosophy of Software Design" (Stanford, 2018), MIT modular software research (2025), LLM coding assistant design space analysis (Lau & Guo, VLHCC 2025).*
