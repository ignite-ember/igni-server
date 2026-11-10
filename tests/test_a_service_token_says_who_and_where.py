"""What the token endpoint hands out, to whom, and when it refuses.

Every token is for one organisation, says what it is, and says who it is
for. A service never asks igni whether a person is still allowed: it
holds a ten-minute token that says so, and the renewal is where igni
refuses somebody deleted, deactivated, removed or withdrawn.
"""

from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
import uuid

import jwt
import pytest

from app.db.sql.account_sessions import AccountSessionsDB
from app.db.sql.team import TeamDB
from app.db.sql.user import UserDB
from app.security.api_key_sessions import REFUSED as API_KEY_SESSION_REFUSED
from app.services.auth.jwt_service import JWTService, TokenUse
from app.services.auth.token_claims import CustomClaims
from app.services.identity.assertions import ASSERTION_TYPE
from app.services.identity.jwk import RsaKeyPair
from app.services.identity.token_requests import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE
from tests.conftest import add_member, auth_header, create_org, create_user, make_token
from tests.registered_service import REDIRECT_URI, RegisteredService

pytestmark = pytest.mark.anyio

VERIFIER = 'a-code-verifier-that-is-long-enough-to-satisfy-rfc-7636-limits'
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()


def _claims(token: str) -> dict:
    return jwt.decode(token, options={'verify_signature': False})


async def _token(client, form: dict):
    return await client.post('/oauth/token', data=form)


def _unsigned(service: RegisteredService) -> str:
    """`service`'s own claims, with `alg: none` and no signature at all."""
    signed = service.assertion()

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()

    header = {**jwt.get_unverified_header(signed), 'alg': 'none'}
    return f'{part(header)}.{part(_claims(signed))}.'


#: Assertions that name a client but were not signed with its key.
_FORGERIES = {
    'unsigned': _unsigned,
    'wrong key': lambda service: service.assertion(key=RsaKeyPair.generate()),
}


async def _client_in(state: str) -> RegisteredService:
    service = RegisteredService(name=f'In state {state}')
    await service.approve(state=state)
    return service


@pytest.fixture
async def world(db):
    """An organisation with an admin and a member, and an approved service."""
    admin = await create_user(db, email='admin@acme.com', full_name='Ada Admin')
    member = await create_user(db, email='member@acme.com', full_name='Max Member')
    org = await create_org(db, owner_user_id=str(admin.user_id), slug='acme', display_name='Acme')
    await add_member(db, str(org.id), str(member.user_id))
    other = await create_user(db, email='someone@globex.com')
    globex = await create_org(db, owner_user_id=str(other.user_id), slug='globex', display_name='Globex')
    service = RegisteredService()
    await service.approve()
    return {
        'org': str(org.id),
        'globex': str(globex.id),
        'admin': str(admin.user_id),
        'member': str(member.user_id),
        'service': service,
    }


class TestAServiceActingAsItself:
    async def test_it_gets_a_token_for_one_organisation(self, client, world):
        service = world['service']

        response = await _token(client, service.authenticated('client_credentials', organisation=world['org']))

        assert response.status_code == 200, response.text
        assert 'no-store' in response.headers['cache-control']
        claims = _claims(response.json()['access_token'])
        assert claims['sub'] == service.client_id
        assert claims['aud'] == JWTService().issuer
        assert claims['token_use'] == 'service'
        assert claims['org'] == world['org']
        assert response.json()['expires_in'] == 600

    async def test_the_organisation_is_required(self, client, world):
        response = await _token(client, world['service'].authenticated('client_credentials'))

        assert response.status_code == 400
        assert response.json()['error'] == 'invalid_request'

    async def test_an_unknown_organisation_is_refused(self, client, world):
        response = await _token(client, world['service'].authenticated('client_credentials', organisation='nope'))

        assert response.json()['error'] == 'invalid_target'

    async def test_an_app_acts_only_in_its_own_organisation(self, client, db, world):
        app = RegisteredService(kind='app', name='Review bot', base_url='', org_id=world['org'])
        await app.approve()

        response = await _token(client, app.authenticated('client_credentials', organisation=world['globex']))

        assert response.json()['error'] == 'invalid_target'


