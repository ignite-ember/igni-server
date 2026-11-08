"""igni applies a group pack only if this server signed it.

igni caches the pack on the developer's machine and applies it at the
next start. That cache is a file the user can write, so an unsigned pack
could be edited, swapped for somebody else's, or deleted, and nothing
could tell. `GET /v1/portal/me/group/pack` now carries `signed_pack`: the
same content as a compact JWS, under the key igni signs its tokens with
and publishes at `/.well-known/jwks.json`.

These tests pin what the client relies on:

* the existing fields are still served, unchanged;
* `signed_pack` verifies against the published JWKS, names this server
  (`iss`), the person (`sub`) and the organisation (`org`), and carries
  a monotonic `version` and no expiry;
* the ETag is bound to the person, so a 304 never leaves somebody holding
  a pack signed for somebody else;
* a pack is never accepted where an igni token is expected.
"""

from __future__ import annotations

import base64
import json
import uuid

import jwt
import pytest

from app.config import settings
from app.services.auth.jwt_service import JWTService
from app.services.portal.default_group import DefaultGroup
from app.services.portal.group_pack_claims import PACK_USE
from app.services.portal.group_pack_service import GroupPackService
from app.services.portal.group_pack_signature import PACK_TYPE
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

pytestmark = pytest.mark.anyio


@pytest.fixture
async def two_members_in_a_group(client, db):
    suffix = uuid.uuid4().hex[:8]
    admin_id, alice, bob = f'admin-{suffix}', f'alice-{suffix}', f'bob-{suffix}'
    for user_id in (admin_id, alice, bob):
        await create_user(db, user_id=user_id, email=f'{user_id}@example.invalid', full_name=user_id)
    org = await create_org(db, owner_user_id=admin_id, slug=f'signed-{suffix}', display_name='Signed')
    org_id = str(org.id)
    for user_id in (alice, bob):
        await add_member(db, org_id=org_id, user_id=user_id, role='member')
    admin_headers = {**auth_header(make_token(admin_id, org_id=org_id)), 'X-Org-Id': org_id}

    created = await client.post('/portal/groups', json={'name': f'Signed {suffix}'}, headers=admin_headers)
    assert created.status_code == 201, created.text
    group_id = created.json()['id']
    for user_id in (alice, bob):
        added = await client.post(
            f'/portal/groups/{group_id}/members', json={'user_id': user_id}, headers=admin_headers
        )
        assert added.status_code in (200, 201), added.text

    entry = await client.post(
        f'/portal/groups/{group_id}/entries',
        json={
            'kind': 'settings',
            'entry_name': 'no-web',
            'content': '{"permissions": {"web_fetch": "deny"}}',
            'content_type': 'json',
        },
        headers=admin_headers,
    )
    assert entry.status_code in (200, 201), entry.text
    return {'org_id': org_id, 'group_id': group_id, 'alice': alice, 'bob': bob}


async def _pack(client, user_id: str, etag: str | None = None):
    headers = auth_header(make_token(user_id))
    if etag:
        headers['If-None-Match'] = etag
    return await client.get('/portal/me/group/pack', headers=headers)


def _segment(token: str, index: int) -> dict:
    raw = token.split('.')[index]
    return json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))


class TestThePackIsSigned:
    async def test_it_verifies_against_the_published_jwks(self, client, two_members_in_a_group):
        response = await _pack(client, two_members_in_a_group['alice'])
        assert response.status_code == 200, response.text
        signed = response.json()['signed_pack']

        header = _segment(signed, 0)
        assert header['alg'] == 'RS256'
        assert header['typ'] == PACK_TYPE
        published = (await client.get('http://test/.well-known/jwks.json')).json()
        key = next(k for k in published['keys'] if k['kid'] == header['kid'])

        claims = jwt.decode(signed, jwt.PyJWK(key), algorithms=['RS256'], options={'verify_aud': False})
        assert claims['iss'] == settings.public_base_url.rstrip('/')
        assert claims['sub'] == two_members_in_a_group['alice']
        assert claims['org'] == two_members_in_a_group['org_id']
        assert claims['token_use'] == PACK_USE
        assert isinstance(claims['iat'], int)
        assert isinstance(claims['version'], int) and claims['version'] >= claims['iat'] * 1000
        assert 'exp' not in claims, 'a pack must keep working offline'
        assert 'aud' not in claims

    async def test_it_carries_the_pack_exactly_as_served(self, client, two_members_in_a_group):
        body = (await _pack(client, two_members_in_a_group['alice'])).json()
        signed = body.pop('signed_pack')
        assert _segment(signed, 1)['policy'] == body
        # The fields an older client reads are all still there.
        assert body['group_id'] == two_members_in_a_group['group_id']
        assert any(e['entry_name'] == 'no-web' for e in body['entries'])

    async def test_a_forged_signature_does_not_verify(self, client, two_members_in_a_group):
        signed = (await _pack(client, two_members_in_a_group['alice'])).json()['signed_pack']
        published = (await client.get('http://test/.well-known/jwks.json')).json()
        key = next(k for k in published['keys'] if k['kid'] == _segment(signed, 0)['kid'])
        head, payload, signature = signed.split('.')
        edited = _segment(signed, 1)
        edited['policy']['entries'] = []
        forged_payload = base64.urlsafe_b64encode(json.dumps(edited).encode()).rstrip(b'=').decode()
        with pytest.raises(Exception):
            jwt.decode(f'{head}.{forged_payload}.{signature}', jwt.PyJWK(key), algorithms=['RS256'])

    async def test_a_later_pack_has_a_later_version(self, client, two_members_in_a_group):
        first = _segment((await _pack(client, two_members_in_a_group['alice'])).json()['signed_pack'], 1)
        second = _segment((await _pack(client, two_members_in_a_group['alice'])).json()['signed_pack'], 1)
        assert second['version'] >= first['version']


