"""What the OIDC redirect hands forward to its own callback.

This survives a full round trip through the user's browser — the two
handlers that write and read it are different requests, minutes apart,
with an identity provider in between. It was a dict literal on one
side and three `.get(key, '')` reads with hand-written presence checks
on the other, which is the shape Rule 1 names: a contract described
only by the code that happens to build it.

The record itself never reaches the browser; only the opaque state
token does, and the contents are fetched from Redis on the way back.
That is deliberate and is why `pkce` can live here at all — see the
field notes below.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OidcSignInState(BaseModel):
    """The nonce, the PKCE verifier, and where to land afterwards.

    Both secrets are required with `min_length=1`, which is the check
    the callback used to write out twice. Empty is not merely invalid
    here, it is the dangerous value, so it is refused by the type
    rather than by a reader remembering to look.
    """

    #: Replay defence. The provider echoes it in the ID token and
    #: `OidcClient.exchange` compares them.
    nonce: str = Field(min_length=1)

    #: The PKCE verifier. **Never leaves the server** — only its S256
    #: challenge goes to the provider — which is why it is kept in the
    #: Redis-backed state record rather than in a cookie.
    #:
    #: Required, not optional. A code injected into the callback
    #: arrives with somebody else's state, and if a missing verifier
    #: were allowed through, the exchange would simply omit
    #: `code_verifier` and the provider — which cannot know we meant to
    #: use PKCE — would honour it. An optional defence is not one.
    pkce: str = Field(min_length=1)

    #: Where to send the browser after sign-in. Absent is ordinary.
    return_to: str = Field(default='', alias='returnTo')

    model_config = {'populate_by_name': True}
