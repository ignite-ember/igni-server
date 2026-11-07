"""A removed member's VM is kept for the organisation's retention, then erased — unless a legal hold stops it.

The organisation is the controller: it sets `removed_member_retention_days`
(default 30, 0 at once) within the platform's maximum. The feed says
`removed: true` at once and `erase_at` when the gateway erases the VM's
state, volume and keys. A legal hold stops the clock; a personal erasure
request overrides the retention but not a hold. Every erasure scheduled,
moved or stopped is audited (`docs/service-identity.md` §18).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import pytest

from app.config import settings
from app.services import retention
from tests.cloud_world import CloudOrg, make_super_admin, platform
from tests.conftest import audit_rows

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setattr(settings, 'secret_encryption_key', Fernet.generate_key().decode())


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


class Feed:
    def __init__(self, client, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers
        self.cursor: str | None = None

    async def poll(self) -> dict[str, dict]:
        params = {'since': self.cursor} if self.cursor else {}
        body = (await self.client.get('/platform/producers', params=params, headers=self.headers)).json()
        self.cursor = body['cursor']
        return {row['producer']['id']: row for row in body['producers']}


def _when(row: dict) -> datetime | None:
    return datetime.fromisoformat(row['erase_at']) if row['erase_at'] else None


def _about(when: datetime | None, days: int) -> bool:
    expected = datetime.now(timezone.utc) + timedelta(days=days)
    return when is not None and abs((when - expected).total_seconds()) < 60


async def _removed(client, acme: CloudOrg, feed: Feed, email: str = 'ada@acme.com') -> str:
    ada, _ = await acme.person(email)
    await feed.poll()
    assert (await client.delete(f'/portal/team/{ada}', headers=acme.admin)).status_code in (200, 204)
    return ada


class TestTheRetention:
    async def test_a_removed_member_is_erased_after_thirty_days_by_default(self, client, acme):
        feed = Feed(client, await platform(client))
        ada = await _removed(client, acme, feed)

        row = (await feed.poll())[ada]

        assert (row['active'], row['removed']) == (False, True)
        assert _about(_when(row), 30)
        [event] = [e for e in audit_rows(route='producer-feed/erasure') if e['target_id'] == ada]
        assert event['detail']['why'] == 'removed'
        assert event['detail']['incarnation'] == row['incarnation']

    async def test_the_organisation_sets_it_within_the_platforms_maximum(self, client, acme):
        feed = Feed(client, await platform(client))
        path = f'/portal/orgs/{acme.org_id}/retention'
        _, member = await acme.person('max@acme.com')

        too_long = await client.put(path, json={'removed_member_retention_days': 400}, headers=acme.admin)
        by_member = await client.put(path, json={'removed_member_retention_days': 1}, headers=member)
        at_once = await client.put(path, json={'removed_member_retention_days': 0}, headers=acme.admin)
        ada = await _removed(client, acme, feed)
        row = (await feed.poll())[ada]
        await make_super_admin(acme.admin_id)
        raised = await client.put(
            '/portal/retention-caps', json={'max_removed_member_retention_days': 500}, headers=acme.admin
        )
        longer = await client.put(path, json={'removed_member_retention_days': 400}, headers=acme.admin)

        assert too_long.status_code == 400
        assert 'at most 365 days' in too_long.json()['detail']
        assert by_member.status_code == 403
        assert at_once.json() == {
            'org_id': acme.org_id,
            'removed_member_retention_days': 0,
            'max_removed_member_retention_days': 365,
            'updated_at': at_once.json()['updated_at'],
        }
        assert _about(_when(row), 0)
        assert raised.json()['max_removed_member_retention_days'] == 500
        assert longer.json()['removed_member_retention_days'] == 400

    async def test_an_app_is_erased_at_once(self, client, acme):
        feed = Feed(client, await platform(client))
        app = await acme.app()
        await feed.poll()

        await client.delete(f'/portal/apps/{app.client_id}', headers=acme.admin)
        row = (await feed.poll())[app.client_id]

        assert row['removed'] is True
        assert _about(_when(row), 0)

    async def test_the_feed_purges_counting_from_the_erasure(self, client, acme, monkeypatch):
        feed = Feed(client, await platform(client))
        ada = await _removed(client, acme, feed)
        await feed.poll()

        def later(days: int) -> None:
            ahead = datetime.now(timezone.utc) + timedelta(days=days)
            monkeypatch.setattr(
                'app.db.sql.producer_feed.datetime', type('Later', (), {'now': staticmethod(lambda tz=None: ahead)})
            )

        later(45)
        kept = await retention.run_once()
        later(61)
        purged = await retention.run_once()

        assert (kept.producer_feed, purged.producer_feed) == (0, 1)
        assert ada not in await feed.poll()


class TestLegalHolds:
    async def test_a_hold_stops_the_clock_and_releasing_it_starts_it_again(self, client, acme):
        feed = Feed(client, await platform(client))
        ada = await _removed(client, acme, feed)
        first = (await feed.poll())[ada]
        hold = f'/portal/orgs/{acme.org_id}/legal-holds/{ada}'

        placed = await client.put(hold, json={'reason': 'Litigation 2026-17'}, headers=acme.admin)
        held = (await feed.poll())[ada]
        listed = (await client.get(f'/portal/orgs/{acme.org_id}/legal-holds', headers=acme.admin)).json()
        released = await client.delete(hold, headers=acme.admin)
        again = (await feed.poll())[ada]

        assert placed.status_code == 200, placed.text
        assert held['erase_at'] is None
        assert (held['removed'], held['incarnation'], held['legal_hold']) == (True, first['incarnation'], False)
        [entry] = listed['holds']
        assert (entry['user_id'], entry['reason']) == (ada, 'Litigation 2026-17')
        assert entry['placed_by_user_id'] == acme.admin_id
        assert [i['incarnation'] for i in entry['incarnations']] == [first['incarnation']]
        assert released.status_code == 204
        assert _about(_when(again), 30)
        whys = [e['detail']['why'] for e in audit_rows(route='producer-feed/erasure') if e['target_id'] == ada]
        assert sorted(whys) == ['held', 'released', 'removed']
        holds = [(e['method'], e['status_code']) for e in audit_rows(target_type='legal_hold', target_id=ada)]
        assert sorted(holds) == [('DELETE', 204), ('PUT', 200)]

    async def test_a_hold_placed_before_the_removal_holds_it_from_the_start(self, client, acme):
        feed = Feed(client, await platform(client))
        ada, _ = await acme.person('ada@acme.com')
        await client.put(f'/portal/orgs/{acme.org_id}/legal-holds/{ada}', json={'reason': 'x'}, headers=acme.admin)
        await feed.poll()

        await client.delete(f'/portal/team/{ada}', headers=acme.admin)
        row = (await feed.poll())[ada]

        assert (row['removed'], row['erase_at']) == (True, None)

    async def test_a_personal_erasure_is_refused_while_held_and_overrides_retention_without(self, client, acme):
        feed = Feed(client, await platform(client))
        ada = await _removed(client, acme, feed)
        await feed.poll()
        hold = f'/portal/orgs/{acme.org_id}/legal-holds/{ada}'
        await client.put(hold, json={'reason': 'x'}, headers=acme.admin)

        refused = await client.post('/portal/erasure', json={'email': 'ada@acme.com'}, headers=acme.admin)
        await client.delete(hold, headers=acme.admin)
        erased = await client.post('/portal/erasure', json={'email': 'ada@acme.com'}, headers=acme.admin)
        row = (await feed.poll())[ada]

        assert refused.status_code == 409
        assert 'legal hold' in refused.json()['detail']
        assert any(
            e['route'].endswith('/erasure') and e['status_code'] == 409 and 'refused' in str(e['detail'])
            for e in audit_rows()
        )
        assert erased.status_code == 200, erased.text
        assert _about(_when(row), 0)

    async def test_only_the_organisations_admins(self, client, acme):
        ada, member = await acme.person('ada@acme.com')

        response = await client.put(
            f'/portal/orgs/{acme.org_id}/legal-holds/{ada}', json={'reason': 'x'}, headers=member
        )

        assert response.status_code == 403
