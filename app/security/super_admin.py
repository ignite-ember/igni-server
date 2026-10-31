"""Super admins: the accounts that own the install.

A super admin is an account (``users.is_super_admin``), not a password.
The first account a deployment creates is one; super admins create
organisations, make other super admins, and change settings that belong
to the whole install rather than to one organisation.

The ``SUPER_ADMIN_PASSWORD_HASH`` key is something else now: a
**break-glass** way into deployment-wide settings for an install where
nobody can sign in (see ``deployment_admin``). It makes nobody a super
admin. :func:`looks_like_bcrypt` stays here because the startup check
and that path both validate it.
"""

import logging

from fastapi import Depends, HTTPException, status

from app.api.base_v1.schema.portal.user import PortalAccount
from app.security.portal_auth import get_current_account

logger = logging.getLogger(__name__)


#: A bcrypt hash is ``$2a$``/``$2b$``/``$2y$`` followed by the cost and a
#: 53-character salt+digest — 60 characters in total.
_BCRYPT_PREFIXES = ('$2a$', '$2b$', '$2y$')


def looks_like_bcrypt(value: str) -> bool:
    """Whether *value* could be a bcrypt hash at all.

    Exists because the setting is named ``..._PASSWORD_HASH`` and the
    natural thing to paste into it is a password. ``bcrypt.checkpw``
    answers that with ``ValueError: Invalid salt``, which surfaced as an
    unhandled 500 on every attempt and told the operator nothing.

    It fails closed either way — a plaintext value never authenticates,
    not even against itself, which was verified before this was added.
    The problem was diagnosis, not access.
    """
    return value.startswith(_BCRYPT_PREFIXES) and len(value) == 60


async def require_super_admin(account: PortalAccount = Depends(get_current_account)) -> PortalAccount:
    """The signed-in account, if it is a super admin; 403 otherwise."""
    if not account.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Super admin access required')
    return account
