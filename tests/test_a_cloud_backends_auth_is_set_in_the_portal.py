"""Each kind's auth methods are set in the portal, and a secret goes in and never comes out.

The portal builds its form from the server's own table of methods, so it
can never offer one the server refuses. Settings (client ids, a service
account's address) are shown back; secrets (a key, a client secret, a
service-account key) are not, and a refusal never echoes one. Switching
method drops the old method's secret and settings.
"""

from __future__ import annotations

import pytest

from app.db.sql.inference_nodes import InferenceNodeDB
from app.services.ai.providers.auth import AuthMethod, METHODS
from tests.portal_backends import AZURE, routed, VERTEX

pytestmark = pytest.mark.usefixtures('no_probing', 'only_pinned_nodes')


class TestAuthMethods:
    async def test_the_portal_is_told_every_kinds_methods_from_the_servers_own_table(self, client, fleet_admin):
        headers, _org = fleet_admin
        resp = await client.get('/portal/inference/auth-methods', headers=headers)

        assert resp.status_code == 200
        table = {row['kind']: [m['method'] for m in row['methods']] for row in resp.json()}
        assert table == {kind.value: [m.value for m in methods] for kind, methods in METHODS.items()}
        azure = next(row for row in resp.json() if row['kind'] == 'azure_openai')
        secret = next(m for m in azure['methods'] if m['method'] == 'client_secret')
        assert (secret['settings'], secret['secret']) == (['tenant_id', 'client_id'], 'credential')

    async def test_settings_are_shown_back_and_the_secret_is_not(self, client, fleet_admin):
        headers, _org = fleet_admin
        body = {
            **AZURE,
            'auth': 'client_secret',
            'auth_settings': {'tenant_id': 'tenant-1', 'client_id': 'client-1'},
            'credential': 'CLIENT-SECRET-MATERIAL',
        }
        created = await client.post('/portal/inference/nodes', json=body, headers=headers)
        read = await client.get('/portal/inference/nodes/azure-eu', headers=headers)

        assert created.status_code == 201, created.text
        for resp in (created, read):
            assert 'CLIENT-SECRET-MATERIAL' not in resp.text
        view = read.json()
        assert (view['auth'], view['auth_settings'], view['has_credential']) == (
            'client_secret',
            {'tenant_id': 'tenant-1', 'client_id': 'client-1'},
            True,
        )

    async def test_a_method_the_kind_does_not_offer_is_refused_at_the_door(self, client, fleet_admin):
        headers, _org = fleet_admin
        resp = await client.post('/portal/inference/nodes', json={**VERTEX, 'auth': 'client_secret'}, headers=headers)
        assert resp.status_code == 422
        assert resp.json()['detail'] == (
            "Node 'vertex-eu' was not added: Backend 'vertex-eu' (vertex_openai): auth 'client_secret' is not "
            'offered for this kind; it takes ambient, impersonate, service_account_key'
        )
        assert await InferenceNodeDB().get_by_node_id('vertex-eu') is None

    async def test_switching_method_drops_the_old_methods_secret_and_settings(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post(
            '/portal/inference/nodes',
            json={
                **AZURE,
                'auth': 'client_secret',
                'auth_settings': {'tenant_id': 't', 'client_id': 'c'},
                'credential': 'CLIENT-SECRET-MATERIAL',
            },
            headers=headers,
        )

        resp = await client.patch(
            '/portal/inference/nodes/azure-eu',
            json={'auth': 'managed_identity', 'auth_settings': {'client_id': 'uami-1'}},
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        backend = (await routed())['azure-eu']
        assert (backend.auth, backend.auth_settings, backend.credential) == (
            AuthMethod.MANAGED_IDENTITY,
            {'client_id': 'uami-1'},
            None,
        )
        assert resp.json()['has_credential'] is False

    async def test_switching_to_the_servers_identity_drops_the_app_registration_entirely(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post(
            '/portal/inference/nodes',
            json={
                **AZURE,
                'auth': 'client_secret',
                'auth_settings': {'tenant_id': 't', 'client_id': 'c'},
                'credential': 'CLIENT-SECRET-MATERIAL',
            },
            headers=headers,
        )

        resp = await client.patch('/portal/inference/nodes/azure-eu', json={'auth': 'ambient'}, headers=headers)

        assert resp.status_code == 200, resp.text
        backend = (await routed())['azure-eu']
        assert (backend.auth, backend.auth_settings, backend.credential) == (AuthMethod.AMBIENT, {}, None)

    async def test_switching_to_the_servers_identity_needs_nothing_else(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json={**AZURE, 'api_key': 'RESOURCE-KEY'}, headers=headers)

        resp = await client.patch('/portal/inference/nodes/azure-eu', json={'auth': 'ambient'}, headers=headers)

        assert resp.status_code == 200, resp.text
        backend = (await routed())['azure-eu']
        assert (backend.auth, backend.api_key) == (AuthMethod.AMBIENT, None)
        assert resp.json()['has_api_key'] is False

    async def test_a_switch_missing_the_new_methods_settings_is_refused_and_writes_nothing(self, client, fleet_admin):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json={**AZURE, 'api_key': 'RESOURCE-KEY'}, headers=headers)

        resp = await client.patch(
            '/portal/inference/nodes/azure-eu', json={'auth': 'managed_identity'}, headers=headers
        )

        assert resp.status_code == 422
        backend = (await routed())['azure-eu']
        assert (backend.auth, backend.api_key) == (AuthMethod.API_KEY, 'RESOURCE-KEY')


class TestARefusalNeverEchoesASecret:
    """FastAPI answers a validation failure with the offending input. A
    refusal of a node carrying a key or a credential must not hand it back."""

    @pytest.mark.parametrize(
        'body',
        [
            {**VERTEX, 'api_key': 'KEY-MATERIAL'},
            {**AZURE, 'auth': 'ambient', 'credential': 'CREDENTIAL-MATERIAL'},
            {**AZURE, 'base_url': 'not-a-url', 'api_key': 'KEY-MATERIAL'},
        ],
    )
    async def test_adding(self, client, fleet_admin, body):
        headers, _org = fleet_admin
        resp = await client.post('/portal/inference/nodes', json=body, headers=headers)
        assert resp.status_code == 422
        assert 'MATERIAL' not in resp.text

    @pytest.mark.parametrize(
        'edit',
        [
            {'base_url': 'not-a-url', 'api_key': 'KEY-MATERIAL'},
            {'auth': 'managed_identity', 'credential': 'CREDENTIAL-MATERIAL'},
        ],
    )
    async def test_editing(self, client, fleet_admin, edit):
        headers, _org = fleet_admin
        await client.post('/portal/inference/nodes', json=AZURE, headers=headers)
        resp = await client.patch('/portal/inference/nodes/azure-eu', json=edit, headers=headers)
        assert resp.status_code == 422
        assert 'MATERIAL' not in resp.text
