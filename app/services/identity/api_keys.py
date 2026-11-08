"""Personal API keys: a person's access, handed to a program (`docs/service-identity.md` §22).

A person makes a key on the portal and gives it to a program — a script,
a CI job, `igni-client` driving their igni in Fleet. It names its
organisation and its row:

    igpak1.<org_id>.<key_id>.<secret>

The program exchanges it at the token endpoint (`API_KEY`) for a
15-minute session as the person, pinned to that organisation — the same
session a sign-in gives, so every igni server route and Fleet's gateway
take it — marked `api_key` so it cannot mint further credentials
(`app.security.api_key_sessions`).

A key works until it expires (30, 90 or 365 days, chosen when it is made)
or is revoked: by the person or an org admin on the portal, or when the
person signs out everywhere, leaves the organisation or is deactivated —
checked at every exchange, and eagerly where igni server does those.
Revoking a key stops new sessions at once; a session already bought
lives out its 15 minutes.

Only SHA-256 of the 256-bit secret is kept, compared in constant time;
the key is shown once, when it is made.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from app.api.base_v1.schema.portal.user import PortalUser
from app.db.sql.api_keys import ApiKeysDB
from app.db.sql.models.api_key import ApiKey, REVOKED_BY_ADMIN, REVOKED_BY_PERSON, SIGNED_OUT
from app.db.sql.organizations import OrganizationDB
from app.security.producer import ProducerRef
from app.security.subject import Subjects
from app.services.auth.jwt_service import JWTService, PERSON
from app.services.cloud.volume_sizes import VolumeSizes
from app.services.identity.errors import OAuthError
from app.services.identity.grants import IssuedTokens, PersonInOrganisation, TokenRequest
from app.services.portal.group_extensions import GroupExtensions

logger = logging.getLogger(__name__)

#: A personal API key, exchanged for a short-lived session as its person.
API_KEY = 'urn:igni:params:oauth:grant-type:api-key'

#: How long the session a key buys lives.
TOKEN_LIFETIME = timedelta(minutes=15)

_PREFIX = 'igpak1'


class ApiKeyString(BaseModel):
    """A key as a program holds it: its organisation, its row and its secret."""

    org_id: str
    key_id: str
    secret: str

    def render(self) -> str:
        return f'{_PREFIX}.{self.org_id}.{self.key_id}.{self.secret}'

    @property
    def secret_hash(self) -> str:
        return hashlib.sha256(self.secret.encode()).hexdigest()

    @classmethod
    def parse(cls, value: str) -> ApiKeyString:
        parts = value.strip().split('.')
        if len(parts) != 4 or parts[0] != _PREFIX or not all(parts[1:]):
            raise OAuthError.invalid_grant('not a personal API key')
        return cls(org_id=parts[1], key_id=parts[2], secret=parts[3])

    @classmethod
    def fresh(cls, org_id: str) -> ApiKeyString:
        return cls(org_id=org_id, key_id=secrets.token_hex(16), secret=secrets.token_urlsafe(32))


class CreateApiKey(BaseModel):
    """What the portal posts to make a key."""

    #: Which program it is for, so it can be told apart and revoked.
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: Literal[30, 90, 365] = 90


class ApiKeyRecord(BaseModel):
    """A key as the portal lists it: never the key."""

    key_id: str
    name: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    expired: bool


class CreatedApiKey(ApiKeyRecord):
    """A key just made — the only time `api_key` is ever shown."""

    api_key: str


class ApiKeyList(BaseModel):
    keys: list[ApiKeyRecord]


class Exchanged(BaseModel):
    """A key exchanged: the session, and whose key it was — for the audit row."""

    issued: IssuedTokens
    user_id: str
    org_id: str
    key_id: str


class ApiKeys:
    def __init__(self, *, organizations: OrganizationDB | None = None, subjects: Subjects | None = None) -> None:
        self._db = ApiKeysDB()
        self._organizations = organizations or OrganizationDB()
        self._subjects = subjects or Subjects()

    @staticmethod
    def record(row: ApiKey) -> ApiKeyRecord:
        return ApiKeyRecord(
            key_id=row.id,
            name=row.name,
            user_id=row.user_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            expired=row.expires_at <= datetime.now(timezone.utc),
        )

    # ── The portal ──────────────────────────────────────────────────

    async def create(self, user: PortalUser, request: CreateApiKey) -> CreatedApiKey:
        key = ApiKeyString.fresh(user.org_id)
        row = ApiKey(
            id=key.key_id,
            user_id=user.user_id,
            name=request.name.strip(),
            secret_hash=key.secret_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(days=request.expires_in_days),
        )
        stored = await self._db.add(user.org_id, row)
        logger.info('Made API key %s for %s in %s', key.key_id, user.user_id, user.org_id)
        return CreatedApiKey(**self.record(stored).model_dump(), api_key=key.render())

    async def listed(self, user: PortalUser) -> ApiKeyList:
        """An org admin sees the organisation's keys; anybody else their own."""
        rows = await self._db.working(user.org_id, user_id=None if user.org_role == 'admin' else user.user_id)
        return ApiKeyList(keys=[self.record(row) for row in rows])

    async def revoke(self, user: PortalUser, key_id: str) -> None:
        row = await self._db.get(user.org_id, key_id)
        own = row is not None and row.user_id == user.user_id
        if row is None or row.revoked_at is not None or not (own or user.org_role == 'admin'):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='API key not found')
        await self._db.revoke(
            user.org_id, key_id, reason=REVOKED_BY_PERSON if own else REVOKED_BY_ADMIN, by_id=user.user_id
        )

    async def revoke_person(self, user_id: str, *, org_ids: list[str] | None = None) -> None:
        """A person signed out everywhere, left or was deactivated: their keys stop now."""
        for org_id in (
            org_ids if org_ids is not None else await self._organizations.member_org_ids(user_id, active_only=False)
        ):
            await self._db.revoke_person(org_id, user_id, reason=SIGNED_OUT)

    # ── The token endpoint ──────────────────────────────────────────

    async def exchange(self, request: TokenRequest) -> Exchanged:
        presented = ApiKeyString.parse(request.api_key or '')
        if await OrganizationDB().get_by_id(presented.org_id) is None:
            raise OAuthError.invalid_grant('this API key is not valid')
        org_id = presented.org_id
        row = await self._db.get(org_id, presented.key_id)
        if row is None or not hmac.compare_digest(row.secret_hash, presented.secret_hash):
            raise OAuthError.invalid_grant('this API key is not valid')
        if row.revoked_at is not None:
            raise OAuthError.invalid_grant('this API key has been revoked')
        if row.expires_at <= datetime.now(timezone.utc):
            raise OAuthError.invalid_grant('this API key has expired')
        try:
            # The key's making stands in for a sign-in: signing out
            # everywhere, or leaving and rejoining, after it was made
            # ends it, as it ends every session from before.
            await PersonInOrganisation.check(
                row.user_id, org_id, issued_at=int(row.created_at.timestamp()), subjects=self._subjects
            )
        except OAuthError:
            await self._db.revoke(org_id, row.id, reason=SIGNED_OUT, by_id=None)
            raise
        await self._db.used(org_id, row.id)
        organisation = await OrganizationDB().get_by_id(org_id)
        token = JWTService().create_token(
            row.user_id,
            expires_delta=TOKEN_LIFETIME,
            org_id=org_id,
            org_name=str(organisation.display_name or organisation.slug or '') if organisation else None,
            group=await GroupExtensions().group_of(row.user_id, org_id),
            incarnation=await VolumeSizes.incarnation(org_id, ProducerRef(kind=PERSON, id=row.user_id)),
            api_key_id=row.id,
        )
        logger.info('Exchanged API key %s for a session of %s in %s', row.id, row.user_id, org_id)
        return Exchanged(
            issued=IssuedTokens(access_token=token, expires_in=int(TOKEN_LIFETIME.total_seconds())),
            user_id=row.user_id,
            org_id=org_id,
            key_id=row.id,
        )
