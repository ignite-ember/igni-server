# igni — code standards

Companion to `CODE_GRADES.md`, which judges how close a given file gets to
these standards. This document prescribes how to write code that belongs
here.

It applies to **both** igni server repositories — `igni server` (the free,
open-source core: auth, orgs, billing, chat, repository linking) and
`igni__code-index` (the premium indexing service). They are deployed
separately and talk over HTTP, but they are written on the same bones and
graded by the same bar. A file that would fail review in one fails in the
other.

Patterns are drawn from the layers that already got this right:
`app/db/sql/inference_nodes.py`, `app/services/notifications/`, `app/api/base_v1/schema/`.

---

## Hard rules — non-negotiable

These are invariants, not preferences.

### Rule 1: no raw dicts — always Pydantic

Any structured value with more than one field is a `pydantic.BaseModel`.
Applies to:

- function parameters and return values, at every layer
- internal state that survives more than one expression (accumulators,
  caches, counters carrying structured info)
- wire payloads: HTTP request/response bodies, Celery task arguments,
  cross-service calls, JSONL changeset ops

```python
# WRONG — dict literal for structured data
return {'sha': sha, 'parent_sha': parent, 'indexed_at': ts}

# RIGHT — a model that names the contract
return CommitOp(sha=sha, parent_sha=parent, indexed_at=ts)
```

Callsites that must hand a `dict` to a third party (SQLAlchemy JSONB
columns, `json.dumps`, an SDK that takes `**kwargs`) call
`model.model_dump(mode='json')` at the boundary. Never construct the dict
literal directly and never let one travel further than the one expression
that serialises it.

**Genuine exceptions, and only these:**

- A JSONB column's value at the moment it is written or read. The row is
  a model; `processed_data` inside it may be `dict` because Postgres
  stores it that way. Reconstitute into a model as soon as it is loaded.
- A payload received from an external system (a GitHub webhook body, an
  LLM provider response) *before* it has been validated. Validate on the
  first line of the handler; do not pass the raw dict onward.
- Genuinely unstructured maps: `dict[str, int]` counters keyed by a
  runtime value, `dict[str, str]` HTTP headers. The test is whether the
  keys are a fixed, known set — if they are, it is a model.

`dict[str, Any]` as a return type is the smell that this rule was skipped.

Applies retroactively to code you are actively editing: a dict literal in
your diff becomes a model in the same diff. Do **not** opportunistically
refactor dicts in code you are not touching.

### Rule 2: no inline imports — module-top only

All `import` and `from ... import` statements go at the top of the module.
Never inside a function, method, class body, or conditional branch.

```python
# WRONG
async def wake(self, user_ids: set[str]) -> None:
    from app.services.notifications.listeners import Listeners

# RIGHT
from app.services.notifications.listeners import Listeners

class Nudger:
    async def wake(self, user_ids: set[str]) -> None: ...
```

Two exceptions, each of which must carry a one-line comment saying which
it is:

- **Genuine circular-import break.** Prefer fixing the cycle — usually by
  moving the shared type into a `schemas.py` both sides import, as the
  inference types live in `app/services/ai/schemas.py`.
- **Optional heavy dependency** that must not be imported when the
  feature is off (`google.cloud.secretmanager` in a code path that runs
  without GCP credentials).

"It's only used in one function" and "it makes startup faster" are not
exceptions.

### Rule 3: queries live in the DB layer — never in services

All SQL lives in a `*DB` class under `app/db/sql/`. These classes extend
`SQLAlchemySessionManager` and are `SingletonMeta` singletons; each owns
one domain (`InferenceNodeDB`, `NotificationsDB`, `VmProfilesDB`).
A service may call several of them and apply business logic between the
calls, but it must not emit `select(...)`, `insert(...)`, `update(...)`,
`delete(...)` or `await session.execute(...)` itself.

```python
# WRONG — SQL in a service
class NotificationFeed:
    async def page(self, user_id: str) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        rows = await session.execute(stmt)

# RIGHT — the DB class owns the query
class NotificationFeed:
    async def page(self, user_id: str) -> list[Notification]:
        return await self._notifications.list_for(user_id)
```

The same rule applies to background runners, startup hooks and
anything else — "it isn't technically a service" does not license inline SQL.

### Rule 4: OOP — classes own data and behaviour

A free function whose first argument is a state object, and whose body
touches that object's attributes, is a method in the wrong place.
Promote it.

