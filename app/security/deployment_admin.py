"""Who may change deployment-wide configuration.

Some settings belong to the *install* rather than to an organization —
the organisations themselves, for one. There is exactly one install, and
changing it changes it for everybody, so it is a **super admin's**: the
accounts that own the install (``users.is_super_admin``). An
organisation admin manages their organisation, not the deployment.

The ``X-Super-Admin-Key`` remains as **break-glass**, for the case a
session cannot cover: a deployment whose sign-in is broken, often
precisely because the provider configuration is what needs fixing. It
reaches these settings and nothing else, and it makes nobody a super
admin. Unset means there is no break-glass.

When it was a session, the caller learns who it was, and the audit
column has somebody to hold.
"""

from __future__ import annotations

import logging

import bcrypt
from fastapi import Depends, Header, HTTPException, Request, status

from app.api.base_v1.schema.portal.user import PortalAccount
from app.config import settings
from app.security.portal_auth import get_current_account_optional

logger = logging.getLogger(__name__)


async def require_deployment_admin(
    request: Request,
    x_super_admin_key: str | None = Header(
        default=None,
        description='Break-glass key; not needed when signed in as a super admin',
    ),
    current_user: PortalAccount | None = Depends(get_current_account_optional),
) -> PortalAccount | None:
    """Allow a super admin, or the correct break-glass key.

    Returns the acting user when the caller had a session, and ``None``
    when they came in on the bootstrap key — so callers can record
    attribution where it exists without pretending to have it where it
    does not.
    """
    if current_user is not None and current_user.is_super_admin:
        return current_user

    if x_super_admin_key:
        stored = settings.super_admin_password_hash
        if not stored:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail='Super-admin auth is not configured',
            )
        if bcrypt.checkpw(x_super_admin_key.encode(), stored.encode()):
            return None
        logger.warning(
            'Deployment-admin auth failed (IP: %s)',
            request.client.host if request.client else 'unknown',
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid super-admin key')

    # Distinguish "you are signed in but not an admin" from "you are not
    # signed in at all": the first is a permissions problem the user
    # cannot fix by re-authenticating, and 401 would send them in a loop.
    if current_user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Super admin access required to change deployment configuration',
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Sign in as a super admin, or supply the break-glass key',
    )