class TestClientAuthentication:
    async def test_a_pending_client_is_told_it_is_waiting(self, client, db, world):
        pending = RegisteredService(name='Waiting')
        await pending.approve(state='pending')

        response = await _token(client, pending.authenticated('client_credentials', organisation=world['org']))

        assert response.status_code == 401
        assert 'waiting for a super admin' in response.json()['error_description']

    async def test_a_pending_client_is_told_its_state_without_reading_the_prose(self, client, db, world):
        """An SDK tells "wait" from "give up" by `client_state`, the registration's own `state`."""
        pending = RegisteredService(name='Waiting')
        await pending.approve(state='pending')

        response = await _token(client, pending.authenticated('client_credentials', organisation=world['org']))

        assert response.status_code == 401
        assert set(response.json()) == {'error', 'error_description', 'client_state'}
        assert response.json()['error'] == 'invalid_client'
        assert response.json()['client_state'] == 'pending'

    async def test_a_revoked_client_is_refused(self, client, db, world):
        revoked = RegisteredService(name='Gone')
        await revoked.approve(state='revoked')

        response = await _token(client, revoked.authenticated('client_credentials', organisation=world['org']))

        assert response.status_code == 401
        assert response.json()['error'] == 'invalid_client'
        assert set(response.json()) == {'error', 'error_description', 'client_state'}
        assert response.json()['client_state'] == 'revoked'

    async def test_an_unknown_client_is_told_no_state(self, client, world):
        stranger = RegisteredService(name='Never registered')
        stranger.client_id = str(uuid.uuid4())

        response = await _token(client, stranger.authenticated('client_credentials', organisation=world['org']))

        assert response.status_code == 401
        assert response.json() == {'error': 'invalid_client', 'error_description': 'no such client'}

    @pytest.mark.parametrize('forgery', sorted(_FORGERIES))
    @pytest.mark.parametrize('state', ['pending', 'revoked'])
    async def test_only_a_caller_holding_the_key_learns_the_state(self, client, db, world, state, forgery):
        """An assertion that does not verify is refused as it would be for an
        approved client: no `client_state`, no wording that says waiting or
        revoked. Otherwise anybody who knew a client id could ask."""
        forge = _FORGERIES[forgery]
        approved, other = world['service'], await _client_in(state)
        fields = {'grant_type': 'client_credentials', 'organisation': world['org']}

        for_approved = await _token(client, {**approved.authenticated(**fields), 'client_assertion': forge(approved)})
        for_other = await _token(client, {**other.authenticated(**fields), 'client_assertion': forge(other)})

        assert for_other.status_code == for_approved.status_code == 401
        assert for_other.json() == for_approved.json()
        assert 'client_state' not in for_other.json()
        assert 'waiting' not in for_other.text and 'revoked' not in for_other.text

    @pytest.mark.parametrize('forgery', sorted(_FORGERIES))
    async def test_an_unknown_client_learns_no_state_either(self, client, world, forgery):
        stranger = RegisteredService(name='Never registered')
        stranger.client_id = str(uuid.uuid4())
        form = stranger.authenticated('client_credentials', organisation=world['org'])

        response = await _token(client, {**form, 'client_assertion': _FORGERIES[forgery](stranger)})

        assert response.status_code == 401
        assert response.json() == {'error': 'invalid_client', 'error_description': 'no such client'}

    @pytest.mark.parametrize('state', ['pending', 'revoked'])
    async def test_the_revocation_endpoint_tells_the_state_the_same_way(self, client, db, world, state):
        """`/oauth/revoke` authenticates the client with the same code."""
        other = await _client_in(state)

        def revoking(assertion: str) -> dict:
            return {'token': 'anything', 'client_assertion_type': ASSERTION_TYPE, 'client_assertion': assertion}

        signed = await client.post('/oauth/revoke', data=revoking(other.assertion()))
        forged = await client.post('/oauth/revoke', data=revoking(other.assertion(key=RsaKeyPair.generate())))

        assert signed.status_code == forged.status_code == 401
        assert signed.json()['client_state'] == state
        assert set(forged.json()) == {'error', 'error_description'}
        assert 'did not verify' in forged.json()['error_description']

    async def test_an_assertion_is_used_once(self, client, world):
        service = world['service']
        form = service.authenticated('client_credentials', organisation=world['org'])

        first = await _token(client, form)
        replay = await _token(client, form)

        assert first.status_code == 200
        assert replay.status_code == 401
        assert 'already been used' in replay.json()['error_description']
        assert 'client_state' not in replay.json()

    async def test_an_assertion_signed_with_another_key(self, client, world):
        service = world['service']
        form = {
            **service.authenticated('client_credentials', organisation=world['org']),
            'client_assertion': service.assertion(key=RsaKeyPair.generate()),
        }

        response = await _token(client, form)

        assert response.json()['error'] == 'invalid_client'
        assert set(response.json()) == {'error', 'error_description'}

    async def test_an_assertion_for_another_server(self, client, world):
        service = world['service']
        form = {
            **service.authenticated('client_credentials', organisation=world['org']),
            'client_assertion': service.assertion(audience='https://elsewhere.example.com'),
        }

        response = await _token(client, form)

        assert response.json()['error'] == 'invalid_client'

    async def test_a_long_lived_assertion(self, client, world):
        """One assertion per request; a long one is only a longer replay window."""
        service = world['service']
        form = {
            **service.authenticated('client_credentials', organisation=world['org']),
            'client_assertion': service.assertion(lifetime=timedelta(hours=1)),
        }

        response = await _token(client, form)

        assert 'at most 300 seconds' in response.json()['error_description']

    async def test_no_assertion_at_all(self, client, world):
        response = await _token(client, {'grant_type': 'client_credentials', 'organisation': world['org']})

        assert response.status_code == 401
        assert set(response.json()) == {'error', 'error_description'}

    async def test_an_unknown_grant(self, client):
        response = await _token(client, {'grant_type': 'password'})

        assert response.json()['error'] == 'unsupported_grant_type'


