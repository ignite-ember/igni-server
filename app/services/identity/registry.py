"""Registering a client, and a super admin deciding about it.

A client registers itself — nobody types a secret into anything. It
generated its key pair on first start, sends the public half here with
its name and its URL (RFC 7591's shape, so a stock OAuth library can do
it), and prints the key's thumbprint in its own log. A super admin
compares that thumbprint with the one the portal shows and approves.
Until then the registration grants nothing.

Registering is unauthenticated by necessity, so it is bounded instead: a
key already registered gets its existing registration back (a service
restarting must not pile up duplicates), and at most `MAX_PENDING`
registrations may wait for a decision at once.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.db.sql.models.registered_client import APP, APPROVED, PENDING, PLATFORM, RegisteredClient, REVOKED, SERVICE
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.identity.jwk import PublicJwk
from app.services.identity.service_url import ServiceUrl

#: How many registrations may wait for a super admin at once.
MAX_PENDING = 20

#: More than a rotation needs (old and new, perhaps a third staged).
MAX_KEYS = 3


class RegistrationRefused(ValueError):
    """The registration cannot be accepted; the message says why."""


class ExtensionIdTaken(RuntimeError):
    """Approving would give two approved services one extension id."""


class RegistryFull(RuntimeError):
    """Too many registrations are already waiting for a decision."""


class ClientRegistration(BaseModel):
    """What a client sends to `POST /v1/clients` — RFC 7591 client metadata.

    `client_uri` is the client's own URL: the audience of every token a
    person gets for it, and, for a service, where igni reaches it for
    erasure and subject access. `kind` is igni's own addition: a
    `service`, or a `platform` client (the cloud's gateway and node
    agent). An app does not register here — an org admin registers it on
    the portal, in their organisation (`app.services.portal.apps`).
    """

    client_name: str = Field(min_length=1, max_length=80)
    client_uri: str = Field(max_length=2000)
    kind: Literal['service', 'platform'] = 'service'
    redirect_uris: list[str] = Field(default_factory=list, max_length=10)
    token_endpoint_auth_method: Literal['private_key_jwt'] = 'private_key_jwt'
    jwks: dict[str, list[dict[str, object]]]

    @field_validator('redirect_uris')
    @classmethod
    def _redirects(cls, value: list[str]) -> list[str]:
        return [ServiceUrl.redirect(uri) for uri in value]

    def keys(self) -> list[PublicJwk]:
        raw = self.jwks.get('keys') or []
        if not raw:
            raise RegistrationRefused('jwks must hold at least one public key')
        if len(raw) > MAX_KEYS:
            raise RegistrationRefused(f'at most {MAX_KEYS} keys per client')
        try:
            return [PublicJwk.from_mapping(dict(key)) for key in raw]
        except ValueError as exc:
            raise RegistrationRefused(str(exc)) from exc

    def url(self) -> str:
        try:
            return ServiceUrl.own_origin(self.client_uri)
        except ValueError as exc:
            raise RegistrationRefused(str(exc)) from exc


class ClientRegistry:
    """The registrations, and the decisions about them."""

    def __init__(self, store: RegisteredClientDB | None = None) -> None:
        self._store = store or RegisteredClientDB()
        self._extensions = ServiceExtensionDB()

    async def register(self, registration: ClientRegistration) -> RegisteredClient:
        keys = registration.keys()
        url = registration.url()
        thumbprint = keys[0].thumbprint()

        existing = await self._store.by_thumbprint(thumbprint)
        if existing is not None:
            # The same key again: a restart, or a retry. Its decision
            # stands — re-registering cannot un-revoke a client.
            return existing

        if await self._store.pending_count() >= MAX_PENDING:
            raise RegistryFull(f'{MAX_PENDING} registrations are already waiting for a super admin')

        return await self._store.add(
            RegisteredClient(
                kind=registration.kind,
                name=registration.client_name.strip(),
                base_url=url,
                redirect_uris=registration.redirect_uris,
                jwks={'keys': [key.as_dict() for key in keys]},
                thumbprint=thumbprint,
                state=PENDING,
            )
        )

    async def approve(self, client_id: str, *, by_user_id: str | None) -> RegisteredClient | None:
        """Approve, unless its extension's id is already another approved service's.

        Two approved services shipping `igni.artifacts` would put two
        different bundles on people's machines under one name.
        """
        extension = await self._extensions.of(client_id)
        if extension is not None:
            owner = await self._extensions.approved_owner_of(extension.extension_id)
            if owner is not None and owner != client_id:
                raise ExtensionIdTaken(f'another approved service already ships {extension.extension_id}')
        return await self._store.decide(client_id, state=APPROVED, by_user_id=by_user_id)

    async def revoke(self, client_id: str, *, by_user_id: str | None) -> RegisteredClient | None:
        return await self._store.decide(client_id, state=REVOKED, by_user_id=by_user_id)

    async def remove(self, client_id: str) -> bool:
        return await self._store.remove(client_id)

    async def all(self) -> list[RegisteredClient]:
        return await self._store.all()

    async def approved(self, client_id: str, *, kind: str = SERVICE) -> RegisteredClient | None:
        """The client, if it exists, is of this kind and is approved."""
        client = await self._store.get(client_id)
        return client if client is not None and client.state == APPROVED and client.kind == kind else None


__all__ = [
    'APP',
    'PLATFORM',
    'SERVICE',
    'ClientRegistration',
    'ClientRegistry',
    'ExtensionIdTaken',
    'RegistrationRefused',
    'RegistryFull',
]
