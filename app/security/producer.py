"""Who is calling, when the caller is a producer: a person, an app, or a space.

The cloud has three kinds of producer (`docs/service-identity.md` §11,
§17). **People**, each in exactly one group, present their igni session.
**Apps**, which an org admin registered, present an `app` token they
obtained as themselves (`client_credentials` with `private_key_jwt`); an
app belongs to one organisation, is in no group and never acts for a
person. **Spaces** are an app's end users, each a producer of its own,
owned by the app; they present a `space` token, from the app's token
exchange or their VM's credential.

Routes a producer may call — the extension listing, the commands in its
own VM — take any of them, and get a `ProducerCaller` saying which.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.db.sql.models.app_space import SPACE
from app.db.sql.models.registered_client import APP
from app.middlewares.request_audit import RequestAudit
from app.security.portal_auth import get_current_account
from app.services.auth.jwt_service import JWTService, PERSON, TokenUse
from app.services.identity.registry import ClientRegistry
from app.services.identity.space_tokens import SpaceTokens

_bearer = OAuth2PasswordBearer(tokenUrl='/v1/oauth/token', auto_error=False)

ProducerKind = Literal['person', 'app', 'space']


class ProducerRef(BaseModel):
    """One producer of one organisation — and so one VM in the cloud."""

    kind: ProducerKind
    id: str


class ProducerCaller(BaseModel):
    """The producer making this request."""

    kind: ProducerKind
    id: str
    #: Null only for a person in no organisation yet.
    org_id: str | None
    #: A person's role in `org_id` (`admin`/`member`); null for an app.
    org_role: str | None = None
    is_super_admin: bool = False
    #: An app's registered name, a space's external id, or the person's address.
    name: str = ''
    #: A space's app.
    app_id: str | None = None

    @property
    def ref(self) -> ProducerRef:
        return ProducerRef(kind=self.kind, id=self.id)

    @property
    def is_org_admin(self) -> bool:
        return self.kind == PERSON and self.org_role == 'admin'


class Producers:
    """Resolves the bearer token to a producer."""

    async def app(self, request: Request, token: str) -> ProducerCaller | None:
        """The app this token is for, or None when it is not an app token."""
        tokens = JWTService()
        payload = tokens.verify(token, audience=tokens.issuer, use=TokenUse.APP)
        if payload is None:
            return None
        client = await ClientRegistry().approved(payload.sub, kind=APP)
        if client is None or client.org_id != payload.org:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='This app is no longer registered',
                headers={'WWW-Authenticate': 'Bearer'},
            )
        RequestAudit.of(request).attribute_to(user_id=f'app:{client.client_id}', org_id=str(client.org_id))
        return ProducerCaller(kind=APP, id=client.client_id, org_id=str(client.org_id), name=client.name)

    async def space(self, request: Request, token: str) -> ProducerCaller | None:
        """The space this token is for, or None when it is not a space token (§17)."""
        tokens = JWTService()
        payload = tokens.verify(token, audience=tokens.issuer, use=TokenUse.SPACE)
        if payload is None:
            return None
        org_id = payload.org or ''
        space = await SpaceTokens.live(org_id, payload.sub, app_id=payload.app or '')
        if space is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='This space no longer exists',
                headers={'WWW-Authenticate': 'Bearer'},
            )
        RequestAudit.of(request).attribute_to(user_id=f'space:{space.id}', org_id=org_id)
        return ProducerCaller(kind=SPACE, id=space.id, org_id=org_id, name=space.external_id, app_id=space.app_id)

    async def machine(self, request: Request, token: str) -> ProducerCaller | None:
        """The app or space this token is for; None for anything else — a person's session."""
        return await self.app(request, token) or await self.space(request, token)

    async def resolve(self, request: Request, token: str | None) -> ProducerCaller:
        app = await self.app(request, token or '')
        if app is not None:
            return app
        space = await self.space(request, token or '')
        if space is not None:
            return space
        account = await get_current_account(request, token)
        return ProducerCaller(
            kind=PERSON,
            id=account.user_id,
            org_id=account.org_id,
            org_role=account.org_role,
            is_super_admin=account.is_super_admin,
            name=account.email or '',
        )


async def current_producer(request: Request, token: str | None = Depends(_bearer)) -> ProducerCaller:
    """A person (with or without an organisation) or an app."""
    return await Producers().resolve(request, token)


async def require_producer(caller: ProducerCaller = Depends(current_producer)) -> ProducerCaller:
    """A person in an organisation, or an app."""
    if caller.org_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You are not in an organisation yet')
    return caller
