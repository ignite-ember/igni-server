"""A program needs only igni server's address: discovery names Fleet's gateway too.

igni's SDK configures the organisation here and runs work in a producer's
VM through Fleet's gateway. So that a program is given one address, the
discovery document announces the gateway (`igni_gateway`, from
`FLEET_GATEWAY_URL`) — and leaves the field out where there is no Fleet,
rather than naming a gateway that is not there.
"""

from __future__ import annotations

import pytest

from app.config import settings

pytestmark = pytest.mark.anyio


async def _metadata(client) -> dict:
    response = await client.get('http://test/.well-known/openid-configuration')
    assert response.status_code == 200, response.text
    return response.json()


async def test_it_names_the_gateway(client, monkeypatch):
    monkeypatch.setattr(settings, 'fleet_gateway_url', 'https://gw.acme.internal/')

    assert (await _metadata(client))['igni_gateway'] == 'https://gw.acme.internal'


async def test_without_fleet_it_names_none(client, monkeypatch):
    monkeypatch.setattr(settings, 'fleet_gateway_url', '')

    assert 'igni_gateway' not in await _metadata(client)
