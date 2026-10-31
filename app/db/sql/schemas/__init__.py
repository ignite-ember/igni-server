"""The values a ``*DB`` class takes and answers with.

``app/db/sql/models/`` holds the tables. This holds the shapes that
cross the door: what a query is asked for, and what it answers when
the answer must outlive the session it was read in.

**A SQLAlchemy row is not one of them.** A row that leaves its session
lazy-loads on attribute access, carries every column nobody upstairs
reads, and has to be expunged by hand before it can travel. Flattening
at the boundary — the ``from_row`` classmethod on a frozen model — is
what the rest of this codebase already does at its service boundaries
(``DocumentRef.from_row``, ``CommentRef.from_row``); this package gives
the database layer the same thing, so a query module stays a query
module instead of also being the home of the value types it answers
with.

**The one rule.** A schema here may be imported by any layer above it
and must never import one. It knows about ``app/db/sql/models/`` and
about nothing further up; the moment one of these needs a service, the
type belongs in that service's own ``schemas.py`` instead.

The package ships with :mod:`app.db.sql.schemas.notification` only.
Other query modules still keep their value types inline —
``access_requests.Ask`` is the obvious next one — and moving them is a
separate change, because each move is a rename visible to that
module's callers.
"""
