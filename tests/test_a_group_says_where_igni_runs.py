"""A group says where igni runs for its people: only on their computers, only in the cloud, or both.

`org_groups.mode` is `local`, `cloud` or `local_and_cloud` (every group
before it was the last). The admin edits it with the group PATCH — on the
Default group too, which takes nothing else — and the change is audited
with what it was. It rides the signed pack after `theme`, so the desktop
enforces it offline; igni server enforces `local` itself: the feed lists
those people inactive with the reason, and their VM can neither sign in
nor renew (igni-core `docs/design/local-cloud-switch.md` §6).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.fernet import Fernet
import jwt
import pytest
from sqlalchemy import select, text

from app.api.base_v1.schema.portal.group_pack import OrgGroupPack
from app.config import settings
from app.db.sql.models.producer_feed import TenantProducerFeedMark
from app.db.sql.models.vm_credential import VmCredential
from app.db.sql.tenancy import TenantSessions
from app.services.cloud.vm_credential_grants import VM_CREDENTIAL, VM_SIGN_IN
from app.services.portal.group_pack_claims import PACK_USE
from app.services.portal.group_pack_signature import PACK_TYPE
from tests.cloud_world import CloudOrg, platform, shared_plane
from tests.conftest import audit_rows, make_token

pytestmark = pytest.mark.anyio

PACK_FIXTURE = Path(__file__).parent / 'fixtures' / 'contracts' / 'group-pack-with-mode.json'


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def _patch(client, acme: CloudOrg, group_id: str, **body: object):
    return await client.patch(f'/portal/groups/{group_id}', json=body, headers=acme.admin)


async def _default_group(client, acme: CloudOrg) -> dict:
    groups = (await client.get('/portal/groups', headers=acme.admin)).json()['groups']
    return next(group for group in groups if group['is_default'])


def _segment(token: str, index: int) -> dict:
    raw = token.split('.')[index]
    return dict(json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))))


class TestTheSetting:
    async def test_a_new_group_uses_both(self, client, acme):
        legal = await acme.group('Legal')

        read = (await client.get(f'/portal/groups/{legal}', headers=acme.admin)).json()

        assert read['mode'] == 'local_and_cloud'

    async def test_the_admin_sets_it(self, client, acme):
        legal = await acme.group('Legal')

        changed = await _patch(client, acme, legal, mode='cloud')
        read = (await client.get(f'/portal/groups/{legal}', headers=acme.admin)).json()
        listed = (await client.get('/portal/groups', headers=acme.admin)).json()['groups']

        assert changed.status_code == 200, changed.text
        assert changed.json()['mode'] == 'cloud'
        assert read['mode'] == 'cloud'
        assert [g['mode'] for g in listed if g['id'] == legal] == ['cloud']

    async def test_another_edit_leaves_it_alone(self, client, acme):
        legal = await acme.group('Legal')
        await _patch(client, acme, legal, mode='local')

        renamed = await _patch(client, acme, legal, name='Legal team')

        assert renamed.json()['mode'] == 'local'

    async def test_an_unknown_mode_is_refused(self, client, acme):
        legal = await acme.group('Legal')

        assert (await _patch(client, acme, legal, mode='hybrid')).status_code == 422
        assert (await _patch(client, acme, legal, mode=None)).status_code == 200
        assert (await client.get(f'/portal/groups/{legal}', headers=acme.admin)).json()['mode'] == 'local_and_cloud'

    async def test_a_member_cannot_set_it(self, client, acme):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)

        assert (
            await client.patch(f'/portal/groups/{legal}', json={'mode': 'local'}, headers=session)
        ).status_code == 403

    async def test_the_database_holds_it_to_the_three(self, client, acme):
        legal = await acme.group('Legal')

        with pytest.raises(Exception, match='ck_org_groups_mode'):
            async with TenantSessions().session_scope(acme.org_id) as session:
                await session.execute(text("UPDATE org_groups SET mode = 'both' WHERE id = :g"), {'g': legal})

    async def test_a_copy_runs_where_its_source_does(self, client, acme):
        legal = await acme.group('Legal')
        await _patch(client, acme, legal, mode='cloud')

        copy = await client.post(f'/portal/groups/{legal}/copy', json={'name': 'Legal 2'}, headers=acme.admin)

        assert copy.status_code == 201, copy.text
        assert copy.json()['mode'] == 'cloud'


class TestTheDefaultGroup:
    async def test_takes_a_mode(self, client, acme):
        default = await _default_group(client, acme)

        changed = await _patch(client, acme, default['id'], mode='local')

        assert changed.status_code == 200, changed.text
        assert changed.json()['mode'] == 'local'

    async def test_takes_nothing_else_with_it(self, client, acme):
        default = await _default_group(client, acme)

        refused = await _patch(client, acme, default['id'], mode='local', name='Everyone')
        alone = await _patch(client, acme, default['id'], name='Everyone')

        assert refused.status_code == 409
        assert alone.status_code == 409
        assert (await _default_group(client, acme))['mode'] == 'local_and_cloud'

    async def test_an_empty_edit_is_still_refused(self, client, acme):
        default = await _default_group(client, acme)

        assert (await _patch(client, acme, default['id'])).status_code == 409


class TestTheAudit:
    async def test_a_change_names_what_it_was(self, client, acme):
        legal = await acme.group('Legal')

        await _patch(client, acme, legal, mode='local')
        await _patch(client, acme, legal, mode='local')
        await _patch(client, acme, legal, mode='cloud')
        await _patch(client, acme, legal, name='Legal team')

        rows = [r for r in audit_rows(actor_user_id=acme.admin_id) if r['method'] == 'PATCH']
        assert [r['detail'] for r in reversed(rows)] == [
            {'mode': {'from': 'local_and_cloud', 'to': 'local'}},
            None,
            {'mode': {'from': 'local', 'to': 'cloud'}},
            None,
        ]


class TestThePack:
    async def test_carries_the_mode_after_the_theme(self, client, acme):
        legal = await acme.group('Legal')
        ada, session = await acme.person('ada@acme.com', legal)
        await _patch(client, acme, legal, mode='cloud')

        body = (await client.get('/portal/me/group/pack', headers=session)).json()
        signed = body.pop('signed_pack')
        published = (await client.get('http://test/.well-known/jwks.json')).json()
        key = next(k for k in published['keys'] if k['kid'] == _segment(signed, 0)['kid'])
        claims = jwt.decode(signed, jwt.PyJWK(key), algorithms=['RS256'], options={'verify_aud': False})

        assert body['mode'] == 'cloud'
        assert list(claims['policy']) == [
            'group_id',
            'group_name',
            'fetched_at',
            'entries',
            'default_model',
            'theme',
            'mode',
        ]
        assert claims['policy'] == body
        assert claims['sub'] == ada

    async def test_a_change_of_mode_is_a_new_pack(self, client, acme):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        etag = (await client.get('/portal/me/group/pack', headers=session)).headers['etag']
        assert (
            await client.get('/portal/me/group/pack', headers={**session, 'If-None-Match': etag})
        ).status_code == 304

        await _patch(client, acme, legal, mode='local')
        again = await client.get('/portal/me/group/pack', headers={**session, 'If-None-Match': etag})

        assert again.status_code == 200
        assert again.json()['mode'] == 'local'


class TestTheCrossRepoFixture:
    """`tests/fixtures/contracts/group-pack-with-mode.json`: a pack this server's signer made, for igni-core to verify.

    Made with a key thrown away afterwards; only its public JWKS is kept.
    It must stay what the server signs today, field order included, or
    igni-core would be checking a pack nobody sends.
    """

    @pytest.fixture
    def fixture(self) -> dict:
        return dict(json.loads(PACK_FIXTURE.read_text()))

    def test_it_verifies_under_its_own_key_and_names_who_it_is_for(self, fixture):
        [key] = fixture['jwks']['keys']

        claims = jwt.decode(
            fixture['signed_pack'], jwt.PyJWK(key), algorithms=['RS256'], options={'verify_aud': False}
        )

        assert _segment(fixture['signed_pack'], 0)['typ'] == PACK_TYPE
        assert (claims['iss'], claims['sub'], claims['org'], claims['token_use']) == (
            fixture['issuer'],
            fixture['subject'],
            fixture['org'],
            PACK_USE,
        )
        assert 'exp' not in claims
        assert claims['policy']['mode'] == 'cloud'

    def test_its_pack_is_the_one_the_server_serialises_today(self, fixture):
        policy = _segment(fixture['signed_pack'], 1)['policy']

        assert OrgGroupPack.model_validate(policy).as_payload() == policy
        assert list(OrgGroupPack.model_validate(policy).as_payload()) == list(policy)
        assert list(policy)[-2:] == ['theme', 'mode']


class Feed:
    def __init__(self, client, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self.cursor: str | None = None

    async def poll(self) -> list[dict]:
        params = {'since': self.cursor} if self.cursor is not None else {}
        response = await self.client.get('/platform/producers', params=params, headers=self.headers)
        assert response.status_code == 200, response.text
        self.cursor = response.json()['cursor']
        return list(response.json()['producers'])


def _people(rows: list[dict]) -> dict[str, tuple[bool, str | None, bool]]:
    return {
        row['producer']['id']: (row['active'], row['inactive_reason'], row['removed'])
        for row in rows
        if row['producer']['kind'] == 'person'
    }


class TestTheFeed:
    async def test_a_local_only_group_is_listed_inactive_with_the_reason(self, client, acme):
        feed = Feed(client, await platform(client))
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        max_id, _ = await acme.person('max@acme.com')
        assert _people(await feed.poll())[ada] == (True, None, False)

        await _patch(client, acme, legal, mode='local')
        changed = _people(await feed.poll())

        assert changed == {ada: (False, 'group_local_only', False)}
        assert max_id not in changed

    async def test_moving_it_back_makes_them_active_again(self, client, acme):
        feed = Feed(client, await platform(client))
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await _patch(client, acme, legal, mode='local')
        await feed.poll()

        await _patch(client, acme, legal, mode='local_and_cloud')

        assert _people(await feed.poll()) == {ada: (True, None, False)}

    async def test_cloud_only_keeps_them_active(self, client, acme):
        feed = Feed(client, await platform(client))
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await feed.poll()

        await _patch(client, acme, legal, mode='cloud')

        assert ada not in _people(await feed.poll())

    async def test_a_deactivated_member_says_no_reason(self, client, acme):
        feed = Feed(client, await platform(client))
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await _patch(client, acme, legal, mode='local')
        await feed.poll()
        async with shared_plane() as session:
            await session.execute(
                text("UPDATE org_memberships SET status = 'deactivated' WHERE user_id = :u"), {'u': ada}
            )
            await session.execute(text("SELECT producer_feed_mark(:o, 'person', :u)"), {'o': acme.org_id, 'u': ada})
            await session.commit()

        assert _people(await feed.poll()) == {ada: (False, None, False)}

    async def test_going_local_stops_their_vms_tokens(self, client, acme):
        gateway = await platform(client)
        feed = Feed(client, gateway)
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await feed.poll()
        cursor = (await client.get('/platform/revocations', headers=gateway)).json()['cursor']

        await _patch(client, acme, legal, mode='local')
        await feed.poll()
        revoked = (await client.get('/platform/revocations', params={'since': cursor}, headers=gateway)).json()

        assert [(r['subject_id'], r['reason']) for r in revoked['revocations']] == [(ada, 'inactive')]

    async def test_the_trigger_marks_the_groups_people_on_a_change_of_mode(self, client, acme):
        legal = await acme.group('Legal')
        async with TenantSessions().session_scope(acme.org_id) as session:
            await session.execute(text('DELETE FROM producer_feed_marks'))

        async with TenantSessions().session_scope(acme.org_id) as session:
            await session.execute(text("UPDATE org_groups SET mode = 'local' WHERE id = :g"), {'g': legal})
        async with TenantSessions().session_scope(acme.org_id) as session:
            marks = (
                await session.execute(select(TenantProducerFeedMark.producer_kind, TenantProducerFeedMark.producer_id))
            ).all()

        assert [tuple(mark) for mark in marks] == [('person', '*')]


async def _sign_in(client, session: str):
    return await client.post('/oauth/token', data={'grant_type': VM_SIGN_IN, 'subject_token': session})


async def _exchange(client, credential: str):
    return await client.post('/oauth/token', data={'grant_type': VM_CREDENTIAL, 'vm_credential': credential})


class TestTheVmCredential:
    async def test_a_local_only_person_cannot_sign_their_vm_in(self, client, acme):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await _patch(client, acme, legal, mode='local')

        refused = await _sign_in(client, make_token(ada, org_id=acme.org_id))

        assert refused.status_code == 400
        assert refused.json()['error'] == 'invalid_grant'
        assert refused.json()['reason'] == 'group_local_only'

    async def test_a_cloud_only_person_can(self, client, acme):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await _patch(client, acme, legal, mode='cloud')

        assert (await _sign_in(client, make_token(ada, org_id=acme.org_id))).status_code == 200

    async def test_a_vm_still_awake_cannot_renew_and_keeps_its_credential(self, client, acme):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        credential = (await _sign_in(client, make_token(ada, org_id=acme.org_id))).json()['vm_credential']
        await _patch(client, acme, legal, mode='local')

        refused = await _exchange(client, credential)
        async with TenantSessions().session_scope(acme.org_id) as session:
            revoked = (
                (await session.execute(select(VmCredential.revoked_at).where(VmCredential.producer_id == ada)))
                .scalars()
                .all()
            )
        await _patch(client, acme, legal, mode='local_and_cloud')
        renewed = await _exchange(client, credential)

        assert refused.json() == {
            'error': 'invalid_grant',
            'error_description': refused.json()['error_description'],
            'reason': 'group_local_only',
        }
        assert revoked == [None]
        assert renewed.status_code == 200, renewed.text

    async def test_another_refusal_carries_no_reason(self, client, acme):
        refused = await _sign_in(client, 'not-a-session')

        assert 'reason' not in refused.json()

    async def test_the_default_groups_mode_reaches_somebody_in_no_group(self, client, acme):
        ada, _ = await acme.person('ada@acme.com')
        default = await _default_group(client, acme)
        await _patch(client, acme, default['id'], mode='local')

        refused = await _sign_in(client, make_token(ada, org_id=acme.org_id))

        assert refused.json()['reason'] == 'group_local_only'


async def test_an_app_vm_is_untouched_by_any_group(client, acme):
    """Apps are in no group: their VMs renew whatever every group's mode is."""
    default = await _default_group(client, acme)
    await _patch(client, acme, default['id'], mode='local')
    app = await acme.app()
    enabled = await client.post(f'/portal/apps/{app.client_id}/vm-credential', headers=acme.admin)
    assert enabled.status_code == 201, enabled.text
    picked = await client.post(
        f'/platform/orgs/{acme.org_id}/producers/app/{app.client_id}/vm-credential/pickup',
        headers=await platform(client),
    )
    assert picked.status_code == 200, picked.text

    assert (await _exchange(client, picked.json()['vm_credential'])).status_code == 200
