"""The token endpoint's two VM grants: a person's sign-in for their VM's credential, and a VM's exchange.

Every exchange rotates the credential; a lost reply may be retried once
within 60 seconds; anything else rotated-away presented again revokes
the family (`app.services.cloud.vm_credentials`, `docs/service-identity.md` §16).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from app.db.sql.models.vm_credential import IDLE
from app.db.sql.organizations import OrganizationDB
from app.db.sql.vm_credentials import Rotation, VmCredentialsDB
from app.security.producer import ProducerRef
from app.services.auth.jwt_service import JWTService, PERSON
from app.services.cloud.vm_credential_records import VmCredentialRecord
from app.services.cloud.vm_credential_string import VmCredentialString
from app.services.cloud.vm_credentials import VmCredentials
from app.services.cloud.vm_holders import TOKEN_LIFETIME, VmHolders
from app.services.identity.errors import OAuthError
from app.services.identity.people_in_organisations import PeopleInOrganisations
from app.services.identity.token_requests import IssuedTokens, TokenRequest

logger = logging.getLogger(__name__)

#: The person's own sign-in, exchanged for their VM's credential.
VM_SIGN_IN = 'urn:igni:params:oauth:grant-type:vm-sign-in'
#: A VM credential, exchanged for a short-lived token and its successor.
VM_CREDENTIAL = 'urn:igni:params:oauth:grant-type:vm-credential'
VM_GRANTS = (VM_SIGN_IN, VM_CREDENTIAL)


class VmCredentialGrants:
    def __init__(
        self,
        *,
        credentials: VmCredentials | None = None,
        db: VmCredentialsDB | None = None,
        organizations: OrganizationDB | None = None,
        holders: VmHolders | None = None,
        people: PeopleInOrganisations | None = None,
        tokens: JWTService | None = None,
    ) -> None:
        self._credentials = credentials or VmCredentials()
        self._db = db or VmCredentialsDB()
        self._organizations = organizations or OrganizationDB()
        self._holders = holders or VmHolders()
        self._people = people or PeopleInOrganisations()
        self._tokens = tokens or JWTService()

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        if request.grant_type == VM_SIGN_IN:
            return await self._sign_in(request)
        return await self._exchange(request)

    async def _sign_in(self, request: TokenRequest) -> IssuedTokens:
        """The person's own sign-in — their igni session — exchanged for their VM's credential.

        Only a session: a platform, service or app token is not one, so no
        platform client can obtain a person's credential this way.
        """
        claims = self._tokens.decode_token(request.subject_token or '')
        if not claims.user_id:
            raise OAuthError.invalid_grant("subject_token must be the person's igni session")
        if claims.api_key_id:
            # A VM credential lives 90 days; a key's session buys none (§22).
            raise OAuthError.invalid_grant('a session from an API key cannot sign a VM in')
        org_id = request.organisation or claims.org_id
        if not org_id:
            raise OAuthError.invalid_request('organisation is required: this session is not pinned to one')
        if claims.org_id and org_id != claims.org_id:
            raise OAuthError.invalid_target('this session is pinned to a different organisation')
        await self._people.check(claims.user_id, org_id, issued_at=claims.issued_at)
        issued = await self._credentials.issue(
            org_id, ProducerRef(kind=PERSON, id=claims.user_id), by_kind=PERSON, by_id=claims.user_id, for_pickup=False
        )
        return await self._tokens_for(org_id, issued.record, vm_credential=issued.vm_credential)

    async def _exchange(self, request: TokenRequest) -> IssuedTokens:
        presented = VmCredentialString.parse(request.vm_credential or '')
        if await self._organizations.get_by_id(presented.org_id) is None:
            raise OAuthError.invalid_grant('this VM credential is not valid')
        org_id = presented.org_id
        row = await self._db.get(org_id, presented.family_id)
        if row is None or row.revoked_at is not None:
            raise OAuthError.invalid_grant('this VM credential has been revoked')
        credential = VmCredentialRecord.of(org_id, row)
        if credential.idle_at(datetime.now(timezone.utc)):
            await self._db.revoke(org_id, row.id, reason=IDLE, by_id=None)
            raise OAuthError.invalid_grant('this VM credential expired after 90 days unused')
        await self._still_allowed(org_id, credential)
        successor = await self._rotate(org_id, presented)
        return await self._tokens_for(org_id, credential, vm_credential=successor.render())

    async def _rotate(self, org_id: str, presented: VmCredentialString) -> VmCredentialString:
        """The presented credential's successor, now the only one that works — or refused, as reuse or unknown."""
        successors = presented.successors()
        outcome = await self._db.rotate(
            org_id,
            presented.family_id,
            generation=presented.generation,
            presented_hash=presented.secret_hash,
            successor_hashes=[candidate.secret_hash for candidate in successors],
        )
        if outcome == Rotation.ROTATED:
            return successors[0]
        if outcome == Rotation.RETRIED:
            # The one retry of a lost reply: the successor it was issued,
            # under whichever key issued it, and a fresh token.
            current = await self._db.get(org_id, presented.family_id)
            return next(c for c in successors if current is not None and c.secret_hash == current.secret_hash)
        if outcome == Rotation.REUSED:
            logger.warning('VM credential %s was presented again after rotating: family revoked', presented.family_id)
            raise OAuthError.invalid_grant('this VM credential was already used; its family is revoked')
        raise OAuthError.invalid_grant('this VM credential is not valid')

    async def _still_allowed(self, org_id: str, credential: VmCredentialRecord) -> None:
        """The holder may still act: revoke the family, and refuse, if not."""
        holder = self._holders.of(credential.producer.kind)
        try:
            await holder.check(org_id, credential)
        except OAuthError:
            await self._db.revoke(org_id, credential.credential_id, reason=holder.gone, by_id=None)
            raise

    async def _tokens_for(self, org_id: str, credential: VmCredentialRecord, *, vm_credential: str) -> IssuedTokens:
        token = await self._holders.of(credential.producer.kind).token(org_id, credential.producer.id)
        return IssuedTokens(
            access_token=token, expires_in=int(TOKEN_LIFETIME.total_seconds()), vm_credential=vm_credential
        )