class TestTheCliTradesItsSession:
    def _exchange(self, world, session: str, **extra: str) -> dict:
        return {
            'grant_type': TOKEN_EXCHANGE,
            'subject_token': session,
            'subject_token_type': ACCESS_TOKEN_TYPE,
            'audience': world['service'].base_url,
            **extra,
        }

    async def test_it_gets_a_token_only_that_service_accepts(self, client, world):
        session = make_token(world['member'], org_id=world['org'])

        response = await _token(client, self._exchange(world, session))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['issued_token_type'] == ACCESS_TOKEN_TYPE
        assert 'refresh_token' not in body
        claims = _claims(body['access_token'])
        assert claims['aud'] == 'https://codeindex.example.com'
        assert claims['token_use'] == 'user'
        assert claims['sub'] == world['member']
        assert claims['org'] == world['org']
        assert claims['org_role'] == 'member'
        assert claims['email'] == 'member@acme.com'
        assert claims['name'] == 'Max Member'
        assert claims['super_admin'] is False

    async def test_that_token_is_not_a_session_here(self, client, world):
        session = make_token(world['member'], org_id=world['org'])
        access = (await _token(client, self._exchange(world, session))).json()['access_token']

        assert JWTService().decode_token(access).user_id is None

    async def test_an_unpinned_session_names_the_organisation(self, client, world):
        session = make_token(world['member'])

        missing = await _token(client, self._exchange(world, session))
        named = await _token(client, self._exchange(world, session, organisation=world['org']))

        assert missing.json()['error'] == 'invalid_request'
        assert named.status_code == 200

    async def test_not_an_organisation_they_are_not_in(self, client, world):
        session = make_token(world['member'])

        response = await _token(client, self._exchange(world, session, organisation=world['globex']))

        assert response.json()['error'] == 'invalid_grant'

    async def test_a_pinned_session_cannot_switch_organisation(self, client, world):
        session = make_token(world['admin'], org_id=world['org'])

        response = await _token(client, self._exchange(world, session, organisation=world['globex']))

        assert response.json()['error'] == 'invalid_target'

    async def test_the_audience_is_a_service_url(self, client, world):
        session = make_token(world['member'], org_id=world['org'])

        response = await _token(client, {**self._exchange(world, session), 'audience': 'not-a-url'})

        assert response.json()['error'] == 'invalid_target'

    async def test_any_service_url_needs_no_registration(self, client, world):
        """The session is the credential: whoever holds it has everything
        already, so naming an unregistered service grants nothing new.
        The URL only says which service will accept the token."""
        session = make_token(world['member'], org_id=world['org'])

        response = await _token(
            client, {**self._exchange(world, session), 'audience': 'https://Elsewhere.example.com/'}
        )

        assert response.status_code == 200, response.text
        assert _claims(response.json()['access_token'])['aud'] == 'https://elsewhere.example.com'

    async def test_a_withdrawn_session_gets_nothing(self, client, world):
        session = make_token(world['member'], org_id=world['org'])
        await AccountSessionsDB().withdraw_sessions(world['member'])

        response = await _token(client, self._exchange(world, session))

        assert response.json()['error'] == 'invalid_grant'

    async def test_a_deleted_person_gets_nothing(self, client, world):
        session = make_token(world['member'], org_id=world['org'])
        await UserDB().delete(world['member'])

        response = await _token(client, self._exchange(world, session))

        assert response.json()['error'] == 'invalid_grant'

    async def test_a_token_for_a_service_cannot_be_exchanged(self, client, world):
        """Only an igni session is a subject token."""
        tokens = JWTService()
        for_service = tokens.sign(
            subject=world['member'],
            audience=world['service'].client_id,
            use=TokenUse.USER,
            lifetime=timedelta(minutes=5),
            claims=CustomClaims(org=world['org']),
        )

        response = await _token(client, self._exchange(world, for_service))

        assert response.json()['error'] == 'invalid_grant'


