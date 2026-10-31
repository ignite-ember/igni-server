"""Telling a caller that something broke, without telling them what.

F120. `AuthService.sign_up` caught bare `Exception` and returned
`str(e)` in the response body, on an **unauthenticated** route.
Demonstrated against a real database by breaking the `users` INSERT: an
anonymous caller received 484 characters containing the full SQL
statement, every column of the `users` table including
`hashed_password`, the bound parameters, and a version-pinned
`sqlalche.me` link identifying the ORM and dialect.

## Why the other nine sites that do this are fine

Nine other places put a caught exception into a response detail, and all
nine catch a **named** exception whose message was written for whoever
receives it: `ModelNotAvailableError` lists the models this deployment
serves, `SecretCipherUnavailable` names the environment variable to set.
Those are
not leaks, they are the API.

The distinction is not "how much text" or "which status code" — it is
**who wrote the message**. A domain exception's message is part of the
contract. A `ProgrammingError` from asyncpg was written for the person
debugging asyncpg, and it says whatever it needs to say.

So: broad catches must not describe themselves to a caller. Named ones
may. `tests/test_an_error_says_nothing_it_should_not.py` enforces exactly
that, and this helper is the sanctioned way through it.

## The reference

A generic message with no way to correlate it is the other failure mode:
the caller reports "it said something went wrong" and nobody can find
which request they mean. So each failure gets a short random reference,
returned to the caller and logged with the traceback beside it. Eight hex
characters — enough to grep for, short enough to read down a phone.

Not derived from anything about the request. A hash of the email would
be stable across attempts, which is convenient right up to the point
where it becomes a way to test whether two failures came from the same
address.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, status


def internal_failure(exc: BaseException, *, doing: str, logger: logging.Logger) -> HTTPException:
    """Log `exc` with a reference; return a 500 that carries only that.

    ``doing`` is a short phrase in the third person — "creating the
    account", "storing the credential" — and reaches the caller. It says
    which step failed, which is all they can act on, and it must not
    include anything the caller supplied.

    Returns the exception rather than raising it, so the call site reads
    ``raise internal_failure(...) from exc`` and keeps the chain intact
    for anything above it.
    """
    reference = secrets.token_hex(4)
    # `exception` rather than `error`: the traceback is the entire value
    # of this record. Without it the reference points at a line that says
    # something failed, which the caller already knew.
    logger.exception('%s failed [ref %s]', doing.capitalize(), reference)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            f'Something went wrong {doing}. Reference {reference} — an administrator '
            'can find the details in the server log.'
        ),
    )
