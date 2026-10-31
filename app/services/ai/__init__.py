"""Inference backend pool — the customer's own GPU nodes.

Not a set of upstream provider API keys: igni is self-hosted, and this
routes across the vLLM instances the customer runs. See ``schemas.py``
for why capacity here is a concurrency gauge rather than a rolling
window.

Deliberately empty of re-exports. This package's ``__init__`` used to
pull in the pool, the provider and the manager, which meant that
importing so much as ``InferenceBackend`` — a plain Pydantic model —
built the entire routing stack, and anything the provider imported became
a circular import by construction. Import from the module that defines
the thing.
"""
