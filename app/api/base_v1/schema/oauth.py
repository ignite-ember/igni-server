"""What the social sign-in redirect hands forward to its own callback.

The counterpart of `OidcSignInState` for the providers in
`oauth_service.py`. It carries less — the provider round trip has no
nonce or PKCE verifier here — but it is the same kind of contract: two
requests, minutes apart, agreeing on the contents of the shared state
record. It was a `{'returnTo': ...}` literal on one side and a
`.get('returnTo', '')` on the other.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SocialSignInState(BaseModel):
    """Where to land after signing in. Absent is ordinary."""

    return_to: str = Field(default='', alias='returnTo')

    model_config = {'populate_by_name': True}