class TestSigningInToAServicesPortal:
    async def _code(self, client, world, *, user: str = 'member', state: str = 'xyz') -> str:
        headers = auth_header(make_token(world[user]))
        response = await client.post(
            '/portal/oauth/authorize',
            headers=headers,
            json={
                'client_id': world['service'].client_id,
                'redirect_uri': REDIRECT_URI,
                'organisation': world['org'],
                'code_challenge': CHALLENGE,
                'state': state,
            },
        )
        assert response.status_code == 200, response.text
        redirect = urlsplit(response.json()['redirect_to'])
        query = parse_qs(redirect.query)
        assert f'{redirect.scheme}://{redirect.netloc}{redirect.path}' == REDIRECT_URI
        assert query['state'] == [state]
        return query['code'][0]

    def _redeem(self, world, code: str, **overrides: str) -> dict:
        return world['service'].authenticated(
            'authorization_code',
            code=code,
            redirect_uri=REDIRECT_URI,
            code_verifier=VERIFIER,
            **overrides,
        )

    async def test_the_portal_says_who_is_asking(self, client, world):
        response = await client.get(
            '/portal/oauth/request',
            params={'client_id': world['service'].client_id, 'redirect_uri': REDIRECT_URI},
            headers=auth_header(make_token(world['member'])),
        )

        assert response.json() == {
            'client_name': 'CodeIndex',
            'organisations': [{'org_id': world['org'], 'name': 'Acme'}],
        }

    async def test_an_unregistered_redirect_is_refused_not_followed(self, client, world):
        response = await client.get(
            '/portal/oauth/request',
            params={'client_id': world['service'].client_id, 'redirect_uri': 'https://evil.example.com/cb'},
            headers=auth_header(make_token(world['member'])),
        )

        assert (response.status_code, response.json()['detail']) == (
            400,
            'This redirect address was not registered by the application',
        )

    async def test_a_client_not_yet_approved_is_not_described(self, client, world):
        pending = RegisteredService(name='Waiting')
        await pending.approve(state='pending')

        response = await client.get(
            '/portal/oauth/request',
            params={'client_id': pending.client_id, 'redirect_uri': REDIRECT_URI},
            headers=auth_header(make_token(world['member'])),
        )

        assert (response.status_code, response.json()['detail']) == (404, 'This application is not approved here')

    async def test_a_session_an_api_key_bought_may_still_see_who_is_asking(self, client, world):
        session = JWTService().create_token(world['member'], api_key_id='key-1')

        response = await client.get(
            '/portal/oauth/request',
            params={'client_id': world['service'].client_id, 'redirect_uri': REDIRECT_URI},
            headers=auth_header(session),
        )

        assert response.status_code == 200, response.text
        assert response.json()['client_name'] == 'CodeIndex'

    async def test_a_person_gets_tokens_for_the_service(self, client, world):
        code = await self._code(client, world)

        response = await _token(client, self._redeem(world, code))

        assert response.status_code == 200, response.text
        body = response.json()
        access = _claims(body['access_token'])
        assert access['aud'] == 'https://codeindex.example.com'
        assert access['azp'] == world['service'].client_id
        assert access['sub'] == world['member']
        assert access['org'] == world['org']
        refresh = _claims(body['refresh_token'])
        assert refresh['token_use'] == 'refresh'
        assert refresh['aud'] == JWTService().issuer

    async def test_a_code_is_used_once(self, client, world):
        code = await self._code(client, world)
        await _token(client, self._redeem(world, code))

        replay = await _token(client, self._redeem(world, code))

        assert replay.json()['error'] == 'invalid_grant'

    async def test_the_verifier_must_match(self, client, world):
        code = await self._code(client, world)

        response = await _token(client, {**self._redeem(world, code), 'code_verifier': 'x' * 64})

        assert response.json()['error'] == 'invalid_grant'

    async def test_another_client_cannot_redeem_it(self, client, db, world):
        code = await self._code(client, world)
        other = RegisteredService(name='Other')
        await other.approve()

        response = await _token(
            client,
            other.authenticated('authorization_code', code=code, redirect_uri=REDIRECT_URI, code_verifier=VERIFIER),
        )

        assert response.json()['error'] == 'invalid_grant'

    async def test_not_for_an_organisation_they_are_not_in(self, client, world):
        response = await client.post(
            '/portal/oauth/authorize',
            headers=auth_header(make_token(world['member'])),
            json={
                'client_id': world['service'].client_id,
                'redirect_uri': REDIRECT_URI,
                'organisation': world['globex'],
                'code_challenge': CHALLENGE,
            },
        )

        assert (response.status_code, response.json()['detail']) == (403, 'Not a member of this organisation')

    async def _authorize(self, client, world, *, token: str | None = None, **overrides: str):
        body = {
            'client_id': world['service'].client_id,
            'redirect_uri': REDIRECT_URI,
            'organisation': world['org'],
            'code_challenge': CHALLENGE,
            **overrides,
        }
        return await client.post(
            '/portal/oauth/authorize',
            headers=auth_header(token or make_token(world['member'])),
            json=body,
        )

    async def test_no_code_for_a_client_not_yet_approved(self, client, world):
        pending = RegisteredService(name='Waiting')
        await pending.approve(state='pending')

        response = await self._authorize(client, world, client_id=pending.client_id)

        assert (response.status_code, response.json()['detail']) == (404, 'This application is not approved here')

    async def test_no_code_for_an_unregistered_redirect(self, client, world):
        response = await self._authorize(client, world, redirect_uri='https://evil.example.com/cb')

        assert (response.status_code, response.json()['detail']) == (
            400,
            'This redirect address was not registered by the application',
        )

    async def test_no_code_for_a_plain_challenge(self, client, world):
        response = await self._authorize(client, world, code_challenge=VERIFIER, code_challenge_method='plain')

        assert (response.status_code, response.json()['detail']) == (400, 'Only S256 code challenges are accepted')

    async def test_no_code_for_a_session_an_api_key_bought(self, client, world):
        session = JWTService().create_token(world['member'], api_key_id='key-1')

        response = await self._authorize(client, world, token=session)

        assert (response.status_code, response.json()['detail']) == (403, API_KEY_SESSION_REFUSED)

    @pytest.mark.parametrize(
        'overrides',
        [
            {'code_challenge': 'c' * 42},
            {'code_challenge': 'c' * 129},
            {'state': 's' * 1001},
        ],
        ids=['challenge-too-short', 'challenge-too-long', 'state-too-long'],
    )
    async def test_a_challenge_or_state_out_of_bounds_is_not_accepted(self, client, world, overrides):
        response = await self._authorize(client, world, **overrides)

        assert response.status_code == 422

    async def test_the_longest_challenge_and_state_are_accepted(self, client, world):
        response = await self._authorize(client, world, code_challenge='c' * 128, state='s' * 1000)

        assert response.status_code == 200, response.text


