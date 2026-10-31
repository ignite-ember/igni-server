"""Nothing lives here any more.

``CommitCacheService`` did: a Redis store for file contents fetched from
a provider during an indexing run, keyed by the Celery task id — a queue
DEC-13 has since removed as well. It was 270
lines, it had no callers anywhere in this repository, and the pipeline it
served moved to igni__code-index — which has no equivalent, so this was a
remnant rather than one half of a pair.

Deleted in B8 rather than left, for a reason beyond tidiness: its whole
purpose was to hold **the customer's source code** in Redis. That is not
in `docs/RECORDS_OF_PROCESSING.md`, it is not in any retention window,
and a Fernet key does not touch it. Dead code that would put source in a
cache is worse than dead code, because the next person to want a cache
finds it and uses it.

Its docstring also promised something it never did — "content is
automatically expired after job completion". There was no such
mechanism; entries carried a one-hour TTL and were otherwise cleared
only by a caller remembering to call `clear_job`.

The package stays so the import path is not a puzzle for anyone reading
an old traceback. Recoverable from git if a cache is wanted again — in
which case it belongs where the pipeline is.
"""
