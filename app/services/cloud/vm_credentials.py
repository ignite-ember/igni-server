"""Each producer's VM holds its own credential; the platform never mints one a token.

A **VM credential** is for audience `igni-vm` and names its organisation,
its family and its generation:

    igvm1.<org_id>.<family_id>.<generation>.<secret>

The VM exchanges it at the token endpoint for a short-lived igni token
(15 minutes, the claims a session carries) and a **rotated** credential;
the one presented stops working. A VM whose reply was lost may present
the one it had again, once, within 60 seconds, and is answered with the
same successor; anything else rotated-away presented again is reuse: the
whole family is revoked. A family unused for 90 days expires.

* **People**: the person's cloud igni gets one when they sign in through
  the gateway — the sign-in's own session is exchanged for it
  (`VM_SIGN_IN`). Revoked on sign-out, on removal from the organisation
  or deactivation (checked at every exchange, and eagerly where igni
  server does those), and by the person or an org admin on the portal.
* **Apps**: an org admin enables the cloud for an app and igni server
  issues one, kept encrypted until the node agent picks it up — once —
  with its platform token when it first creates the app's VM. After that
  it lives only on the VM's volume. Re-issuing revokes the old family.

No route gives a platform client a token for a producer: the pickup
hands over a credential the server can never hand over again, and only
the VM holding it can exchange it (`docs/service-identity.md` §16).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import secrets

from fastapi import HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.db.sql.models.app_space import SPACE
from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_credential import (
    APP_REMOVED,
    IDLE,
    IGNI_VM,
    REPLACED,
    REVOKED_BY_ADMIN,
    REVOKED_BY_PERSON,
    SIGNED_OUT,
    SPACE_REMOVED,
    VmCredential,
)
from app.db.sql.organizations import OrganizationDB
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.revocations import FeedSignals
from app.db.sql.tenancy import TenantData
from app.db.sql.vm_credentials import Rotation, VmCredentialsDB
from app.security.producer import ProducerCaller, ProducerRef
from app.services.auth.jwt_service import JWTService, PERSON, TokenUse
from app.services.cloud.volume_sizes import VolumeSizes
from app.services.identity.errors import OAuthError
from app.services.identity.grants import IssuedTokens, PersonInOrganisation, TokenRequest
from app.services.identity.space_tokens import SpaceTokens
from app.services.portal.group_extensions import GroupExtensions
from app.services.secrets.cipher import decrypt_secret, encrypt_secret, previous_keys, SecretCipherUnavailable

logger = logging.getLogger(__name__)

#: The person's own sign-in, exchanged for their VM's credential.
VM_SIGN_IN = 'urn:igni:params:oauth:grant-type:vm-sign-in'
#: A VM credential, exchanged for a short-lived token and its successor.
VM_CREDENTIAL = 'urn:igni:params:oauth:grant-type:vm-credential'
VM_GRANTS = (VM_SIGN_IN, VM_CREDENTIAL)

#: How long the token a VM credential buys lives.
TOKEN_LIFETIME = timedelta(minutes=15)

#: A family unused this long stops working.
IDLE_LIMIT = timedelta(days=90)

_PREFIX = 'igvm1'


class VmCredentialString(BaseModel):
    """The parts of a VM credential."""

    org_id: str
    family_id: str
    generation: int
    secret: str

    def render(self) -> str:
        return f'{_PREFIX}.{self.org_id}.{self.family_id}.{self.generation}.{self.secret}'

    @property
    def secret_hash(self) -> str:
        return hashlib.sha256(self.secret.encode()).hexdigest()

    @classmethod
    def parse(cls, value: str) -> VmCredentialString:
        parts = value.split('.')
        if len(parts) != 5 or parts[0] != _PREFIX or not parts[3].isdigit():
            raise OAuthError.invalid_grant(f'not a VM credential for {IGNI_VM}')
        return cls(org_id=parts[1], family_id=parts[2], generation=int(parts[3]), secret=parts[4])

    @classmethod
    def fresh(cls, org_id: str, family_id: str, generation: int) -> VmCredentialString:
        return cls(org_id=org_id, family_id=family_id, generation=generation, secret=secrets.token_urlsafe(32))

    def successors(self) -> list[VmCredentialString]:
        """The credential that replaces this one, under each key this deployment holds, current first.

        Derived, not drawn: HMAC-SHA256 of the family, the next generation
        and this credential's secret, keyed from `SECRET_ENCRYPTION_KEY`.
        So a retry of a lost reply — which presents this credential again
        — recomputes exactly the successor it was issued, and igni server
        never stores a usable credential to answer it with: only its hash.
        Unpredictable without both the presented secret and the key. The
        previous keys are tried as well, so a key rotation inside the
        retry window does not turn a retry into a reuse.
        """
        keys = [settings.secret_encryption_key, *previous_keys()]
        message = f'{self.family_id}.{self.generation + 1}.{self.secret}'.encode()
        derived = []
        for key in keys:
            if not key:
                raise SecretCipherUnavailable('secret_encryption_key is not configured; VM credentials need it')
            mac = hmac.new(hashlib.sha256(b'igni-vm-credential:' + key.encode()).digest(), message, hashlib.sha256)
            derived.append(
                VmCredentialString(
                    org_id=self.org_id,
                    family_id=self.family_id,
                    generation=self.generation + 1,
                    secret=base64.urlsafe_b64encode(mac.digest()).rstrip(b'=').decode(),
                )
            )
        return derived


class VmCredentialRecord(BaseModel):
    """A family as the portal shows it: never the secret."""

    credential_id: str
    org_id: str
    producer: ProducerRef
    created_at: datetime
    last_used_at: datetime | None
    #: When it stops working if nobody uses it before then.
    idle_expires_at: datetime
    #: An app's, issued and not yet picked up by the node agent.
    awaiting_pickup: bool
    picked_up_at: datetime | None
    generation: int


class VmCredentialList(BaseModel):
    credentials: list[VmCredentialRecord]


class PickedUp(BaseModel):
    credential_id: str
    vm_credential: str


class VmCredentials:
    def __init__(self) -> None:
        self._db = VmCredentialsDB()

    @staticmethod
    def record(org_id: str, row: VmCredential) -> VmCredentialRecord:
        return VmCredentialRecord(
            credential_id=row.id,
            org_id=org_id,
            producer=ProducerRef(kind=row.producer_kind, id=row.producer_id),  # type: ignore[arg-type]
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            idle_expires_at=(row.last_used_at or row.created_at) + IDLE_LIMIT,
            awaiting_pickup=row.pending_pickup is not None,
            picked_up_at=row.picked_up_at,
            generation=row.generation,
        )

    async def issue(
        self, org_id: str, producer: ProducerRef, *, by_kind: str, by_id: str, for_pickup: bool
    ) -> tuple[VmCredential, str]:
        """A new family for the producer, replacing its working one. Returns the row and the credential."""
        family_id = secrets.token_hex(16)
        credential = VmCredentialString.fresh(org_id, family_id, 0)
        row = VmCredential(
            id=family_id,
            producer_kind=producer.kind,
            producer_id=producer.id,
            audience=IGNI_VM,
            generation=0,
            secret_hash=credential.secret_hash,
            previous_hashes=[],
            pending_pickup=encrypt_secret(credential.render()) if for_pickup else None,
            created_by_kind=by_kind,
            created_by_id=by_id,
        )
        stored = await self._db.issue(org_id, row, by_id=by_id, replaced_reason=REPLACED)
        logger.info('Issued VM credential %s for %s:%s in %s', family_id, producer.kind, producer.id, org_id)
        return stored, credential.render()

    # ── The token endpoint ──────────────────────────────────────────

    async def grant(self, request: TokenRequest) -> IssuedTokens:
        if request.grant_type == VM_SIGN_IN:
            return await self._sign_in(request)
        return await self._exchange(request)

    async def _sign_in(self, request: TokenRequest) -> IssuedTokens:
        """The person's own sign-in — their igni session — exchanged for their VM's credential.

        Only a session: a platform, service or app token is not one, so no
        platform client can obtain a person's credential this way.
        """
        claims = JWTService().decode_token(request.subject_token or '')
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
        await PersonInOrganisation.check(claims.user_id, org_id, issued_at=claims.issued_at)
        row, credential = await self.issue(
            org_id, ProducerRef(kind=PERSON, id=claims.user_id), by_kind=PERSON, by_id=claims.user_id, for_pickup=False
        )
        issued = await self._token_for(org_id, row)
        return issued.model_copy(update={'vm_credential': credential})

    async def _exchange(self, request: TokenRequest) -> IssuedTokens:
        presented = VmCredentialString.parse(request.vm_credential or '')
        if await OrganizationDB().get_by_id(presented.org_id) is None:
            raise OAuthError.invalid_grant('this VM credential is not valid')
        org_id = presented.org_id
        row = await self._db.get(org_id, presented.family_id)
        if row is None or row.revoked_at is not None:
            raise OAuthError.invalid_grant('this VM credential has been revoked')
        if (row.last_used_at or row.created_at) + IDLE_LIMIT < datetime.now(timezone.utc):
            await self._db.revoke(org_id, row.id, reason=IDLE, by_id=None)
            raise OAuthError.invalid_grant('this VM credential expired after 90 days unused')
        await self._still_allowed(org_id, row)
        successors = presented.successors()
        outcome = await self._db.rotate(
            org_id,
            row.id,
            generation=presented.generation,
            presented_hash=presented.secret_hash,
            successor_hashes=[candidate.secret_hash for candidate in successors],
        )
        successor = successors[0]
        if outcome == Rotation.RETRIED:
            # The one retry of a lost reply: the successor it was issued,
            # under whichever key issued it, and a fresh token.
            current = await self._db.get(org_id, row.id)
            successor = next(c for c in successors if current is not None and c.secret_hash == current.secret_hash)
        if outcome == Rotation.REUSED:
            logger.warning('VM credential %s was presented again after rotating: family revoked', row.id)
            raise OAuthError.invalid_grant('this VM credential was already used; its family is revoked')
        if outcome not in (Rotation.ROTATED, Rotation.RETRIED):
            raise OAuthError.invalid_grant('this VM credential is not valid')
        issued = await self._token_for(org_id, row)
        return issued.model_copy(update={'vm_credential': successor.render()})

    async def _still_allowed(self, org_id: str, row: VmCredential) -> None:
        """The producer may still act: revoke the family, and refuse, if not."""
        if row.producer_kind == SPACE:
            if await SpaceTokens.live(org_id, row.producer_id) is None:
                await self._db.revoke(org_id, row.id, reason=SPACE_REMOVED, by_id=None)
                raise OAuthError.invalid_grant('this space no longer exists')
            return
        if row.producer_kind == APP:
            client = await RegisteredClientDB().app(org_id, row.producer_id)
            if client is None or client.state != 'approved':
                await self._db.revoke(org_id, row.id, reason=APP_REMOVED, by_id=None)
                raise OAuthError.invalid_grant('this app is no longer registered')
            return
        try:
            await PersonInOrganisation.check(row.producer_id, org_id, issued_at=int(row.created_at.timestamp()))
        except OAuthError:
            await self._db.revoke(org_id, row.id, reason=SIGNED_OUT, by_id=None)
            raise

    async def _token_for(self, org_id: str, row: VmCredential) -> IssuedTokens:
        tokens = JWTService()
        if row.producer_kind == SPACE:
            space = await SpaceTokens.live(org_id, row.producer_id)
            if space is None:
                raise OAuthError.invalid_grant('this space no longer exists')
            token = SpaceTokens(tokens).sign(org_id, space, lifetime=TOKEN_LIFETIME)
        elif row.producer_kind == APP:
            client = await RegisteredClientDB().app(org_id, row.producer_id)
            token = tokens.sign(
                subject=row.producer_id,
                audience=tokens.issuer,
                use=TokenUse.APP,
                lifetime=TOKEN_LIFETIME,
                claims={'org': org_id, 'kind': APP, 'name': client.name if client else ''},
            )
        else:
            organisation = await OrganizationDB().get_by_id(org_id)
            token = tokens.create_token(
                row.producer_id,
                expires_delta=TOKEN_LIFETIME,
                org_id=org_id,
                org_name=str(organisation.display_name or organisation.slug or '') if organisation else None,
                group=await GroupExtensions().group_of(row.producer_id, org_id),
                incarnation=await VolumeSizes.incarnation(org_id, ProducerRef(kind=PERSON, id=row.producer_id)),
            )
        return IssuedTokens(access_token=token, expires_in=int(TOKEN_LIFETIME.total_seconds()))

    # ── Apps, and the node agent's one pickup ───────────────────────

    async def enable_app(self, org_id: str, app_id: str, *, by_user_id: str) -> VmCredentialRecord:
        """The cloud for an app: a credential waiting for the node agent. Again re-issues it."""
        if await RegisteredClientDB().app(org_id, app_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='App not found')
        row, _ = await self.issue(
            org_id, ProducerRef(kind=APP, id=app_id), by_kind=PERSON, by_id=by_user_id, for_pickup=True
        )
        return self.record(org_id, row)

    async def pick_up(self, org_id: str, producer: ProducerRef, *, by_client_id: str) -> PickedUp:
        """An app's or a space's credential, once. A person's never waits here: they sign in."""
        found = (
            None
            if producer.kind == PERSON
            else await self._db.pick_up(org_id, producer.kind, producer.id, by_client_id=by_client_id)
        )
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f'No VM credential is waiting for this {producer.kind}: '
                    'none was issued, or it was picked up already'
                ),
            )
        family_id, sealed = found
        logger.info(
            'VM credential %s for %s %s picked up by platform client %s',
            family_id,
            producer.kind,
            producer.id,
            by_client_id,
        )
        return PickedUp(credential_id=family_id, vm_credential=decrypt_secret(sealed))

    # ── The portal ──────────────────────────────────────────────────

    async def listed(self, caller: ProducerCaller) -> VmCredentialList:
        """An org admin sees the organisation's; anybody else their own."""
        org_id = caller.org_id or ''
        if caller.is_org_admin:
            rows = await self._db.active(org_id)
        else:
            rows = await self._db.active(org_id, producer_kind=caller.kind, producer_id=caller.id)
        return VmCredentialList(credentials=[self.record(org_id, row) for row in rows])

    async def revoke(self, caller: ProducerCaller, credential_id: str) -> None:
        org_id = caller.org_id or ''
        row = await self._db.get(org_id, credential_id)
        own = row is not None and (row.producer_kind, row.producer_id) == (caller.kind, caller.id)
        if row is None or not (own or caller.is_org_admin):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='VM credential not found')
        reason = REVOKED_BY_PERSON if own else REVOKED_BY_ADMIN
        await self._db.revoke(org_id, credential_id, reason=reason, by_id=caller.id)
        # An app whose credential goes is inactive: wake the long polls now.
        await FeedSignals().notify()

    async def revoke_person(self, user_id: str, *, org_ids: list[str] | None = None) -> None:
        """A person signed out, left or was deactivated: their VMs' credentials stop now."""
        for org_id in org_ids if org_ids is not None else await TenantData.orgs_of(user_id, active_only=False):
            await self._db.revoke_producer(org_id, PERSON, user_id, reason=SIGNED_OUT)

    async def revoke_app(self, org_id: str, app_id: str) -> None:
        await self._db.revoke_producer(org_id, APP, app_id, reason=APP_REMOVED)