class TestThePackIsBoundToItsPerson:
    async def test_two_people_in_one_group_get_different_tags(self, client, two_members_in_a_group):
        alice = await _pack(client, two_members_in_a_group['alice'])
        bob = await _pack(client, two_members_in_a_group['bob'])
        assert _segment(alice.json()['signed_pack'], 1)['sub'] == two_members_in_a_group['alice']
        assert _segment(bob.json()['signed_pack'], 1)['sub'] == two_members_in_a_group['bob']
        assert alice.headers['etag'] != bob.headers['etag']

    async def test_a_tag_revalidates_only_for_its_own_person(self, client, two_members_in_a_group):
        etag = (await _pack(client, two_members_in_a_group['alice'])).headers['etag']
        assert (await _pack(client, two_members_in_a_group['alice'], etag)).status_code == 304
        # Bob, holding Alice's tag (same machine, different sign-in), is
        # sent his own signed pack rather than told Alice's is current.
        assert (await _pack(client, two_members_in_a_group['bob'], etag)).status_code == 200


class TestAPackIsNotAToken:
    async def test_a_pack_is_refused_as_a_session(self, client, two_members_in_a_group):
        signed = (await _pack(client, two_members_in_a_group['alice'])).json()['signed_pack']
        assert JWTService().decode_token(signed).user_id is None
        refused = await client.get('/portal/me/group/pack', headers=auth_header(signed))
        assert refused.status_code == 401


class TestNoPackIsSignedToo:
    """A person with no pack gets that answer signed, so igni can clear its cache."""

    async def test_an_account_in_no_organisation_gets_a_signed_absence(self, client, db):
        user_id = f'nomad-{uuid.uuid4().hex[:8]}'
        await create_user(db, user_id=user_id, email=f'{user_id}@example.invalid')

        response = await _pack(client, user_id)

        assert response.status_code == 200
        body = response.json()
        assert body['no_pack'] is True
        published = (await client.get('http://test/.well-known/jwks.json')).json()
        key = next(k for k in published['keys'] if k['kid'] == _segment(body['signed_pack'], 0)['kid'])
        claims = jwt.decode(body['signed_pack'], jwt.PyJWK(key), algorithms=['RS256'], options={'verify_aud': False})
        assert claims['sub'] == user_id
        assert claims['no_pack'] is True and claims['policy'] is None
        assert claims['token_use'] == PACK_USE
        assert isinstance(claims['version'], int)

    async def test_the_absence_revalidates(self, client, db):
        user_id = f'nomad-{uuid.uuid4().hex[:8]}'
        await create_user(db, user_id=user_id, email=f'{user_id}@example.invalid')

        etag = (await _pack(client, user_id)).headers['etag']

        assert (await _pack(client, user_id, etag)).status_code == 304


class TestThePackIsTheSessionsOrganisations:
    """The pack is chosen in the organisation the session is in.

    It used to be "the person's group in whichever of their organisations
    comes first". The database now keeps a person in one organisation at a
    time (migration 0022), so two *current* memberships cannot be set up
    here; what can be pinned is that the lookup is scoped — the pack names
    the session's organisation, and asking in any other one finds nothing,
    even where the person still has a group row left behind.
    """

    async def test_the_signed_pack_names_the_sessions_organisation(self, client, two_members_in_a_group):
        token = make_token(two_members_in_a_group['alice'], org_id=two_members_in_a_group['org_id'])

        body = (await client.get('/portal/me/group/pack', headers=auth_header(token))).json()

        assert body['group_id'] == two_members_in_a_group['group_id']
        assert _segment(body['signed_pack'], 1)['org'] == two_members_in_a_group['org_id']

    async def test_another_organisation_yields_no_group(self, two_members_in_a_group):

        alice = two_members_in_a_group['alice']
        elsewhere = str(uuid.uuid4())

        assert await DefaultGroup().ensure_for_user(alice, elsewhere) is None
        assert await GroupPackService().get_my_group_pack(user_id=alice, org_id=elsewhere) is None
        mine = await GroupPackService().get_my_group_pack(user_id=alice, org_id=two_members_in_a_group['org_id'])
        assert mine is not None and mine.group_id == two_members_in_a_group['group_id']
