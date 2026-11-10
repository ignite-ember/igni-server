"""Authentication for the node registration API.

A GPU node announcing itself is not a user and has no session. It
presents a shared secret in ``X-Igni-Node-Token``, handed to it by the
same automation that started it.

Deliberately its own secret. This token goes into cluster start-up
scripts, Terraform, cloud-init, and whatever else the customer uses to
bring hardware up — a wide blast radius, and one that should be
rotatable without touching anything else.

Two behaviours:

* A missing or mismatched token is **401**, never 403.
* An **unset** token disables the API, and here that means **404** rather
  than 503. A deployment that describes its GPUs statically has no
  registration endpoint at all, and saying so is both honest and quieter
  than advertising a disabled one to anything scanning the surface.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.config import settings

NODE_TOKEN_HEADER = 'X-Igni-Node-Token'


async def require_node_token(
    x_igni_node_token: str | None = Header(default=None, alias=NODE_TOKEN_HEADER),
) -> None:
    """FastAPI dependency guarding every ``/nodes`` route."""
    expected = settings.node_registration_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Node registration is not enabled on this deployment',
        )
    # compare_digest over the raw strings; a missing header is treated as
    # an empty candidate so the comparison is constant-time either way.
    if not hmac.compare_digest(x_igni_node_token or '', expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'Missing or invalid {NODE_TOKEN_HEADER}',
        )
