"""Turning a provider's single name string into the two columns we store.

Four places did this, in two spellings: `name.partition(' ')` in the
OIDC endpoint and the SCIM projection, `name.split(' ', 1)` with an
index guard in both OAuth arms. They agree on every input — checked
across empty, single-word, leading-space, trailing-space, double-space
and three-word names — so this is a consolidation and not a change.

The rule itself is a guess, and worth stating as one: identity
providers hand over a single display string, our `users` table has
`first_name` and `last_name`, and nothing in between knows which part
of "María del Carmen García" is which. First token, then the rest. It
is wrong for a good share of the world's names and it is what the
column layout asks for; keeping it in one place at least means it is
wrong identically everywhere, and there is somewhere to fix it.
"""

from __future__ import annotations


def split_full_name(full_name: str | None) -> tuple[str, str]:
    """Split a display name into (first, last). Never raises.

    `None` and `''` both give `('', '')` — a provider that supplies no
    name is ordinary, not an error.
    """
    first, _, last = (full_name or '').partition(' ')
    return first, last


def email_local_part(email: str) -> str:
    """The part before the `@`, as a last-resort display name.

    Used when a provider authenticates somebody but supplies no name
    at all. It is a real identity decision — the person sees this as
    their first name — and it was written inline in a router, where
    nothing marked it as a decision at all.
    """
    return email.split('@', 1)[0]
