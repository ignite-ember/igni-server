# Code grades

Companion to `CODE_STANDARDS.md`. That document prescribes how code here
is supposed to be written; this one judges how close a given file gets.

Applies to both `igni server` and `igni__code-index`.

---

## The grades

### A — perfect

Follows every hard rule and every applicable pattern. Few or no
anti-patterns. Single-responsibility modules under 300 lines. Every query
in a `*DB` class. Every structured value is a Pydantic model.
Module-top imports. Dependencies constructor-injected. Typed errors. No
workarounds and no workaround comments.

### B — acceptable

Minor violations only — one or two anti-patterns of minor severity, or a
rule that genuinely does not apply in this context. Nothing that produces
bugs or makes future changes harder. Mergeable as-is with a TODO.

### C — needs work before merge

Significant violations of the hard rules, or a cluster of minor
violations that compound into a maintainability problem. Should not merge
without either fixing the violations or explicitly deferring them to a
tracked issue with a named owner.

### D — blocking

Violates a core architectural invariant — SQL outside `app/db/sql/`, raw
dict literals in a wire contract, a hand-rolled engine outside
`SQLAlchemySessionManager`, a thread pool used for IO-bound concurrency,
a broken cross-repo contract — or has so many C-tier problems that the
combined signal is that author and reviewer have lost alignment on the
expected shape. Blocks merge until fixed or re-scoped to a smaller change
that earns a C or better.

---

## Grade by file tier

The codebase has three tiers; grade expectations are calibrated to the
tier.

| Tier | Examples | Notes |
|------|----------|-------|
| **Core** | `app/config.py`, `app/db/sql/__init__.py`, `app/celery_async_pool.py`, `app/services/ai/key_pool.py` | Low-volume, high-leverage, and a bug here is a whole-fleet bug. A is expected; C is a conversation. |
| **Pipeline** | `app/workflows/`, `app/dataset/`, `app/services/jsonl_changeset/` | Correctness here is measured against real repositories, and a regression is silent — it ships an index that looks complete. B is acceptable; C needs a deferral. |
| **Feature** | `app/api/base_v1/endpoints/`, `app/api/base_v1/schema/`, `app/services/portal/` | Highest volume, lowest blast radius. B is the floor; a new C-tier file should be fixed before merge. |

---

## What changes the grade

### Drops a file to C or D

- SQL (`select` / `insert` / `update` / `delete` / `session.execute`)
  outside `app/db/sql/`
- Dict literals on a wire contract (HTTP body, Celery argument, JSONL op)
  where a model exists or should
- `dict[str, Any]` as a public return type with a knowable key set
- Inline imports that are neither a circular-import break nor a genuinely
  optional dependency
- Bare `except: pass`
- `create_async_engine` outside `SQLAlchemySessionManager`
- A thread pool or `run_in_executor` used for IO-bound concurrency
- Free functions taking a state object (session, client, workflow) as
  first argument
- Module-level mutable state, or a class attribute used as a singleton
- A file over 600 lines with no split in sight
- Workaround comments that mark where the abstraction is wrong
- A backward-compatibility reader for a payload shape we control
- A cross-repo contract changed without changing its authoritative file
  and its pinning test

### Raises a file toward A

- Every query in a `*DB` method
- Every structured input and output a Pydantic model
- Layered architecture respected (router → service → `*DB` → model, and
  workflow → phase → service for the pipeline)
- Constructor injection throughout; the class is testable without
  patching a module global
- Keyword-only arguments past the first
- Errors typed by failure mode, so the class name is the diagnosis
- Per-item degradation bounded by an aggregate guard
- Phase progress persisted so an interrupted run resumes
- Tests that pin the exact contract, not the status code

---

## How to use these grades

The `refactor-to-standards` workflow uses them when auditing a file:

1. **Assess** — catalog every violation, count the anti-patterns, judge
   the tier, grade honestly.
2. **Design** — set the target grade honestly, not aspirationally. "We
   know this lands at B and aspire to A next pass" is a valid deferred
   graft in the synthesis notes.
3. **Review** — the reviewer judges whether the target grade was hit.
   A- or better with no blockers is a greenlight.

Grade changes on existing files belong in the PR description — "this file
was C, bringing it to B" or "this file is C; the blocking violations are
deferred to issue #X". A PR that improves a file by one full grade
(D → C, C → B, B → A) is a mergeable outcome even when the file does not
reach A.

---

## Current known-bad files

Honest baseline, so nobody has to rediscover it. These are the largest
standing violations of Rule 5 and Rule 4; each is a candidate for
`refactor-to-standards`.

| File | Lines | Primary problem |
|------|-------|-----------------|
| `app/dataset/dataset_creation/code_extractor.py` | 2628 | mixed concerns; extraction, orchestration and IO in one module |
| `app/dataset/ast_parsing/extractor/__init__.py` | 2132 | a package `__init__` doing the work of a package |
| `app/dataset/ast_parsing/extractor/schemas.py` | 2105 | one schemas file for 58 languages |
| `app/db/sql/dataset.py` | 1980 | free functions taking `session` as first arg — Rule 4 |
| `app/dataset/ast_parsing/extractor/definitions.py` | 1612 | as above |
| `app/services/jsonl_changeset/emitter.py` | 1471 | emission, walking and reference resolution in one class |
| `app/dataset/dataset_creation/file_summarizer.py` | 1365 | summarisation, retry policy and degradation handling together |
| `app/services/webhooks/gitlab_event_processor.py` | 1044 | event routing and processing not separated |
| `app/db/sql/repositories.py` | 1036 | one `*DB` class carrying several domains |

46 files exceed 300 lines. The count is the backlog, not the licence:
adding to one of these makes its grade worse.
