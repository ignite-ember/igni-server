"""Portal-specific auth helpers — resolve user identity, org context, and role."""

import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.api.base_v1.schema.portal.user import PortalAccount, PortalUser
from app.config import settings
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.middlewares.request_audit import RequestAudit
from app.security.bypass_identity import BYPASS_EMAIL, BYPASS_FULL_NAME, BYPASS_ORG_ID, BYPASS_USER_ID
from app.security.membership_sessions import MembershipSessions
from app.security.subject import Subjects
from app.services.auth.jwt_service import JWTService

logger = logging.getLogger(__name__)


def _bypass_user() -> PortalUser:
    """A fresh bypass identity each time it is asked for.

    This was `_BYPASS_USER`, a module-level `PortalUser`. Pydantic
    models are mutable by default, so a *privileged* identity — admin
    of an org — sat in module state where any importer could rewrite
    its `org_role` or its `user_id` for the life of the process.
    Nothing did. Nothing had to be able to.

    The ids and the address live in `bypass_identity`, shared with the
    startup check that refuses to boot when these rows are found in a
    production database.
    """
    return PortalUser(
        user_id=BYPASS_USER_ID,
        email=BYPASS_EMAIL,
        full_name=BYPASS_FULL_NAME,
        is_super_admin=True,
        org_id=BYPASS_ORG_ID,
        org_role='admin',
    )


_optional_oauth2 = OAuth2PasswordBearer(tokenUrl='token', auto_error=False)


#: What an org-scoped route answers to an account in no organisation yet.
#: A sentence for a person, because the portal shows it as it is.
NO_ORGANISATION = (
    "You're not in an organisation yet. Ask an admin of your organisation to invite you, "
    'or a super admin to create one.'
)


def _attributed(request: Request, user: PortalAccount) -> PortalAccount:
    """Resolve the actor *and* attribute the request, in one place.

    Both return paths of ``get_current_portal_user`` go through here so
    that attribution cannot be added to one and forgotten on the other —
    the mistake F80 and F88 are both records of. Every route behind this
    dependency is audited without doing anything itself.
    """
    RequestAudit.of(request).attribute_to(user_id=user.user_id, email=user.email, org_id=user.org_id)
    return user


async def get_current_portal_user(
    request: Request,
    authorization: str | None = Depends(_optional_oauth2),
) -> PortalUser:
    """The signed-in account *in its active organisation*; 403 when it has none.

    Every org-scoped route depends on this. An account starts in no
    organisation and joins one by invitation, so "none yet" is a normal
    state with a sentence of its own (:data:`NO_ORGANISATION`), not an
    error to recover from by making one up — which is what this used to
    do, creating a personal organisation on first sign-in.
    """
    resolved = await _resolve(request, authorization, stale_header_is_fatal=True)
    if resolved.org_id is None or resolved.org_role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NO_ORGANISATION)
    return PortalUser.model_validate(resolved.model_dump())


async def get_current_account(
    request: Request,
    authorization: str | None = Depends(_optional_oauth2),
) -> PortalAccount:
    """Resolve the signed-in account, and its active organisation if it has one.

    For routes about the account itself — its profile, the organisations
    it can see. A portal ``X-Org-Id`` naming an organisation the account
    is no longer in (it was deleted, or they were removed) falls back to
    their memberships instead of refusing: refusing here would lock them
    out of the very page that lets them pick another.
    """
    return await _resolve(request, authorization, stale_header_is_fatal=False)


async def _resolve(
    request: Request,
    authorization: str | None,
    *,
    stale_header_is_fatal: bool,
) -> PortalAccount:
    """The account, and the organisation it is acting in.

    The organisation is the token's (CLI), else the ``X-Org-Id`` header
    (portal), else the account's first active membership. None of those
    is an organisation of the account's own: there are none.

    An org-scoped route must refuse a header naming somebody else's
    organisation (``stale_header_is_fatal``): falling back would act on
    a different organisation from the one the portal is showing. A
    token's organisation is always refused when it is not theirs.
    """
    if settings.portal_auth_bypass:
        await UserDB().ensure_exists(
            _bypass_user().user_id,
            email=_bypass_user().email or 'demo@demo.invalid',
            full_name=_bypass_user().full_name,
        )
        await OrganizationDB().ensure_exists(
            org_id=BYPASS_ORG_ID,
            slug='demo',
            display_name='Demo Org',
            owner_user_id=_bypass_user().user_id,
        )
        # Re-read so the resolved PortalUser reflects DB edits (e.g. the
        # user updated their display name from the settings page).
        local_user = await UserDB().get_by_user_id(_bypass_user().user_id)
        return _attributed(
            request,
            PortalUser(
                user_id=_bypass_user().user_id,
                email=(str(local_user.email) if local_user and local_user.email else _bypass_user().email),
                full_name=(
                    str(local_user.full_name) if local_user and local_user.full_name else _bypass_user().full_name
                ),
                is_super_admin=True,
                org_id=BYPASS_ORG_ID,
                org_role='admin',
            ),
        )

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated',
        )

    claims = JWTService().decode_token(authorization)
    user_id = claims.user_id
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or expired token',
        )

    # A signed token for somebody who no longer exists is not a valid
    # token. This used to carry on with `local_user` as None — so a
    # deleted account kept working for the 30 days its token had left,
    # and the org resolution below *created* a fresh organisation for
    # them, with `admin`. F122.
    local_user = await Subjects().require_existing_user(user_id, issued_at=claims.issued_at)
    email = str(local_user.email) if local_user.email else None
    full_name = str(local_user.full_name) if local_user.full_name else None

    # Org resolution priority: token claim (CLI) > header (portal) > first membership
    token_org_id: str | None = claims.org_id
    requested_org_id: str | None = token_org_id or request.headers.get('x-org-id')

    org_id: str | None = None
    org_role: str | None = None
    membership = await TeamDB().get_active_member(requested_org_id, user_id) if requested_org_id else None
    if requested_org_id and membership is None and (token_org_id or stale_header_is_fatal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Not a member of this organization',
        )
    if token_org_id and membership is not None:
        # Pinned to this organisation: only for the membership it was signed in under.
        MembershipSessions.require_current(membership, claims.issued_at)
    if requested_org_id and membership is not None:
        org_id = requested_org_id
        org_role = str(membership.role)
    else:
        memberships = await OrganizationDB().list_user_orgs(user_id)
        if memberships:
            org, first = memberships[0]
            org_id, org_role = str(org.id), str(first.role)

    return _attributed(
        request,
        PortalAccount(
            user_id=user_id,
            email=email,
            full_name=full_name,
            is_super_admin=bool(local_user.is_super_admin),
            session_issued_at=claims.issued_at,
            org_id=org_id,
            org_role=org_role,
        ),
    )


async def get_current_account_optional(
    request: Request,
    authorization: str | None = Depends(_optional_oauth2),
) -> PortalAccount | None:
    """The signed-in account, or ``None`` when there is no usable session."""
    if not settings.portal_auth_bypass and not authorization:
        return None
    try:
        return await get_current_account(request, authorization)
    except HTTPException:
        return None


async def require_admin(
    current_user: PortalUser = Depends(get_current_portal_user),
) -> PortalUser:
    """Dependency that ensures the current user is an admin of the active org."""
    if current_user.org_role != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required',
        )
    return current_user
