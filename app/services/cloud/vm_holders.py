"""Who holds a VM credential — a person, an app or a space — and what each may still do with it.

Every exchange asks the holder's kind whether it may still act, and signs
the short-lived token it buys the way that kind's tokens are signed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import ClassVar

from app.db.sql.models.app_space import AppSpace, SPACE
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_credential import APP_REMOVED, SIGNED_OUT, SPACE_REMOVED
from app.db.sql.organizations import OrganizationDB
from app.db.sql.registered_clients import RegisteredClientDB
from app.security.producer import ProducerRef
from app.security.subject import Subjects
from app.services.auth.jwt_service import JWTService, PERSON, TokenUse
from app.services.auth.token_claims import CustomClaims
from app.services.cloud.vm_credential_records import VmCredentialRecord
from app.services.cloud.volume_sizes import VolumeSizes
from app.services.identity.errors import OAuthError
from app.services.identity.grants import PersonInOrganisation
from app.services.identity.space_tokens import SpaceTokens
from app.services.portal.group_extensions import GroupExtensions

#: How long the token a VM credential buys lives.
TOKEN_LIFETIME = timedelta(minutes=15)


class VmHolder(ABC):
    """The producers of one kind, as holders of VM credentials."""

    kind: ClassVar[str]
    #: Why a family is revoked once its holder may no longer act.
    gone: ClassVar[str]

    @abstractmethod
    async def check(self, org_id: str, credential: VmCredentialRecord) -> None:
        """Refuse with `invalid_grant` if the credential's holder may no longer act."""

    @abstractmethod
    async def token(self, org_id: str, producer_id: str) -> str:
        """The short-lived token one exchange buys the holder."""


class SpaceHolder(VmHolder):
    kind = SPACE
    gone = SPACE_REMOVED

    def __init__(self, *, spaces: SpaceTokens | None = None) -> None:
        self._spaces = spaces or SpaceTokens()

    async def check(self, org_id: str, credential: VmCredentialRecord) -> None:
        await self._live(org_id, credential.producer.id)

    async def token(self, org_id: str, producer_id: str) -> str:
        return self._spaces.sign(org_id, await self._live(org_id, producer_id), lifetime=TOKEN_LIFETIME)

    async def _live(self, org_id: str, space_id: str) -> AppSpace:
        space = await self._spaces.live(org_id, space_id)
        if space is None:
            raise OAuthError.invalid_grant('this space no longer exists')
        return space


class AppHolder(VmHolder):
    kind = APP
    gone = APP_REMOVED

    def __init__(self, *, clients: RegisteredClientDB | None = None, tokens: JWTService | None = None) -> None:
        self._clients = clients or RegisteredClientDB()
        self._tokens = tokens or JWTService()

    async def check(self, org_id: str, credential: VmCredentialRecord) -> None:
        client = await self._clients.app(org_id, credential.producer.id)
        if client is None or client.state != 'approved':
            raise OAuthError.invalid_grant('this app is no longer registered')

    async def token(self, org_id: str, producer_id: str) -> str:
        client = await self._clients.app(org_id, producer_id)
        return self._tokens.sign(
            subject=producer_id,
            audience=self._tokens.issuer,
            use=TokenUse.APP,
            lifetime=TOKEN_LIFETIME,
            claims=CustomClaims(org=org_id, kind=APP, name=client.name if client else ''),
        )


class PersonHolder(VmHolder):
    kind = PERSON
    gone = SIGNED_OUT

    def __init__(
        self,
        *,
        subjects: Subjects | None = None,
        organizations: OrganizationDB | None = None,
        groups: GroupExtensions | None = None,
        tokens: JWTService | None = None,
    ) -> None:
        self._subjects = subjects or Subjects()
        self._organizations = organizations or OrganizationDB()
        self._groups = groups or GroupExtensions()
        self._tokens = tokens or JWTService()

    async def check(self, org_id: str, credential: VmCredentialRecord) -> None:
        await PersonInOrganisation.check(
            credential.producer.id,
            org_id,
            issued_at=int(credential.created_at.timestamp()),
            subjects=self._subjects,
        )

    async def token(self, org_id: str, producer_id: str) -> str:
        organisation = await self._organizations.get_by_id(org_id)
        return self._tokens.create_token(
            producer_id,
            expires_delta=TOKEN_LIFETIME,
            org_id=org_id,
            org_name=str(organisation.display_name or organisation.slug or '') if organisation else None,
            group=await self._groups.group_of(producer_id, org_id),
            incarnation=await VolumeSizes.incarnation(org_id, ProducerRef(kind=PERSON, id=producer_id)),
        )


class VmHolders:
    """The holder of each producer kind."""

    def __init__(self, *, holders: list[VmHolder] | None = None) -> None:
        self._by_kind = {holder.kind: holder for holder in holders or [PersonHolder(), AppHolder(), SpaceHolder()]}

    def of(self, kind: str) -> VmHolder:
        return self._by_kind[kind]
