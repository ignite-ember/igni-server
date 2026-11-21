"""The deployment's one licence high-water mark, served to the modules and the gateway.

igni server holds the single mark; the paid modules (`service` clients) and
the Fleet gateway (a `platform` client) read it from here and cache it
(docs/licence-file.md, "The clock"). The endpoint is approval-gated — a
module's own token opens it, an unapproved or absent one does not — so the
rollback protection reaches the modules rather than silently degrading.
"""

from __future__ import annotations

from tests.cloud_world import CloudOrg, platform, token_for
from tests.registered_service import RegisteredService

_PATH = '/platform/licence/clock'


async def test_a_platform_client_reads_the_mark(client) -> None:
    gateway = await platform(client)

    response = await client.get(_PATH, headers=gateway)

    assert response.status_code == 200
    assert 'seen' in response.json()  # None before the first time is recorded; the shape is always there


async def test_an_approved_service_client_reads_the_mark(client) -> None:
    # A paid module (CodeIndex, Artifacts) calls igni with its own service token, scoped to one organisation.
    acme = await CloudOrg(client).create()
    module = RegisteredService(kind='service', name='CodeIndex')
    await module.approve()

    headers = await token_for(client, module, organisation=acme.org_id)
    response = await client.get(_PATH, headers=headers)

    assert response.status_code == 200
    assert 'seen' in response.json()


async def test_no_token_is_refused(client) -> None:
    assert (await client.get(_PATH)).status_code == 401
