"""`GET /v1/me/cloud`: whether a person has a cloud igni to use from the desktop, and where.

The desktop's Local / Cloud switch is built from this answer: whether
there is a cloud igni for the person (and why not), the gateway's
address, their group's mode and who pulls their events (igni-core
`docs/design/local-cloud-switch.md` §2.1). The person's own session only;
the shape is igni-sdk's `cloud-discovery.schema.json`, whose copy is in
`tests/fixtures/contracts`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.api.base_v1.schema.cloud_discovery import CloudDiscovery, SetEventsOwner
from app.config import settings
from app.security.producer import ProducerCaller
from app.services.auth.jwt_service import JWTService
from app.services.cloud.cloud_discovery import CloudDiscoveries
from app.services.deployment_licence import deployment_licence, DeploymentLicence
from app.services.licence import Entitlement, LicenceKind, Module
from app.services.licence.source import RECHECK
from tests.cloud_world import CloudOrg, shared_plane
from tests.conftest import auth_header
from tests.json_schema import JsonSchema
from tests.licence_kit import EXPIRES, FakeClock, licence_file, payload, signed, verifier

pytestmark = pytest.mark.anyio

GATEWAY = 'https://fleet.acme.example'
#: igni-sdk's `contracts/cloud-discovery.schema.json`, copied (igni-sdk feat/local-cloud-switch, 2d4e752).
CONTRACT = Path(__file__).parent / 'fixtures' / 'contracts' / 'cloud-discovery.schema.json'


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(EXPIRES - timedelta(days=100))


@pytest.fixture
def licence_path(tmp_path: Path) -> Path:
    return tmp_path / 'licence' / 'licence.json'


@pytest.fixture
def licence(app, licence_path: Path, clock: FakeClock) -> Iterator[DeploymentLicence]:
    """The deployment's licence on the test's key and clock — Fleet in it — served to every request."""
    licence_path.parent.mkdir(parents=True)
    licence_path.write_bytes(signed(payload({Module.FLEET: Entitlement(users=100, expires=EXPIRES)})))
    instance = DeploymentLicence(licence_file(licence_path, clock), verifier=verifier())
    app.dependency_overrides[deployment_licence] = lambda: instance
    yield instance
    app.dependency_overrides.pop(deployment_licence, None)


@pytest.fixture
def fleet(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'fleet_gateway_url', f'{GATEWAY}/')


@pytest.fixture
async def acme(client) -> CloudOrg:
    return await CloudOrg(client).create()


async def _cloud(client, headers: dict[str, str], **extra: str) -> dict:
    response = await client.get('/me/cloud', headers={**headers, **extra})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _set_mode(client, acme: CloudOrg, group_id: str, mode: str) -> None:
    response = await client.patch(f'/portal/groups/{group_id}', json={'mode': mode}, headers=acme.admin)
    assert response.status_code == 200, response.text


class TestWhatItAnswers:
    async def test_a_person_in_a_group_that_uses_both_has_one(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        ada, session = await acme.person('ada@acme.com', legal)

        assert await _cloud(client, session) == {
            'cloud': 'available',
            'reason': None,
            'gateway_url': GATEWAY,
            'mode': 'local_and_cloud',
            'events_owner': 'local',
            'group': {'id': legal, 'name': 'Legal'},
            'producer': f'person:{ada}',
        }

    async def test_a_cloud_only_group_owns_its_events_in_the_cloud(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        await _set_mode(client, acme, legal, 'cloud')

        answer = await _cloud(client, session)

        assert (answer['cloud'], answer['reason'], answer['mode'], answer['events_owner']) == (
            'available',
            None,
            'cloud',
            'cloud',
        )

    async def test_a_local_only_group_has_none(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        await _set_mode(client, acme, legal, 'local')

        answer = await _cloud(client, session)

        assert (answer['cloud'], answer['reason'], answer['mode'], answer['events_owner']) == (
            'unavailable',
            'group_local_only',
            'local',
            'local',
        )
        # Still says where the gateway is: that is the deployment's, not the person's.
        assert answer['gateway_url'] == GATEWAY

    async def test_without_fleet_there_is_no_gateway(self, client, acme, licence, monkeypatch):
        monkeypatch.setattr(settings, 'fleet_gateway_url', '')
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        answer = await _cloud(client, session)

        assert (answer['cloud'], answer['reason'], answer['gateway_url']) == (
            'unavailable',
            'fleet_not_configured',
            None,
        )

    async def test_no_fleet_comes_before_the_groups_mode(self, client, acme, licence, monkeypatch):
        monkeypatch.setattr(settings, 'fleet_gateway_url', '')
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        await _set_mode(client, acme, legal, 'local')

        assert (await _cloud(client, session))['reason'] == 'fleet_not_configured'

    async def test_the_gateway_is_the_one_discovery_publishes(self, client, acme, fleet, licence):
        _, session = await acme.person('ada@acme.com')

        published = (await client.get('http://test/.well-known/openid-configuration')).json()['igni_gateway']

        assert (await _cloud(client, session))['gateway_url'] == published

    async def test_somebody_in_no_group_has_the_default_groups(self, client, acme, fleet, licence):
        _, session = await acme.person('ada@acme.com')

        answer = await _cloud(client, session)

        groups = (await client.get('/portal/groups', headers=acme.admin)).json()['groups']
        [default] = [group for group in groups if group['is_default']]
        assert answer['group'] == {'id': default['id'], 'name': default['name']}
        assert answer['mode'] == default['mode'] == 'local_and_cloud'

    async def test_the_default_groups_mode_reaches_its_people(self, client, acme, fleet, licence):
        _, session = await acme.person('ada@acme.com')
        default = (await _cloud(client, session))['group']['id']
        await _set_mode(client, acme, default, 'local')

        assert (await _cloud(client, session))['reason'] == 'group_local_only'


class TestTheLicence:
    async def test_without_a_licence_fleet_cannot_start_a_vm(self, client, acme, fleet, licence, licence_path, clock):
        licence_path.unlink()
        clock.advance(RECHECK)
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        assert (await _cloud(client, session))['reason'] == 'not_licensed'

    async def test_a_licence_without_fleet_is_not_one_for_it(self, client, acme, fleet, licence, licence_path, clock):
        licence_path.write_bytes(signed(payload({Module.CODEINDEX: Entitlement(users=100, expires=EXPIRES)})))
        clock.advance(RECHECK)
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        assert (await _cloud(client, session))['reason'] == 'not_licensed'

    async def test_a_read_only_fleet_starts_no_vm(self, client, acme, fleet, licence, licence_path, clock):
        licence_path.write_bytes(
            signed(payload({Module.FLEET: Entitlement(users=25, expires=EXPIRES)}, kind=LicenceKind.EVALUATION))
        )
        clock.set(EXPIRES + timedelta(days=1))
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        assert (await _cloud(client, session))['reason'] == 'not_licensed'

    async def test_a_lapsed_licence_in_its_grace_still_does(self, client, acme, fleet, licence, clock):
        clock.set(EXPIRES + timedelta(days=1))
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        assert (await _cloud(client, session))['cloud'] == 'available'


class TestAnInactiveMember:
    """A deactivated member's session is refused before this is asked; the service still answers for them."""

    async def _deactivate(self, acme: CloudOrg, user_id: str) -> None:
        async with shared_plane() as session:
            await session.execute(
                text("UPDATE org_memberships SET status = 'deactivated' WHERE org_id = :o AND user_id = :u"),
                {'o': acme.org_id, 'u': user_id},
            )
            await session.commit()

    async def test_is_inactive_and_still_named_in_their_group(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await self._deactivate(acme, ada)

        answer = await CloudDiscoveries(licence).of(ProducerCaller(kind='person', id=ada, org_id=acme.org_id))

        assert answer.group is not None
        assert (answer.cloud, answer.reason, answer.group.id) == ('unavailable', 'inactive', legal)

    async def test_inactive_comes_before_the_groups_mode(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        await _set_mode(client, acme, legal, 'local')
        await self._deactivate(acme, ada)

        answer = await CloudDiscoveries(licence).of(ProducerCaller(kind='person', id=ada, org_id=acme.org_id))

        assert answer.reason == 'inactive'

    async def test_one_in_no_group_at_all_is_named_in_none(self, client, acme, fleet, licence):
        stranger = ProducerCaller(kind='person', id='nobody-here', org_id=acme.org_id)

        answer = await CloudDiscoveries(licence).of(stranger)

        assert (answer.reason, answer.group, answer.mode) == ('inactive', None, 'local_and_cloud')


class TestWhoMayAsk:
    async def test_an_app_is_refused(self, client, acme, fleet, licence):
        app = await acme.app()

        assert (await client.get('/me/cloud', headers=await acme.app_token(app))).status_code == 403

    async def test_a_space_is_refused(self, client, acme, fleet, licence):
        app = await acme.app()
        app_headers = await acme.app_token(app)
        space = (await client.put(f'/apps/{app.client_id}/spaces/cust-1', headers=app_headers)).json()
        token = (
            await client.post(
                '/oauth/token',
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:token-exchange',
                    'subject_token': app_headers['Authorization'].removeprefix('Bearer '),
                    'subject_token_type': 'urn:ietf:params:oauth:token-type:access_token',
                    'space': space['space_id'],
                },
            )
        ).json()['access_token']

        assert (await client.get('/me/cloud', headers=auth_header(token))).status_code == 403

    async def test_a_vms_token_is_refused(self, client, acme, fleet, licence):
        ada, _ = await acme.person('ada@acme.com')
        vms = JWTService().create_token(ada, org_id=acme.org_id, incarnation='membership-1')

        response = await client.get('/me/cloud', headers=auth_header(vms))

        assert response.status_code == 403
        assert (
            await client.put('/me/cloud/events-owner', json={'owner': 'cloud'}, headers=auth_header(vms))
        ).status_code == 403

    async def test_no_session_is_a_401(self, client):
        assert (await client.get('/me/cloud')).status_code == 401


class TestRevalidation:
    async def test_it_may_be_kept_a_minute_by_the_person_only(self, client, acme, fleet, licence):
        _, session = await acme.person('ada@acme.com')

        response = await client.get('/me/cloud', headers=session)

        assert response.headers['cache-control'] == 'private, max-age=60'
        assert response.headers['vary'] == 'Authorization'
        assert response.headers['etag'].startswith('"')

    async def test_the_tag_held_gets_a_304_without_a_body(self, client, acme, fleet, licence):
        _, session = await acme.person('ada@acme.com')
        etag = (await client.get('/me/cloud', headers=session)).headers['etag']

        again = await client.get('/me/cloud', headers={**session, 'If-None-Match': etag})

        assert again.status_code == 304
        assert again.content == b''
        assert again.headers['etag'] == etag
        assert again.headers['cache-control'] == 'private, max-age=60'

    async def test_a_change_of_mode_changes_the_tag(self, client, acme, fleet, licence):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        etag = (await client.get('/me/cloud', headers=session)).headers['etag']
        await _set_mode(client, acme, legal, 'cloud')

        again = await client.get('/me/cloud', headers={**session, 'If-None-Match': etag})

        assert again.status_code == 200
        assert again.headers['etag'] != etag
        assert again.json()['mode'] == 'cloud'

    async def test_a_refusal_is_not_kept(self, client, acme):
        app = await acme.app()

        refused = await client.get('/me/cloud', headers=await acme.app_token(app))

        assert refused.headers['cache-control'] == 'no-store, max-age=0'


@pytest.fixture
def contract() -> JsonSchema:
    return JsonSchema(json.loads(CONTRACT.read_text()))


class TestTheContract:
    """Every answer is igni-sdk's `cloud-discovery.schema.json`, its cross-field rules included."""

    def test_the_checker_reads_the_contract_and_refuses_what_breaks_it(self, contract):
        document = json.loads(CONTRACT.read_text())
        examples = document['$defs']['CloudDiscovery']['examples']
        good = examples[0]

        assert [contract.problems(example) for example in examples] == [[]] * len(examples)
        assert contract.problems({**good, 'reason': 'inactive'})  # available with a reason
        assert contract.problems({**good, 'mode': 'local'})  # local yet available
        assert contract.problems({**good, 'gateway_url': None})  # no gateway, not fleet_not_configured
        assert contract.problems({**good, 'producer': 'app:x'})
        assert contract.problems({k: v for k, v in good.items() if k != 'group'})
        assert contract.problems({**good, 'mode': 'cloud', 'events_owner': 'local'})

    async def test_every_answer_it_gives_matches(self, client, acme, fleet, licence, contract, monkeypatch):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        answers = [await _cloud(client, session)]
        for mode in ('local', 'cloud'):
            await _set_mode(client, acme, legal, mode)
            answers.append(await _cloud(client, session))
        await _set_mode(client, acme, legal, 'local_and_cloud')
        put = await client.put('/me/cloud/events-owner', json={'owner': 'cloud'}, headers=session)
        answers.append(put.json())
        monkeypatch.setattr(settings, 'fleet_gateway_url', '')
        answers.append(await _cloud(client, session))

        assert [contract.problems(answer) for answer in answers] == [[]] * len(answers)
        assert {answer['reason'] for answer in answers} == {None, 'group_local_only', 'fleet_not_configured'}

    async def test_the_answers_the_service_gives_without_a_session_match(self, client, acme, fleet, licence, contract):
        legal = await acme.group('Legal')
        ada, _ = await acme.person('ada@acme.com', legal)
        async with shared_plane() as session:
            await session.execute(
                text("UPDATE org_memberships SET status = 'deactivated' WHERE user_id = :u"), {'u': ada}
            )
            await session.commit()
        stranger = ProducerCaller(kind='person', id='nobody-here', org_id=acme.org_id)

        inactive = await CloudDiscoveries(licence).of(ProducerCaller(kind='person', id=ada, org_id=acme.org_id))
        nowhere = await CloudDiscoveries(licence).of(stranger)

        assert contract.problems(inactive.model_dump(mode='json')) == []
        assert contract.problems(nowhere.model_dump(mode='json')) == []

    async def test_not_licensed_matches(self, client, acme, fleet, licence, licence_path, clock, contract):
        licence_path.unlink()
        clock.advance(RECHECK)
        _, session = await acme.person('ada@acme.com', await acme.group('Legal'))

        answer = await _cloud(client, session)

        assert answer['reason'] == 'not_licensed'
        assert contract.problems(answer) == []

    async def test_the_refusals_match(self, client, acme, fleet, licence, contract):
        legal = await acme.group('Legal')
        _, session = await acme.person('ada@acme.com', legal)
        await _set_mode(client, acme, legal, 'local')
        app = await acme.app()

        forbidden = await client.put('/me/cloud/events-owner', json={'owner': 'cloud'}, headers=session)
        not_a_person = await client.get('/me/cloud', headers=await acme.app_token(app))
        nobody = await client.get('/me/cloud')

        assert forbidden.status_code == 409
        assert contract.defined('ModeForbids').problems(forbidden.json()) == []
        assert (not_a_person.status_code, nobody.status_code) == (403, 401)
        assert contract.defined('Refusal').problems(not_a_person.json()) == []
        assert contract.defined('Refusal').problems(nobody.json()) == []

    def test_the_put_body_is_the_contracts(self, contract):
        body = contract.defined('EventsOwnerRequest')

        assert body.problems(SetEventsOwner(owner='cloud').model_dump(mode='json')) == []
        assert body.problems({'owner': 'both'})

    def test_the_served_model_names_the_same_fields_and_values(self, app):
        document = json.loads(CONTRACT.read_text())['$defs']
        served = app.openapi()['components']['schemas']
        model = served[CloudDiscovery.__name__]
        wanted = document['CloudDiscovery']

        assert list(model['properties']) == list(wanted['properties'])
        assert sorted(model['required']) == sorted(wanted['required'])
        for name, schema in (
            ('cloud', 'Cloud'),
            ('reason', 'Reason'),
            ('mode', 'Mode'),
            ('events_owner', 'EventsOwner'),
        ):
            assert _values(model['properties'][name], served) == _values(document[schema], document), name


def _values(spec: dict, components: dict) -> set[object]:
    """The values a property allows: its enum, or its branches' (`anyOf`, `oneOf`, a `$ref`, `null`)."""
    if '$ref' in spec:
        return _values(components[spec['$ref'].rsplit('/', 1)[-1]], components)
    if 'enum' in spec:
        return set(spec['enum'])
    if 'const' in spec:
        return {spec['const']}
    if spec.get('type') == 'null':
        return {None}
    found: set[object] = set()
    for branch in [*spec.get('anyOf', []), *spec.get('oneOf', [])]:
        found |= _values(branch, components)
    return found
