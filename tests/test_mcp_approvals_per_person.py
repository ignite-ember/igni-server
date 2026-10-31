"""A person's MCP server approvals, kept on igni server for igni in their cloud VM.

Keyed as on the desktop — a server's name and the configuration it came
from — and only a person's: an app has nobody to ask
(`docs/service-identity.md` §14).
"""

from __future__ import annotations

import pytest

from tests.cloud_world import CloudOrg

pytestmark = pytest.mark.anyio

GITHUB = {'server': 'github', 'source': '/workspace/.mcp.json'}


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def test_a_person_approves_reads_and_withdraws(client, acme):
    _, ada = await acme.person('ada@acme.com')

    approved = await client.post('/me/mcp-approvals', json=GITHUB, headers=ada)
    again = await client.post('/me/mcp-approvals', json=GITHUB, headers=ada)
    other_file = await client.post('/me/mcp-approvals', json={**GITHUB, 'source': 'group-policy'}, headers=ada)
    withdrawn = await client.delete('/me/mcp-approvals', params=GITHUB, headers=ada)
    left = (await client.get('/me/mcp-approvals', headers=ada)).json()['approvals']

    assert approved.status_code == 200
    assert [(a['server'], a['source']) for a in again.json()['approvals']] == [('github', '/workspace/.mcp.json')]
    assert len(other_file.json()['approvals']) == 2
    assert withdrawn.status_code == 204
    assert [(a['server'], a['source']) for a in left] == [('github', 'group-policy')]


async def test_one_persons_approvals_are_not_anothers(client, acme):
    _, ada = await acme.person('ada@acme.com')
    _, max_headers = await acme.person('max@acme.com')

    await client.post('/me/mcp-approvals', json=GITHUB, headers=ada)

    assert (await client.get('/me/mcp-approvals', headers=max_headers)).json()['approvals'] == []


async def test_an_app_does_not_approve(client, acme):
    app = await acme.app()

    response = await client.post('/me/mcp-approvals', json=GITHUB, headers=await acme.app_token(app))

    assert response.status_code == 403


async def test_signed_out_is_refused(client):
    assert (await client.get('/me/mcp-approvals')).status_code == 401
