"""Which organisations a service serves, asked of `Served` itself (`docs/service-identity.md` §21).

An organisation is served when the service's extension is switched on
for one of its groups or one of its apps, and it is still routed to: a
deleted organisation is detached, and a service has nothing to follow
there. `tests/test_removal_takes_effect_at_once.py` drives the same
answers over HTTP; these pin the cases one organisation cannot show.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.api.base_v1.schema.served import ServedOrganisation, ServedOrganisations
from app.db.sql.tenant_registry import TenantRegistryDB
from app.services.identity.served_organisations import Served
from tests.cloud_world import CloudOrg, shared_plane, shipped

pytestmark = pytest.mark.anyio

EXTENSION = 'igni.codeindex'


async def _on_for_a_group(org: CloudOrg) -> None:
    await org.switch_group(await org.group('Legal'), EXTENSION)


async def test_each_routed_organisation_with_the_extension_on_is_served_in_tenant_order(client):
    service = await shipped(client, EXTENSION)
    # Made, and so numbered, in this order; the slugs sort the other way.
    zeta = await CloudOrg(client, slug='zeta').create()
    await _on_for_a_group(zeta)
    acme = await CloudOrg(client, slug='acme').create()
    await acme.switch_app((await acme.app()).client_id, EXTENSION)
    initech = await CloudOrg(client, slug='initech').create()
    await _on_for_a_group(initech)
    globex = await CloudOrg(client, slug='globex').create()
    await globex.group('Legal')

    before = await Served().org_ids(service.client_id)
    await TenantRegistryDB().detach(initech.org_id)
    after = await Served().org_ids(service.client_id)

    assert before == [zeta.org_id, acme.org_id, initech.org_id]
    assert after == [zeta.org_id, acme.org_id]


async def test_switched_off_again_the_organisation_is_no_longer_served(client):
    service = await shipped(client, EXTENSION)
    acme = await CloudOrg(client).create()
    legal = await acme.group('Legal')
    await acme.switch_group(legal, EXTENSION)
    await acme.switch_group(legal, EXTENSION, enabled=False)

    assert await Served().org_ids(service.client_id) == []


async def test_a_client_with_no_extension_serves_nothing(client):
    await shipped(client, EXTENSION)
    acme = await CloudOrg(client).create()
    await _on_for_a_group(acme)

    assert await Served().org_ids('no-such-client') == []
    assert await Served().list('no-such-client') == ServedOrganisations(organisations=[])


async def test_the_list_names_each_by_display_name_then_slug_and_skips_one_with_no_organisation(client):
    service = await shipped(client, EXTENSION)
    named = await CloudOrg(client, slug='acme').create()
    await _on_for_a_group(named)
    unnamed = await CloudOrg(client, slug='globex').create()
    await _on_for_a_group(unnamed)
    gone = await CloudOrg(client, slug='initech').create()
    await _on_for_a_group(gone)
    async with shared_plane() as db:
        await db.execute(text("UPDATE organizations SET display_name = 'Acme Corp' WHERE id = :o"), {'o': named.org_id})
        await db.execute(text("UPDATE organizations SET display_name = '' WHERE id = :o"), {'o': unnamed.org_id})
        await db.execute(text('DELETE FROM org_memberships WHERE org_id = :o'), {'o': gone.org_id})
        await db.execute(text('DELETE FROM organizations WHERE id = :o'), {'o': gone.org_id})
        await db.commit()

    served = await Served().list(service.client_id)

    assert await Served().org_ids(service.client_id) == [named.org_id, unnamed.org_id, gone.org_id]
    assert served == ServedOrganisations(
        organisations=[
            ServedOrganisation(org_id=named.org_id, name='Acme Corp'),
            ServedOrganisation(org_id=unnamed.org_id, name='globex'),
        ]
    )
