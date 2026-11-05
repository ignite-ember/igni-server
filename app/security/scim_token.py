"""Who may call the provisioning API.

SCIM is the identity provider acting as itself: no user is signed in,
there is no browser, and the caller is a scheduled job inside Okta or
Entra. So it carries its own credential rather than a person's — a
token tied to an employee would stop working the day that employee
left, which is precisely the event provisioning exists to handle.

Two rules, the same as the node registration token's for the same reasons:

* An unset ``scim_token`` means SCIM is **disabled**, and every route
  answers 503. Failing closed matters more than failing convenient:
  the alternative is a deployment that accepts unauthenticated
  requests to create and deprovision accounts because somebody forgot
  an environment variable.
* A missing or wrong token is **401**, never 403. There is no notion
  of an authorised-but-forbidden caller here — there is one caller.

Compared with :func:`hmac.compare_digest`, and a missing header is
compared as the empty string so the comparison takes the same time
either way.

A request the token admits is attributed, in the audit trail, to the
identity provider (:data:`SCIM_ACTOR`): there is one caller, and it is
not a person.
"""

import hmac

from fastapi import Header, Request, status

from app.api.scim.errors import scim_error
from app.config import settings
from app.middlewares.audit import set_audit_actor

#: Who a provisioning request is, in the audit trail.
SCIM_ACTOR = 'scim:identity-provider'


async def require_scim_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency guarding every ``/scim/v2`` route."""
    expected = settings.scim_token
    if not expected:
        raise scim_error(
            'SCIM provisioning is disabled: scim_token is not configured',
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    scheme, _, presented = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer':
        presented = ''
    if not hmac.compare_digest(presented, expected):
        raise scim_error('Missing or invalid bearer token', status.HTTP_401_UNAUTHORIZED)
    set_audit_actor(request, user_id=SCIM_ACTOR)