class TestRenewalIsWhereRevocationArrives:
    async def _refresh_token(self, client, world) -> str:
        flow = TestSigningInToAServicesPortal()
        code = await flow._code(client, world)
        return (await _token(client, flow._redeem(world, code))).json()['refresh_token']

    def _renew(self, world, refresh: str) -> dict:
        return world['service'].authenticated('refresh_token', refresh_token=refresh)

    async def test_it_renews(self, client, world):
        refresh = await self._refresh_token(client, world)

        response = await _token(client, self._renew(world, refresh))

        assert response.status_code == 200
        assert _claims(response.json()['access_token'])['sub'] == world['member']

    async def test_a_renewal_stays_in_the_same_sign_in(self, client, world):
        refresh = await self._refresh_token(client, world)

        renewed = (await _token(client, self._renew(world, refresh))).json()['refresh_token']

        first, second = _claims(refresh), _claims(renewed)
        assert first['grant'] == second['grant']
        assert first['jti'] != second['jti']

    async def test_not_once_deactivated(self, client, world):
        refresh = await self._refresh_token(client, world)
        await AccountSessionsDB().set_deactivated(world['member'], deactivated=True)

        response = await _token(client, self._renew(world, refresh))

        assert response.json()['error'] == 'invalid_grant'

    async def test_not_once_removed_from_the_organisation(self, client, world):
        refresh = await self._refresh_token(client, world)
        await TeamDB().update_status(world['org'], user_id=world['member'], status='deactivated')

        response = await _token(client, self._renew(world, refresh))

        assert response.json()['error'] == 'invalid_grant'

    async def test_not_once_their_sessions_are_withdrawn(self, client, world):
        refresh = await self._refresh_token(client, world)
        await AccountSessionsDB().withdraw_sessions(world['member'])

        response = await _token(client, self._renew(world, refresh))

        assert response.json()['error'] == 'invalid_grant'

    async def test_not_by_another_client(self, client, db, world):
        refresh = await self._refresh_token(client, world)
        other = RegisteredService(name='Other')
        await other.approve()

        response = await _token(client, other.authenticated('refresh_token', refresh_token=refresh))

        assert response.json()['error'] == 'invalid_grant'