```python
# WRONG — free function reaching into a session
async def _insert_processed_file_row(session, *, repository_id, file_path, ...) -> int:
    result = await session.execute(insert(ProcessedFile).values(...))

# RIGHT — a method on the class that owns the session
class DatasetDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    async def _insert_processed_file_row(self, *, repository_id: str, ...) -> int:
        ...
```

Also promote:

- **Module-level mutable state** — globals, module dicts mutated by
  functions, class attributes used to smuggle state between calls
  (`Handler.token = ...`) — onto an instance.
- **Dispatch dicts of free functions** — `_HANDLERS = {'push': _on_push}`
  — into polymorphism (a base class with subclass overrides) or methods
  on a shared class.
- **Utility modules of related helpers** that all share an implicit
  subject — the subject wants to be a class.
- **Repeated `obj._private` reach-in** from outside `obj`'s class — the
  owner should expose a method.

**Genuine pure helpers may stay free functions.** All four must hold:

- takes only primitives or immutable values — no session, client,
  workflow or other state object as first argument
- touches no module-level mutable state
- is a stateless leaf (`content_uuid(path: str, body: str) -> UUID`)
- is small enough that a class would be ceremony without cohesion

**"The sibling files are all free functions" is not a defence.** Where
the package is procedural, the file you are editing is where OOP starts.
Note the inconsistency in the PR description; do not clone it.

### Rule 5: small modules, single responsibility

~300 lines is the smell to split a file. ~15 methods or ~8 constructor
parameters is the smell to split a class. One concern per file: one DB
class, one service, one router, one workflow phase.

This repository has 46 files over 300 lines and several over 1,500. They
are not a licence — they are the backlog. Adding to one makes the grade
worse; splitting one is a mergeable outcome on its own.

### Rule 6: one engine, one session manager

Never call `create_async_engine` outside `SQLAlchemySessionManager`, and
never hold a session across an `await` that yields to another task.
The manager caches an engine **per event loop** because Celery runs each
task body in its own `asyncio.run(...)`; a shared engine produces
`RuntimeError: Future attached to a different loop`. Anything that opens
its own connection defeats that and will fail intermittently under
concurrency, which is the worst way to find out.

### Rule 7: concurrency is coroutines, not processes or threads

Parallelism inside a task is `asyncio.gather` / `asyncio.Semaphore` on
one event loop. Do not reach for prefork workers, thread pools or
`run_in_executor` to get concurrency for IO-bound work — the pipeline is
designed to hold hundreds of in-flight LLM calls on a single loop, and a
thread pool caps that at its worker count while multiplying connections.
Threads are for genuinely blocking C extensions (tree-sitter parsing),
and then only behind an explicit, named boundary.

### Rule 8: tests pin the contract, not the status code

Asserting that two error paths both return 500 proves nothing. Assert the
exact response body, the exact ops emitted, the exact rows written. When
a shape is a cross-repo contract (the JSONL changeset, the service API
between igni server and igni__code-index), the test is the specification
— write it as though the other side's author will read it, because they
will.

---

## Architectural patterns

### Pattern 1: layered architecture

```
router  →  service  →  *DB class  →  SQLAlchemy model
        →  schema (Pydantic request/response)
```

**Router** — FastAPI `APIRouter`. `Depends` for auth and session
injection, delegates immediately to a service, returns a Pydantic
response model. No business logic, no SQL.

**Service** — orchestration, business logic, external calls, commit
management. Constructor-injected dependencies (see Pattern 2). No SQL.

**`*DB` class** — every query. Extends `SQLAlchemySessionManager`,
`SingletonMeta` singleton, one per domain.

**Model** — SQLAlchemy table definition. No business logic.

### Pattern 2: constructor injection, not module singletons

Dependencies arrive through `__init__` and are stored on `self` with a
leading underscore. A class must be constructible in a test without
patching a module global.

```python
# app/services/notifications/stream.py — the reference
class NotificationStream:
    def __init__(
        self,
        *,
        notifier: Listeners | None = None,
        keepalive_seconds: int = listeners.KEEPALIVE_INTERVAL,
        max_seconds: int = listeners.STREAM_MAX_SECONDS,
    ) -> None:
        self._notifier = notifier or Listeners()
        self._keepalive_seconds = keepalive_seconds
        self._max_seconds = max_seconds
```

The `*DB` classes are singletons, which is the one deliberate exception —
they are stateless query façades over a per-loop engine. Even so, pass
them in rather than reaching for `NotificationsDB()` in the middle of a method,
so the caller can substitute a fake.

### Pattern 3: keyword-only arguments past the first

Any method with more than two parameters makes them keyword-only with
`*`. Positional call sites silently survive a reordering; keyword ones do
not.

