"""An organisation as the cloud tests play one: people, groups, apps, a platform client.

Everything goes through the HTTP API the way the portal, an app, a
service and the gateway use it; only creating accounts and approving a
registered client stand in for steps outside igni server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine

from tests.conftest import add_member, auth_header, create_org, create_user, make_token, TEST_DB_URL_ASYNC
from tests.registered_service import ExtensionBundle, RegisteredService


async def shipped(
    client: AsyncClient, extension_id: str, *, permissions: dict[str, object] | None = None
) -> RegisteredService:
    """An approved service that has uploaded its extension, declaring `permissions` if given."""
    service = RegisteredService(name=extension_id, base_url=f'https://{extension_id.split(".")[-1]}.example.com')
    await service.approve()
    manifest: dict[str, object] = {'id': extension_id, 'version': '1.0.0', 'name': extension_id}
    if permissions is not None:
        manifest['permissions'] = permissions
    bundle = ExtensionBundle.of(manifest)
    response = await client.put(
        f'/clients/{service.client_id}/extension',
        content=bundle.archive,
        headers={
            'Content-Type': 'application/gzip',
            'X-Igni-Bundle-Signature': bundle.signed_by(service, extension=extension_id, version='1.0.0'),
        },
    )
    assert response.status_code == 200, response.text
    return service


async def token_for(client: AsyncClient, registered: RegisteredService, **fields: str) -> dict[str, str]:
    """Headers carrying a `client_credentials` token for a registered client."""
    response = await client.post('/oauth/token', data=registered.authenticated('client_credentials', **fields))
    assert response.status_code == 200, response.text
    return auth_header(response.json()['access_token'])


async def platform(client: AsyncClient) -> dict[str, str]:
    """Headers for an approved platform client — the gateway or the node agent."""
    gateway = RegisteredService(kind='platform', name='Gateway', base_url='https://gateway.example.com')
    await gateway.approve()
    return await token_for(client, gateway)


@asynccontextmanager
async def shared_plane() -> AsyncIterator[AsyncSession]:
    """A session on the shared plane, opened on whichever loop is running now.

    Not the `db` fixture: a fixture's session belongs to the loop that
    made it, and these helpers are called from fixtures and test bodies.
    """
    engine = create_async_engine(TEST_DB_URL_ASYNC)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def make_super_admin(user_id: str) -> None:
    async with shared_plane() as session:
        await session.execute(text('UPDATE users SET is_super_admin = true WHERE user_id = :u'), {'u': user_id})
        await session.commit()


class CloudOrg:
    """One organisation, with an admin."""

    def __init__(self, client: AsyncClient, slug: str = 'acme') -> None:
        self.client = client
        self.slug = slug
        self.org_id = ''
        self.admin_id = ''
        self.admin: dict[str, str] = {}

    async def create(self) -> CloudOrg:
        async with shared_plane() as db:
            owner = await create_user(db, email=f'admin@{self.slug}.com')
            org = await create_org(db, owner_user_id=str(owner.user_id), slug=self.slug, display_name=self.slug)
        self.org_id = str(org.id)
        self.admin_id = str(owner.user_id)
        self.admin = auth_header(make_token(self.admin_id, org_id=self.org_id))
        return self

    async def group(self, name: str) -> str:
        response = await self.client.post('/portal/groups', json={'name': name}, headers=self.admin)
        assert response.status_code == 201, response.text
        return str(response.json()['id'])

    async def person(self, email: str, group_id: str | None = None) -> tuple[str, dict[str, str]]:
        async with shared_plane() as db:
            user = await create_user(db, email=email)
            user_id = str(user.user_id)
            await add_member(db, self.org_id, user_id)
        if group_id is not None:
            response = await self.client.post(
                f'/portal/groups/{group_id}/members', json={'user_id': user_id}, headers=self.admin
            )
            assert response.status_code == 200, response.text
        return user_id, auth_header(make_token(user_id, org_id=self.org_id))

    async def app(self, name: str = 'Review bot') -> RegisteredService:
        """An app registered by the org admin on the portal, holding its own key pair."""
        app = RegisteredService(kind='app', name=name, base_url='', org_id=self.org_id)
        response = await self.client.post(
            '/portal/apps',
            json={'name': name, 'owner_user_id': self.admin_id, 'jwks': {'keys': [app.public.as_dict()]}},
            headers=self.admin,
        )
        assert response.status_code == 201, response.text
        app.client_id = str(response.json()['app_id'])
        return app

    async def app_token(self, app: RegisteredService) -> dict[str, str]:
        return await token_for(self.client, app)

    async def switch_group(self, group_id: str, extension_id: str, enabled: bool = True) -> None:
        response = await self.client.put(
            f'/portal/groups/{group_id}/extensions/{extension_id}', json={'enabled': enabled}, headers=self.admin
        )
        assert response.status_code == 204, response.text

    async def switch_app(self, app_id: str, extension_id: str, enabled: bool = True) -> None:
        response = await self.client.put(
            f'/portal/apps/{app_id}/extensions/{extension_id}', json={'enabled': enabled}, headers=self.admin
        )
        assert response.status_code == 204, response.text
