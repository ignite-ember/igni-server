"""Provisioning, as the identity provider understands it.

A thin layer, on purpose. Everything underneath already exists:
creating an account is the path every sign-up goes through,
deprovisioning is the portal's own deactivation (``MemberStatus``) —
the account, which withdraws the person's sessions in the same
statement, and the membership, which is what the Team page shows —
and the door refuses them from the next request. SCIM adds the
vocabulary an IdP speaks and nothing else.

**``active: false`` is the whole point.** It is how Okta and Entra say
somebody left, and it is the event that used to arrive nowhere: an
administrator removed a person from the directory and igni carried on
serving them until a human remembered to go and deactivate them by
hand.

**Delete is treated as deactivate.** Providers differ — some send
``DELETE``, some only ever set ``active: false`` — and the two must
mean the same thing here, because a provider's choice of verb should
not decide whether a departed employee's documents survive. Removing
the row would take their work with it, which no directory operation
should do silently; `DELETE /v1/portal/me` is the deliberate path for
that and it is a person's own decision.

**Matching is by address.** SCIM has ``externalId`` for the provider's
own key, and it is echoed rather than stored: an account here is
identified by its email, every other sign-in path keys on the same
thing, and a second identity space would be a second thing that can
disagree about who somebody is.
"""

from __future__ import annotations

import logging
import secrets

import bcrypt
from fastapi import status

from app.api.base_v1.schema.scim import Email, Meta, Name, ScimUser
from app.api.scim.errors import scim_error
from app.db.sql.org_domains import OrgDomainDB
from app.db.sql.user import UserDB
from app.services.auth.person_name import split_full_name
from app.services.portal.member_status import MemberStatus

logger = logging.getLogger(__name__)

_users = UserDB()
_domains = OrgDomainDB()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def to_scim(user) -> ScimUser:
    """One of our accounts, as a provider expects to read it."""
    full_name = str(user.full_name or '')
    given, family = split_full_name(full_name)
    email = str(user.email)
    return ScimUser(
        id=str(user.user_id),
        userName=email,
        displayName=full_name,
        name=Name(givenName=given, familyName=family, formatted=full_name),
        emails=[Email(value=email, primary=True, type='work')],
        # The one attribute a provisioning run is really asking about.
        active=getattr(user, 'deactivated_at', None) is None,
        meta=Meta(
            resourceType='User',
            created=_iso(user.created_at),
            lastModified=_iso(user.updated_at),
            location=f'/scim/v2/Users/{user.user_id}',
        ),
    )


def address_of(payload: ScimUser) -> str:
    """The address to key on, from wherever the provider put it.

    ``userName`` is where it belongs and where every provider puts it.
    The primary email is the documented fallback for the ones that
    send a login name there instead, and taking the first email is the
    last resort — a provider that sends exactly one is not ambiguous
    about which it means.
    """
    if '@' in payload.userName:
        return payload.userName.strip().lower()
    primary = next((e.value for e in payload.emails if e.primary and e.value), '')
    fallback = primary or next((e.value for e in payload.emails if e.value), '')
    return fallback.strip().lower()


def wants_deactivation(operations) -> bool | None:
    """Whether a PATCH sets ``active``, and to what.

    ``None`` when it says nothing about it — a provider syncing a
    display name must not be read as a deprovisioning.

    Both spellings are read. Entra sends the attribute in ``path``
    with a scalar value; Okta sends no path and an object. Supporting
    one of the two would mean supporting one of the two providers.
    """
    answer: bool | None = None
    for operation in operations:
        if operation.op and operation.op.lower() not in ('replace', 'add'):
            continue
        path = (operation.path or '').strip().lower()
        if path == 'active':
            answer = _as_bool(operation.value)
        elif not path and isinstance(operation.value, dict):
            for key, value in operation.value.items():
                if key.lower() == 'active':
                    answer = _as_bool(value)
    return answer


def _as_bool(value) -> bool:
    """A provider may send a JSON boolean or the string form of one."""
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return bool(value)


async def organisation_of(address: str) -> str | None:
    """The organisation a person at *address* belongs to: whose audit trail records what SCIM did to them."""
    org = await _domains.org_for_address(address) if address else None
    return str(org.id) if org is not None else None


async def find_by_address(address: str):
    return await _users.get_by_email(address) if address else None


async def find_by_id(user_id: str):
    return await _users.get_by_user_id(user_id)


async def provision(payload: ScimUser):
    """Create the account a provider is telling us about.

    The password is random and discarded. An account provisioned from
    a directory signs in through that directory; giving it a usable
    local password would create a second way in that the directory
    cannot take away, which is the whole thing SCIM is here to fix.
    """
    address = address_of(payload)
    # Refused in SCIM's own shape, before an account exists: the same
    # rule `UserDB.create` applies, said in words a provider shows.
    if await _domains.org_for_address(address) is None:
        raise scim_error(
            f"{address} isn't on a domain any organisation here owns. A super admin adds the domain in the portal.",
            status.HTTP_409_CONFLICT,
        )
    full_name = payload.displayName or payload.name.formatted or ''
    if not full_name:
        full_name = f'{payload.name.givenName} {payload.name.familyName}'.strip()

    throwaway = bcrypt.hashpw(secrets.token_urlsafe(32).encode(), bcrypt.gensalt()).decode()
    user = await _users.create(
        email=address,
        hashed_password=throwaway,
        full_name=full_name or None,
        auth_provider='scim',
    )
    # A provider may create somebody already deactivated — a leaver
    # synced for the first time. Honoured rather than assumed active.
    if not payload.active:
        await MemberStatus().set_everywhere(str(user.user_id), active=False)
        # Re-read so the response carries the deactivated flag. Kept
        # only if it finds the row: the account was created moments
        # ago, so a miss means something removed it concurrently, and
        # answering `None` from a provisioning call that succeeded is
        # worse than answering with the row we already hold.
        refreshed = await _users.get_by_user_id(str(user.user_id))
        if refreshed is not None:
            user = refreshed
    logger.info('scim: provisioned %s', address)
    return user


async def set_active(user_id: str, *, active: bool):
    """The deprovisioning lever, and the thing SCIM is here for.

    Exactly what an org admin's deactivation and reactivation do, for
    each organisation the person is in — see :class:`MemberStatus`.
    Deactivating withdraws every session the person holds, in the same
    statement, so a directory removal stops them on their next request
    rather than when their token happens to expire.
    """
    await MemberStatus().set_everywhere(user_id, active=active)
    logger.info('scim: %s %s', 'deactivated' if not active else 'reactivated', user_id)
    return await _users.get_by_user_id(user_id)