```python
async def check_access(self, *, user_id: str, repository: RepositorySchema) -> AccessResult:
```

### Pattern 4: typed errors, never bare except

Every failure mode the caller can act on differently gets its own
exception class, and the class name is the diagnosis. The pipeline's
`DegenerateOutputError` vs `AgnoAPIError` split exists precisely because
a model repetition loop and a provider outage used to produce the same
log line, which made a broken model indistinguishable from a flaky
network.

`except Exception: pass` is never acceptable. `except Exception:` with a
log line is acceptable only at a top-level boundary (a Celery task body,
a webhook handler) where the alternative is losing the event.

### Pattern 5: degrade loudly, then bound the damage

Where one item's failure should not kill a 5,000-file run, persist a
flagged placeholder rather than aborting — and then assert an aggregate
bound on the placeholders (`ai.max_degraded_ratio`) so a run that is
mostly placeholders fails rather than shipping. A per-item fallback
without an aggregate guard is how you ship an index that looks complete
and reads as garbage.

### Pattern 6: cross-repo contracts live in one named file

The JSONL changeset op shapes, and the igni server ↔ igni__code-index
service API, each have exactly one authoritative definition. Changing it
means changing that file, versioning the change, and updating the test
that pins it. Never infer a contract from what the other side currently
happens to accept.

### Pattern 7: resumability is a first-class requirement

Every long pipeline phase records its progress in Postgres before it
starts the next unit of work, so an interrupted run resumes rather than
restarting. New phases inherit this; a phase that cannot be resumed needs
a comment saying why.

---

## Anti-patterns

### AP1: `dict[str, Any]` in a signature

The most common way Rule 1 is skipped. If the keys are a known set, it is
a model. If you genuinely do not know the keys, say so in a comment.

### AP2: SQL outside `app/db/sql/`

`await session.execute(select(...))` in a service, a task, a workflow
phase or a router. It belongs in a `*DB` method.

### AP3: `**kwargs` in a public signature

Untyped at the call site; the type checker cannot catch a mismatch.
Define explicit parameters.

### AP4: bare `except: pass`

Swallows errors silently. See Pattern 4.

### AP5: a comment explaining a workaround instead of fixing the abstraction

A comment opening with "Fix for the bug where…", "Belt-and-suspenders
because…", or "Workaround: …" marks the place where the abstraction is
wrong. Either fix it, or name the invariant as a method whose docstring
carries the reason. A paragraph comment is not a home for an invariant.

### AP6: `# type: ignore` hiding a real mismatch

Acceptable for a third-party stub gap, with the error code named
(`# type: ignore[attr-defined]`). Not acceptable to paper over a place
where the typed API and the implementation genuinely disagree — that is a
bug with a suppression on it.

### AP7: `getattr(obj, 'x', default)` gymnastics for test-only construction

If a test builds an object via `__new__` and the production code has to
defend against half-built instances, the constructor contract is broken.
Fix the test to go through `__init__` or a builder.

### AP8: backward-compatibility readers for data you control

A validator that accepts both the old and new shape of your own payload
is permanent complexity bought to avoid a one-off migration. Migrate the
data, delete the old shape, and let the new one fail loudly on anything
missed. (See `scripts/migrate_pool_tiers_to_pools.py`.)

---

## Reference implementations — copy these

**New `*DB` class** — `app/db/sql/inference_nodes.py`. `InferenceNodeDB`
extends `SQLAlchemySessionManager`, one method per query, returns models.

**New service** — `app/services/notifications/` (`feed.py`, `nudge.py`,
`stream.py`). Constructor injection, keyword-only arguments, small
classes, one concern each.

**New schema** — `app/services/ai/schemas.py`. Pydantic models and the
exceptions beside them, separate from the classes that use them.

---

## Checklist — before merging

- [ ] Every structured value in the diff is a Pydantic model. No new
      `dict[str, Any]` in a signature.
- [ ] Every import is at module top, or carries a comment naming which
      exception applies.
- [ ] Every query is in a `*DB` method. No `select(...)` outside
      `app/db/sql/`.
- [ ] No free function takes a state object as its first argument.
- [ ] No new file over 300 lines without a note saying why.
- [ ] Dependencies are constructor-injected; the new class is
      constructible in a test without patching a global.
- [ ] Errors are typed; no bare `except: pass`.
- [ ] Tests assert the exact contract — body shape, emitted ops, rows
      written — not just the status code.
- [ ] If a cross-repo contract changed, its authoritative file and its
      pinning test both changed too.
