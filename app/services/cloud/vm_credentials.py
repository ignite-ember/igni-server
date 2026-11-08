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

import logging
import secrets

from fastapi import HTTPException, status

from app.db.sql.models.registered_client import APP
from app.db.sql.models.vm_credential import (
    APP_REMOVED,
    IGNI_VM,
    REPLACED,
    REVOKED_BY_ADMIN,
    REVOKED_BY_PERSON,
    SIGNED_OUT,
    VmCredential,
)
from app.db.sql.organizations import OrganizationDB
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.revocations import FeedSignals
from app.db.sql.vm_credentials import VmCredentialsDB
from app.security.producer import ProducerCaller, ProducerRef
from app.services.auth.jwt_service import PERSON
from app.services.cloud.vm_credential_records import IssuedVmCredential, PickedUp, VmCredentialList, VmCredentialRecord
from app.services.cloud.vm_credential_string import VmCredentialString
from app.services.secrets.cipher import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


class VmCredentials:
    def __init__(
        self,
        *,
        db: VmCredentialsDB | None = None,
        organizations: OrganizationDB | None = None,
        clients: RegisteredClientDB | None = None,
        signals: FeedSignals | None = None,
    ) -> None:
        self._db = db or VmCredentialsDB()
        self._organizations = organizations or OrganizationDB()
        self._clients = clients or RegisteredClientDB()
        self._signals = signals or FeedSignals()

    async def issue(
        self, org_id: str, producer: ProducerRef, *, by_kind: str, by_id: str, for_pickup: bool
    ) -> IssuedVmCredential:
        """A new family for the producer, replacing its working one."""
        family_id = secrets.token_hex(16)
        credential = VmCredentialString.fresh(org_id, family_id)
        row = VmCredential(
            id=family_id,
            producer_kind=producer.kind,
            producer_id=producer.id,
            audience=IGNI_VM,
            generation=credential.generation,
            secret_hash=credential.secret_hash,
            previous_hashes=[],
            pending_pickup=encrypt_secret(credential.render()) if for_pickup else None,
            created_by_kind=by_kind,
            created_by_id=by_id,
        )
        stored = await self._db.issue(org_id, row, by_id=by_id, replaced_reason=REPLACED)
        logger.info('Issued VM credential %s for %s:%s in %s', family_id, producer.kind, producer.id, org_id)
        return IssuedVmCredential(record=VmCredentialRecord.of(org_id, stored), vm_credential=credential.render())

    # ── Apps, and the node agent's one pickup ───────────────────────

    async def enable_app(self, org_id: str, app_id: str, *, by_user_id: str) -> VmCredentialRecord:
        """The cloud for an app: a credential waiting for the node agent. Again re-issues it."""
        if await self._clients.app(org_id, app_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='App not found')
        issued = await self.issue(
            org_id, ProducerRef(kind=APP, id=app_id), by_kind=PERSON, by_id=by_user_id, for_pickup=True
        )
        return issued.record

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
        logger.info(
            'VM credential %s for %s %s picked up by platform client %s',
            found.family_id,
            producer.kind,
            producer.id,
            by_client_id,
        )
        return PickedUp(credential_id=found.family_id, vm_credential=decrypt_secret(found.sealed))

    # ── The portal ──────────────────────────────────────────────────

    async def listed(self, caller: ProducerCaller) -> VmCredentialList:
        """An org admin sees the organisation's; anybody else their own."""
        org_id = caller.org_id or ''
        if caller.is_org_admin:
            rows = await self._db.active(org_id)
        else:
            rows = await self._db.active(org_id, producer_kind=caller.kind, producer_id=caller.id)
        return VmCredentialList(credentials=[VmCredentialRecord.of(org_id, row) for row in rows])

    async def revoke(self, caller: ProducerCaller, credential_id: str) -> None:
        org_id = caller.org_id or ''
        row = await self._db.get(org_id, credential_id)
        own = row is not None and (row.producer_kind, row.producer_id) == (caller.kind, caller.id)
        if row is None or not (own or caller.is_org_admin):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='VM credential not found')
        reason = REVOKED_BY_PERSON if own else REVOKED_BY_ADMIN
        await self._db.revoke(org_id, credential_id, reason=reason, by_id=caller.id)
        # An app whose credential goes is inactive: wake the long polls now.
        await self._signals.notify()

    async def revoke_person(self, user_id: str, *, org_ids: list[str] | None = None) -> None:
        """A person signed out, left or was deactivated: their VMs' credentials stop now."""
        for org_id in (
            org_ids if org_ids is not None else await self._organizations.member_org_ids(user_id, active_only=False)
        ):
            await self._db.revoke_producer(org_id, PERSON, user_id, reason=SIGNED_OUT)

    async def revoke_app(self, org_id: str, app_id: str) -> None:
        await self._db.revoke_producer(org_id, APP, app_id, reason=APP_REMOVED)
