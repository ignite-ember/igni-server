"""Registering a node decides where prompts go, so it is recorded. F114.

`/v1/nodes` was outside F90's audited prefixes. The three routes there
are the ones that change the inference fleet, and registration is
**idempotent by design** — re-registering an existing id replaces its
address. So a caller holding the node token could point a node id at a
host they control and receive every chat request routed to it: the
prompt, whatever source the CLI attached as context, the previous turn's
tool output. With no record that it happened, by whom, or from where.

The service contract notes the node token lives in Terraform and
cloud-init — "a much wider blast radius" than the service token — which
is the reason this matters rather than a reason it does not.

**The heartbeat is deliberately not audited**, and that is why a blanket
prefix was the wrong fix: it runs every 60 seconds per node, so auditing
it would write 1,440 rows per node per day and bury the `denied` rows an
administrator reads. A heartbeat's presence is not interesting; its
absence is already handled by the registration TTL.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.middlewares.audit import _TIMER_PATH_SUFFIXES, is_auditable
from app.services.ai.node_registry import NodeRegistry
from tests.conftest import TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def _leave_the_fleet_as_we_found_it():
    """Deregister everything this file registers, and drop the cache.

    Registering a node is global state: it lives in Redis and is cached
    by the backend provider. Without this, a node registered here
    survived into `test_request_size_and_cost_bounds.py`, which asserts
    that no inference backend is configured — so a test that passes
    alone failed in the full suite, for a reason two files away.

    Exactly the coupling F112 and F113 had to unpick in other people's
    tests, produced here by mine. Per-test cleanup rather than
    per-registration, because a test that fails midway would otherwise
    leak.
    """
    yield

    from app.services.ai.pool_manager import PoolManager

    registry = NodeRegistry()
    for node in await registry.list_nodes():
        await registry.deregister(node.backend.id)

    PoolManager().provider.invalidate()


def _audit_rows(route: str):
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            return conn.execute(
                text(
                    'SELECT route, method, status_code, detail::text AS d, client_host '
                    'FROM audit_events WHERE route = :r ORDER BY occurred_at DESC'
                ),
                {'r': route},
            ).fetchall()
    finally:
        engine.dispose()


class TestThePolicy:
    """Read from `is_auditable`, which is the whole rule in one place."""

    def test_registering_is_audited(self):
        assert is_auditable('POST', '/v1/nodes') is True

    def test_deregistering_is_audited(self):
        assert is_auditable('DELETE', '/v1/nodes/gpu-3') is True

    def test_a_heartbeat_is_not(self):
        assert is_auditable('POST', '/v1/nodes/gpu-3/heartbeat') is False

    def test_the_exclusion_is_a_rule_not_a_node_id(self):
        """Matched as a suffix, so it covers every node's heartbeat and
        any future timer endpoint, rather than being a list of ids that
        goes stale the moment a cluster scales."""
        assert '/heartbeat' in _TIMER_PATH_SUFFIXES

        for node_id in ('gpu-3', 'a', 'node.with-dots_and-dashes', 'x' * 120):
            assert is_auditable('POST', f'/v1/nodes/{node_id}/heartbeat') is False

    def test_reading_the_fleet_is_not_audited(self):
        """A GET is not an event. The rule is unchanged for reads, and
        the audited-reads list is for bulk exports."""
        assert is_auditable('GET', '/v1/nodes') is False


class TestTheRecordSaysWherePromptsWillGo:
    @pytest.fixture
    def node_token(self, monkeypatch):
        from app.config import settings

        token = 'node-token-for-this-test'
        monkeypatch.setattr(settings, 'node_registration_token', token)
        return {'X-Igni-Node-Token': token}

    async def test_a_registration_is_recorded_with_its_address(self, client, node_token):
        """The address is the point. Without it the trail would say a
        node was registered without saying where its traffic goes, which
        answers none of the questions somebody would ask afterwards."""
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'

        response = await client.post(
            '/nodes',
            headers=node_token,
            json={
                'id': node_id,
                'base_url': 'http://attacker.internal:8000/v1',
                'models': ['qwen-coder'],
            },
        )
        assert response.status_code == 201, response.text

        rows = _audit_rows('/v1/nodes')
        assert rows, 'registering a node wrote no audit record'
        newest = rows[0].d or ''
        assert node_id in newest, newest
        assert 'attacker.internal' in newest, 'the record does not say where prompts will go'

        await NodeRegistry().deregister(node_id)

    async def test_the_node_credential_is_never_in_the_record(self, client, node_token):
        """`api_key` is the node's own proxy credential. The audit trail
        is retained, so it must not become where one survives."""
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'

        await client.post(
            '/nodes',
            headers=node_token,
            json={
                'id': node_id,
                'base_url': 'http://gpu-1:8000/v1',
                'models': ['m'],
                'api_key': 'sk-the-nodes-own-secret',
            },
        )

        rows = _audit_rows('/v1/nodes')
        assert rows
        assert 'sk-the-nodes-own-secret' not in (rows[0].d or '')

        await NodeRegistry().deregister(node_id)

    async def test_a_replacement_is_distinguishable_from_a_first_registration(self, client, node_token):
        """A cluster starting and somebody redirecting a live node look
        identical on the wire. The difference is the fact an operator
        would want first."""
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'
        body = {'id': node_id, 'base_url': 'http://gpu-1:8000/v1', 'models': ['m']}

        await client.post('/nodes', headers=node_token, json=body)
        await client.post(
            '/nodes',
            headers=node_token,
            json={**body, 'base_url': 'http://somewhere-else:8000/v1'},
        )

        rows = _audit_rows('/v1/nodes')
        assert len(rows) >= 2
        assert '"replaced": true' in (rows[0].d or '').replace("'", '"'), rows[0].d
        assert '"replaced": false' in (rows[1].d or '').replace("'", '"'), rows[1].d

        await NodeRegistry().deregister(node_id)

    async def test_the_principal_is_named(self, client, node_token):
        """These routes authenticate with a shared token, so there is no
        user behind them. A row with no actor and no explanation reads
        like a bug in the audit trail rather than a fact about the
        caller."""
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'

        await client.post(
            '/nodes', headers=node_token, json={'id': node_id, 'base_url': 'http://g:8000/v1', 'models': ['m']}
        )

        rows = _audit_rows('/v1/nodes')
        assert 'node-token' in (rows[0].d or '')

        await NodeRegistry().deregister(node_id)

    async def test_a_heartbeat_writes_nothing(self, client, node_token):
        """The volume argument, checked rather than asserted in a
        comment: 60 seconds per node is 1,440 rows a day each."""
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'
        await client.post(
            '/nodes', headers=node_token, json={'id': node_id, 'base_url': 'http://g:8000/v1', 'models': ['m']}
        )

        before = len(_audit_rows('/v1/nodes/{node_id}/heartbeat'))
        for _ in range(5):
            await client.post(f'/nodes/{node_id}/heartbeat', headers=node_token)

        assert len(_audit_rows('/v1/nodes/{node_id}/heartbeat')) == before

        await NodeRegistry().deregister(node_id)

    async def test_a_removal_is_recorded(self, client, node_token):
        node_id = f'gpu-{uuid.uuid4().hex[:8]}'
        await client.post(
            '/nodes', headers=node_token, json={'id': node_id, 'base_url': 'http://g:8000/v1', 'models': ['m']}
        )

        response = await client.delete(f'/nodes/{node_id}', headers=node_token)
        assert response.status_code == 204, response.text

        rows = _audit_rows('/v1/nodes/{node_id}')
        assert rows, 'removing a node from rotation wrote no audit record'
        assert node_id in (rows[0].d or '')

    async def test_a_refused_registration_is_recorded_too(self, client, monkeypatch):
        """A wrong token is exactly the attempt worth having a record of,
        and F90 audits by route and outcome rather than by success."""
        from app.config import settings

        monkeypatch.setattr(settings, 'node_registration_token', 'the-real-token')

        response = await client.post(
            '/nodes',
            headers={'X-Igni-Node-Token': 'wrong'},
            json={'id': 'gpu-x', 'base_url': 'http://g:8000/v1', 'models': ['m']},
        )
        assert response.status_code in (401, 403), response.status_code

        rows = _audit_rows('/v1/nodes')
        assert rows, 'a refused registration wrote no audit record'
        assert rows[0].status_code in (401, 403), rows[0].status_code