class TestAServiceSigningSomebodyOutRevokesTheSignIn:
    """RFC 7009. A person signing out of a service's pages ends their
    sign-in at igni too: the service revokes the refresh token, and no
    refresh token of that sign-in renews again."""

    async def _signed_in(self, client, world, *, user: str = 'member') -> str:
        flow = TestSigningInToAServicesPortal()
        code = await flow._code(client, world, user=user)
        return (await _token(client, flow._redeem(world, code))).json()['refresh_token']

    @staticmethod
    async def _revoke(client, service: RegisteredService, token: str, **fields: str):
        return await client.post(
            '/oauth/revoke',
            data={
                'token': token,
                'client_assertion_type': ASSERTION_TYPE,
                'client_assertion': service.assertion(),
                **fields,
            },
        )

    @staticmethod
    def _renew(world, refresh: str) -> dict:
        return world['service'].authenticated('refresh_token', refresh_token=refresh)

    async def test_a_revoked_refresh_token_no_longer_renews(self, client, world):
        refresh = await self._signed_in(client, world)

        revoked = await self._revoke(client, world['service'], refresh, token_type_hint='refresh_token')

        assert revoked.status_code == 200, revoked.text
        assert 'no-store' in revoked.headers['cache-control']
        response = await _token(client, self._renew(world, refresh))
        assert response.json()['error'] == 'invalid_grant'

    async def test_every_refresh_token_of_the_sign_in_stops(self, client, world):
        """Renewing does not retire the token it renews, so revoking only the
        latest would leave every earlier one working."""
        first = await self._signed_in(client, world)
        latest = (await _token(client, self._renew(world, first))).json()['refresh_token']

        await self._revoke(client, world['service'], latest)

        assert (await _token(client, self._renew(world, first))).json()['error'] == 'invalid_grant'
        assert (await _token(client, self._renew(world, latest))).json()['error'] == 'invalid_grant'

    async def test_another_sign_in_is_untouched(self, client, world):
        """Signing out on one browser leaves the person signed in on another."""
        here = await self._signed_in(client, world)
        elsewhere = await self._signed_in(client, world)

        await self._revoke(client, world['service'], here)

        assert (await _token(client, self._renew(world, elsewhere))).status_code == 200

    async def test_revoking_twice_is_fine(self, client, world):
        refresh = await self._signed_in(client, world)

        first = await self._revoke(client, world['service'], refresh)
        again = await self._revoke(client, world['service'], refresh)

        assert (first.status_code, again.status_code) == (200, 200)

    async def test_a_token_that_is_not_valid_has_nothing_to_revoke(self, client, world):
        """RFC 7009 §2.2: an invalid token is not an error."""
        response = await self._revoke(client, world['service'], 'not-a-token')

        assert response.status_code == 200, response.text

    async def test_another_clients_refresh_token_is_refused(self, client, world):
        refresh = await self._signed_in(client, world)
        other = RegisteredService(name='Other')
        await other.approve()

        response = await self._revoke(client, other, refresh)

        assert response.json()['error'] == 'invalid_grant'
        assert (await _token(client, self._renew(world, refresh))).status_code == 200

    async def test_an_access_token_cannot_be_revoked(self, client, world):
        flow = TestSigningInToAServicesPortal()
        code = await flow._code(client, world)
        access = (await _token(client, flow._redeem(world, code))).json()['access_token']

        response = await self._revoke(client, world['service'], access, token_type_hint='access_token')

        assert response.status_code == 400
        assert response.json()['error'] == 'unsupported_token_type'

    async def test_the_client_must_authenticate(self, client, world):
        refresh = await self._signed_in(client, world)

        response = await client.post('/oauth/revoke', data={'token': refresh})

        assert response.status_code == 401
        assert response.json()['error'] == 'invalid_client'
        assert (await _token(client, self._renew(world, refresh))).status_code == 200

    async def test_an_assertion_may_name_the_revocation_endpoint(self, client, world):
        refresh = await self._signed_in(client, world)
        service = world['service']

        response = await client.post(
            '/oauth/revoke',
            data={
                'token': refresh,
                'client_assertion_type': ASSERTION_TYPE,
                'client_assertion': service.assertion(audience=f'{JWTService().issuer}/v1/oauth/revoke'),
            },
        )

        assert response.status_code == 200, response.text
        assert (await _token(client, self._renew(world, refresh))).json()['error'] == 'invalid_grant'

    async def test_the_token_is_required(self, client, world):
        response = await self._revoke(client, world['service'], '')

        assert response.json()['error'] == 'invalid_request'

    async def test_a_refresh_token_without_a_sign_in_is_refused(self, client, world):
        """Every refresh token igni issues names its sign-in; one that does
        not could never be revoked, so it does not renew."""
        unnamed = JWTService().sign(
            subject=world['member'],
            audience=JWTService().issuer,
            use=TokenUse.REFRESH,
            lifetime=timedelta(days=1),
            claims=CustomClaims(org=world['org'], azp=world['service'].client_id),
        )

        response = await _token(client, self._renew(world, unnamed))

        assert response.json()['error'] == 'invalid_grant'
