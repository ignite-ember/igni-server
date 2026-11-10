"""SCIM 2.0: the identity provider does the deprovisioning.

Deactivation works and somebody has to remember to do it. An
administrator removes a person in Okta or Entra and nothing here
hears about it — so the account they removed keeps working until a
human repeats the action in a second place, which is the step that
gets forgotten on the day it matters.

`active: false` is the event this exists to receive. Everything under
it already existed: `AccountSessionsDB.set_deactivated` records the fact and
withdraws the person's sessions in the same statement, and the door
refuses them from the next request. SCIM is the vocabulary, not the
mechanism.

Two decisions worth stating, because a provider's behaviour should
not decide them:

* **DELETE deactivates; it does not delete.** Providers differ about
  which verb a removal sends, and a departed colleague's documents —
  and everything built on them — must not depend on that choice.
* **An unfiltered listing answers empty.** A provider asks "do you
  have this person" to decide whether to create them; none of them
  needs the staff list to do it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from tests.conftest import auth_header, create_user, make_token, TEST_DB_URL_SYNC

pytestmark = pytest.mark.anyio

SCIM = 'http://test/scim/v2'
TOKEN = 'scim-token-for-tests'
USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'


@pytest.fixture(autouse=True)
def _scim_configured(monkeypatch):
    """SCIM on, and an organisation owning the domain it provisions on.

    A person joins the organisation that owns their address's domain;
    SCIM is no exception. The rule itself is in
    tests/test_a_person_belongs_to_their_domains_organisation.py.
    """
    monkeypatch.setattr(settings, 'scim_token', TOKEN)
    # Synchronously, like `_is_deactivated` below: an async fixture here
    # runs on a different event loop from the tests that also use `db`.
    org_id = str(uuid.uuid4())
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO organizations (id, slug, display_name, owner_user_id, created_at, updated_at) '
                    "VALUES (:id, 'directory', 'Directory', 'directory-owner', now(), now())"
                ),
                {'id': org_id},
            )
            conn.execute(
                text("INSERT INTO org_domains (id, org_id, domain, created_at) VALUES (:id, :org, 'acme.test', now())"),
                {'id': str(uuid.uuid4()), 'org': org_id},
            )
    finally:
        engine.dispose()


def _head(token: str = TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _is_deactivated(user_id: str) -> bool:
    engine = create_engine(TEST_DB_URL_SYNC)
    try:
        with engine.begin() as conn:
            row = conn.execute(text('SELECT deactivated_at FROM users WHERE user_id = :u'), {'u': user_id}).first()
    finally:
        engine.dispose()
    return row is not None and row[0] is not None


def _person(**over) -> dict:
    return {
        'schemas': [USER_SCHEMA],
        'userName': f'sam-{uuid.uuid4().hex[:8]}@acme.test',
        'name': {'givenName': 'Sam', 'familyName': 'Rivera'},
        'displayName': 'Sam Rivera',
        'active': True,
        **over,
    }


class TestWhoMayCall:
    async def test_without_a_token_nothing_answers(self, client):
        response = await client.get(f'{SCIM}/Users?filter=userName eq "x@y.com"')

        assert response.status_code == 401
        # SCIM's own error shape: a provider given FastAPI's `detail`
        # logs an unparseable response and tells nobody anything.
        assert 'urn:ietf:params:scim:api:messages:2.0:Error' in response.text

    async def test_a_wrong_token_is_refused(self, client):
        response = await client.get(f'{SCIM}/Users', headers=_head('not-the-token'))

        assert response.status_code == 401

    async def test_a_user_token_is_not_a_scim_token(self, client, db):
        # The provisioning credential is the provider's, not a
        # person's — one tied to an employee stops working the day
        # that employee leaves, which is the event it must survive.
        user = await create_user(db, email=f'u-{uuid.uuid4().hex[:8]}@acme.test')

        response = await client.get(f'{SCIM}/Users', headers=auth_header(make_token(str(user.user_id))))

        assert response.status_code == 401

    async def test_unconfigured_means_disabled_not_open(self, client, monkeypatch):
        # Fails closed: the alternative is a deployment that accepts
        # unauthenticated requests to create and remove accounts
        # because somebody forgot an environment variable.
        monkeypatch.setattr(settings, 'scim_token', '')

        response = await client.get(f'{SCIM}/Users', headers=_head())

        assert response.status_code == 503


class TestProvisioning:
    async def test_a_provider_can_create_somebody(self, client):
        payload = _person()

        response = await client.post(f'{SCIM}/Users', json=payload, headers=_head())

        assert response.status_code == 201, response.text
        body = response.json()
        assert body['userName'] == payload['userName']
        assert body['active'] is True
        assert body['id']

    async def test_creating_the_same_person_twice_says_uniqueness(self, client):
        # 409 with `uniqueness` tells a provider to reconcile. A 400
        # makes it retry, which is a sync loop.
        payload = _person()
        await client.post(f'{SCIM}/Users', json=payload, headers=_head())

        again = await client.post(f'{SCIM}/Users', json=payload, headers=_head())

        assert again.status_code == 409
        assert again.json()['detail']['scimType'] == 'uniqueness'

    async def test_attributes_we_do_not_store_do_not_fail_the_sync(self, client):
        # A provider sending phone numbers to a server that has none
        # should provision the user, not make an administrator go and
        # look at a failed run.
        payload = _person(phoneNumbers=[{'value': '+1 555 0100'}], title='Staff Engineer')

        response = await client.post(f'{SCIM}/Users', json=payload, headers=_head())

        assert response.status_code == 201, response.text

    async def test_somebody_created_already_inactive_stays_that_way(self, client):
        # A leaver synced for the first time.
        response = await client.post(f'{SCIM}/Users', json=_person(active=False), headers=_head())

        assert response.status_code == 201
        assert response.json()['active'] is False
        assert _is_deactivated(response.json()['id'])


class TestFindingSomebody:
    async def test_a_provider_looks_them_up_by_address(self, client):
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        found = await client.get(
            f'{SCIM}/Users', params={'filter': f'userName eq "{created["userName"]}"'}, headers=_head()
        )

        assert found.status_code == 200
        assert found.json()['totalResults'] == 1
        assert found.json()['Resources'][0]['id'] == created['id']

    async def test_somebody_who_is_not_here_is_an_empty_list_not_a_404(self, client):
        found = await client.get(f'{SCIM}/Users', params={'filter': 'userName eq "nobody@acme.test"'}, headers=_head())

        assert found.status_code == 200
        assert found.json()['totalResults'] == 0

    async def test_an_unfiltered_listing_does_not_hand_over_the_directory(self, client):
        await client.post(f'{SCIM}/Users', json=_person(), headers=_head())

        found = await client.get(f'{SCIM}/Users', headers=_head())

        assert found.status_code == 200
        assert found.json()['totalResults'] == 0
        assert found.json()['Resources'] == []

    async def test_a_filter_we_cannot_answer_is_refused_not_ignored(self, client):
        # Ignoring it would match everything, which is the opposite of
        # what was asked.
        found = await client.get(f'{SCIM}/Users', params={'filter': 'displayName sw "Sa"'}, headers=_head())

        assert found.status_code == 400
        assert found.json()['detail']['scimType'] == 'invalidFilter'


class TestDeprovisioning:
    """The event this whole resource exists to receive."""

    async def test_entra_style_patch_deactivates(self, client):
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        response = await client.patch(
            f'{SCIM}/Users/{created["id"]}',
            json={
                'schemas': ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
                'Operations': [{'op': 'Replace', 'path': 'active', 'value': False}],
            },
            headers=_head(),
        )

        assert response.status_code == 200, response.text
        assert response.json()['active'] is False
        assert _is_deactivated(created['id'])

    async def test_okta_style_patch_deactivates_too(self, client):
        # No path, value as an object. Supporting one spelling would
        # mean supporting one provider.
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        response = await client.patch(
            f'{SCIM}/Users/{created["id"]}',
            json={
                'schemas': ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
                'Operations': [{'op': 'replace', 'value': {'active': False}}],
            },
            headers=_head(),
        )

        assert response.json()['active'] is False
        assert _is_deactivated(created['id'])

    async def test_the_deprovisioned_person_cannot_use_their_token(self, client):
        # The point of all of it. The account stops working on the
        # next request rather than when a token happens to expire.
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()
        token = make_token(created['id'])
        assert (await client.get('/notifications', headers=auth_header(token))).status_code == 200

        await client.patch(
            f'{SCIM}/Users/{created["id"]}',
            json={'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]},
            headers=_head(),
        )

        assert (await client.get('/notifications', headers=auth_header(token))).status_code == 401

    async def test_a_patch_about_something_else_is_not_a_deprovisioning(self, client):
        # Providers sync display names. Reading that as "they left"
        # would deactivate people for being renamed.
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        response = await client.patch(
            f'{SCIM}/Users/{created["id"]}',
            json={'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'Sam R'}]},
            headers=_head(),
        )

        assert response.json()['active'] is True
        assert not _is_deactivated(created['id'])

    async def test_delete_deactivates_and_keeps_their_work(self, client):
        # A provider's choice of verb must not decide whether a
        # departed colleague's documents survive.
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        response = await client.delete(f'{SCIM}/Users/{created["id"]}', headers=_head())

        assert response.status_code == 204
        assert _is_deactivated(created['id'])
        # Still there, deactivated — not removed.
        still = await client.get(f'{SCIM}/Users/{created["id"]}', headers=_head())
        assert still.status_code == 200
        assert still.json()['active'] is False

    async def test_putting_them_back_active_reactivates(self, client):
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()
        await client.delete(f'{SCIM}/Users/{created["id"]}', headers=_head())

        response = await client.put(
            f'{SCIM}/Users/{created["id"]}',
            json=_person(userName=created['userName'], active=True),
            headers=_head(),
        )

        assert response.status_code == 200
        assert response.json()['active'] is True
        assert not _is_deactivated(created['id'])


class TestWhatAProviderAsksFirst:
    async def test_the_service_provider_config_is_readable(self, client):
        response = await client.get(f'{SCIM}/ServiceProviderConfig', headers=_head())

        assert response.status_code == 200
        assert response.json()['patch']['supported'] is True

    async def test_it_says_plainly_what_is_not_supported(self, client):
        # A provider configured to push groups should read that here
        # rather than discover it by syncing nothing.
        config = (await client.get(f'{SCIM}/ServiceProviderConfig', headers=_head())).json()

        assert config['bulk']['supported'] is False
        assert config['changePassword']['supported'] is False


class TestWhatIsRefusedAndHow:
    """In SCIM's own error shape, with the status a provider acts on.

    A provider reads `status` and `scimType` to decide whether to retry,
    reconcile or give up, so each refusal is pinned to what it says.
    """

    @staticmethod
    def _scim_error(response, status: int) -> dict:
        assert response.status_code == status, response.text
        body = response.json()['detail']
        assert body['status'] == str(status)
        assert body['schemas'] == ['urn:ietf:params:scim:api:messages:2.0:Error']
        return body

    async def test_somebody_with_no_address_is_not_created(self, client):
        """A login name with no `@` and no email: nothing to key an account on."""
        response = await client.post(f'{SCIM}/Users', json=_person(userName='sam.rivera', emails=[]), headers=_head())

        body = self._scim_error(response, 400)
        assert body['scimType'] == 'invalidValue'

    async def test_the_primary_email_is_used_when_the_login_name_is_not_an_address(self, client):
        payload = _person(
            userName='sam.rivera',
            emails=[
                {'value': 'other@acme.test', 'primary': False},
                {'value': 'Sam.Rivera@acme.test', 'primary': True},
            ],
        )

        response = await client.post(f'{SCIM}/Users', json=payload, headers=_head())

        assert response.status_code == 201, response.text
        assert response.json()['userName'] == 'sam.rivera@acme.test'

    @pytest.mark.parametrize(
        ('method', 'json'),
        [
            ('GET', None),
            ('PATCH', {'schemas': ['urn:ietf:params:scim:api:messages:2.0:PatchOp'], 'Operations': []}),
            ('PUT', _person()),
            ('DELETE', None),
        ],
    )
    async def test_somebody_who_is_not_here_is_a_404_on_every_verb(self, client, method, json):
        nobody = f'nobody-{uuid.uuid4().hex[:8]}'

        response = await client.request(method, f'{SCIM}/Users/{nobody}', json=json, headers=_head())

        body = self._scim_error(response, 404)
        assert body['detail'] == 'User not found'

    async def test_a_replace_that_changes_nothing_changes_nothing(self, client):
        """A provider re-sends the whole record on every sync. Re-sending
        `active: true` for somebody active is not a reactivation, and an
        attribute this server does not store is not a failure."""
        created = (await client.post(f'{SCIM}/Users', json=_person(), headers=_head())).json()

        response = await client.put(
            f'{SCIM}/Users/{created["id"]}',
            json=_person(userName=created['userName'], active=True, displayName='Samantha Rivera'),
            headers=_head(),
        )

        assert response.status_code == 200
        assert response.json()['active'] is True
        assert response.json()['userName'] == created['userName']
        assert not _is_deactivated(created['id'])
