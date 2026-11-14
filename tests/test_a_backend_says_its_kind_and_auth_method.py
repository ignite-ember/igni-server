"""A backend's kind decides where a request goes; its auth method, how it is signed.

`openai` is every backend there was before kinds, and must mean exactly
what it meant. Each kind offers named auth methods (`providers/auth.py`),
and a backend is refused at load time — the static list, a registration,
the portal — when it names a method its kind does not offer, leaves out
what the method needs, or carries what the method would ignore. A refusal
says what is wrong and never echoes a secret.
"""

from __future__ import annotations

import json

import pytest

from app.services.ai.backend import BackendInvalid, BackendLease, InferenceBackend
from app.services.ai.providers.auth import AuthMethod, METHODS
from app.services.ai.providers.credentials import Signer
from app.services.ai.providers.kinds import BackendKind, Wire
from tests.cloud_fakes import AZURE, backend, SERVICE_ACCOUNT, VERTEX


class TestABackendSaysWhatItIs:
    def test_a_backend_written_before_kinds_is_an_openai_one(self):
        written = InferenceBackend(id='gpu', base_url='http://gpu:8000/v1', models=['m'])
        assert (written.kind, written.wire, written.auth) == (BackendKind.OPENAI, Wire.OPENAI, AuthMethod.NONE)

    def test_every_kind_speaks_one_wire(self):
        assert {kind: kind.wire for kind in BackendKind} == {
            BackendKind.OPENAI: Wire.OPENAI,
            BackendKind.AZURE_OPENAI: Wire.OPENAI,
            BackendKind.VERTEX_OPENAI: Wire.OPENAI,
            BackendKind.ANTHROPIC: Wire.ANTHROPIC,
            BackendKind.BEDROCK: Wire.ANTHROPIC,
            BackendKind.VERTEX_ANTHROPIC: Wire.ANTHROPIC,
            BackendKind.AZURE_ANTHROPIC: Wire.ANTHROPIC,
        }

    def test_a_vertex_backend_is_addressed_by_its_project_and_region(self):
        assert backend(**VERTEX).base_url == (
            'https://europe-west4-aiplatform.googleapis.com/v1/projects/p1/locations/europe-west4/endpoints/openapi'
        )

    def test_the_global_region_has_no_regional_host(self):
        assert backend(**{**VERTEX, 'region': 'global'}).base_url == (
            'https://aiplatform.googleapis.com/v1/projects/p1/locations/global/endpoints/openapi'
        )

    def test_a_base_url_given_for_vertex_wins(self):
        assert backend(**VERTEX, base_url='https://private.example/v1').base_url == 'https://private.example/v1'

    @pytest.mark.parametrize('missing', ['project', 'region'])
    def test_a_vertex_backend_without_its_project_or_region_is_refused_saying_so(self, missing):
        fields = {key: value for key, value in VERTEX.items() if key != missing}
        with pytest.raises(BackendInvalid) as refused:
            backend(**fields)
        assert refused.value.reasons == ["Backend 'b' (vertex_openai): a Vertex backend needs its project and region"]

    def test_a_region_on_a_backend_that_is_not_vertex_is_refused(self):
        with pytest.raises(BackendInvalid) as refused:
            backend(base_url='http://g/v1', region='r')
        assert refused.value.reasons == ["Backend 'b' (openai): region is for a Vertex or Bedrock backend only"]

    def test_a_backend_with_no_address_is_refused(self):
        with pytest.raises(BackendInvalid) as refused:
            backend()
        assert refused.value.reasons == ["Backend 'b' (openai): base_url must start with http:// or https://"]

    def test_a_model_map_naming_a_model_not_served_is_refused(self):
        with pytest.raises(BackendInvalid) as refused:
            backend(**AZURE, model_ids={'other': 'x'})
        assert refused.value.reasons == ["Backend 'b' (azure_openai): model_ids names models it does not serve: other"]


class TestEachKindOffersItsMethods:
    def test_the_portal_and_the_router_share_one_table(self):
        assert {kind: list(methods) for kind, methods in METHODS.items()} == {
            BackendKind.OPENAI: [AuthMethod.NONE, AuthMethod.API_KEY],
            BackendKind.AZURE_OPENAI: [
                AuthMethod.AMBIENT,
                AuthMethod.MANAGED_IDENTITY,
                AuthMethod.CLIENT_SECRET,
                AuthMethod.API_KEY,
            ],
            BackendKind.VERTEX_OPENAI: [AuthMethod.AMBIENT, AuthMethod.IMPERSONATE, AuthMethod.SERVICE_ACCOUNT_KEY],
            BackendKind.ANTHROPIC: [AuthMethod.API_KEY],
            BackendKind.BEDROCK: [AuthMethod.AMBIENT, AuthMethod.ASSUME_ROLE, AuthMethod.ACCESS_KEYS],
            BackendKind.VERTEX_ANTHROPIC: [
                AuthMethod.AMBIENT,
                AuthMethod.IMPERSONATE,
                AuthMethod.SERVICE_ACCOUNT_KEY,
            ],
            BackendKind.AZURE_ANTHROPIC: [
                AuthMethod.AMBIENT,
                AuthMethod.MANAGED_IDENTITY,
                AuthMethod.CLIENT_SECRET,
                AuthMethod.API_KEY,
            ],
        }

    def test_the_table_cannot_be_changed_at_run_time(self):
        with pytest.raises(TypeError):
            METHODS[BackendKind.OPENAI] = {}  # type: ignore[index]
        with pytest.raises(TypeError):
            METHODS[BackendKind.OPENAI][AuthMethod.AMBIENT] = METHODS[BackendKind.OPENAI][AuthMethod.NONE]  # type: ignore[index]

    @pytest.mark.parametrize(
        ('fields', 'method'),
        [
            ({'base_url': 'http://g/v1'}, AuthMethod.NONE),
            ({'base_url': 'http://g/v1', 'api_key': 'k'}, AuthMethod.API_KEY),
            (AZURE, AuthMethod.AMBIENT),
            ({**AZURE, 'api_key': 'k'}, AuthMethod.API_KEY),
            (VERTEX, AuthMethod.AMBIENT),
            ({**VERTEX, 'credential': SERVICE_ACCOUNT}, AuthMethod.SERVICE_ACCOUNT_KEY),
        ],
    )
    def test_a_definition_that_names_no_method_means_what_it_carries(self, fields, method):
        assert backend(**fields).auth is method

    @pytest.mark.parametrize(
        'fields',
        [
            {**AZURE, 'auth': 'managed_identity', 'auth_settings': {'client_id': 'c'}},
            {
                **AZURE,
                'auth': 'client_secret',
                'auth_settings': {'tenant_id': 't', 'client_id': 'c'},
                'credential': 's',
            },
            {**VERTEX, 'auth': 'impersonate', 'auth_settings': {'service_account': 'sa@p1.iam.gserviceaccount.com'}},
        ],
    )
    def test_a_method_with_its_settings_and_secret_is_accepted(self, fields):
        accepted = backend(**fields)
        assert (accepted.auth, accepted.auth_settings) == (AuthMethod(fields['auth']), fields['auth_settings'])

    @pytest.mark.parametrize(
        ('fields', 'reason'),
        [
            (
                {**VERTEX, 'auth': 'api_key', 'api_key': 'k'},
                "auth 'api_key' is not offered for this kind; it takes ambient, impersonate, service_account_key",
            ),
            (
                {'base_url': 'http://g/v1', 'auth': 'ambient'},
                "auth 'ambient' is not offered for this kind; it takes none, api_key",
            ),
            ({**AZURE, 'auth': 'managed_identity'}, 'with managed_identity auth: needs client_id'),
            (
                {**AZURE, 'auth': 'client_secret', 'auth_settings': {'client_id': 'c'}, 'credential': 's'},
                'with client_secret auth: needs tenant_id',
            ),
            (
                {**AZURE, 'auth': 'client_secret', 'auth_settings': {'tenant_id': 't', 'client_id': 'c'}},
                'with client_secret auth: needs its credential',
            ),
            (
                {**AZURE, 'auth': 'ambient', 'auth_settings': {'client_id': 'c'}},
                'with ambient auth: does not take client_id',
            ),
            ({**AZURE, 'auth': 'ambient', 'credential': 's'}, 'with ambient auth: takes no stored credential'),
            (
                {**AZURE, 'auth': 'managed_identity', 'auth_settings': {'client_id': 'c'}, 'api_key': 'k'},
                'with managed_identity auth: takes no api_key',
            ),
            ({'base_url': 'http://g/v1', 'auth': 'api_key'}, 'with api_key auth: needs its key'),
            (
                {'base_url': 'http://g/v1', 'auth': 'none', 'credential': 's'},
                'with none auth: takes no stored credential',
            ),
            ({**VERTEX, 'auth': 'impersonate'}, 'with impersonate auth: needs service_account'),
        ],
    )
    def test_a_method_missing_what_it_needs_or_given_what_it_ignores_is_refused_saying_which(self, fields, reason):
        with pytest.raises(BackendInvalid) as refused:
            backend(**fields)
        (said,) = refused.value.reasons
        assert said.endswith(reason)

    @pytest.mark.parametrize(
        ('credential', 'reason'),
        [
            ('not json PRIVATE-KEY-MATERIAL', 'the stored credential is not JSON'),
            (json.dumps(['PRIVATE-KEY-MATERIAL']), 'the stored credential is not a Google service-account key'),
            (
                json.dumps({'type': 'authorized_user', 'secret': 'PRIVATE-KEY-MATERIAL'}),
                'the stored credential is not a Google service-account key',
            ),
        ],
    )
    def test_a_service_account_key_must_be_one_and_is_never_echoed(self, credential, reason):
        with pytest.raises(BackendInvalid) as refused:
            backend(**VERTEX, auth='service_account_key', credential=credential)
        assert refused.value.reasons[0].endswith(reason)
        assert 'MATERIAL' not in str(refused.value)

    def test_the_secret_is_not_in_any_repr(self):
        key_backend = backend(**VERTEX, credential=SERVICE_ACCOUNT)
        assert 'PRIVATE-KEY-MATERIAL' not in repr(key_backend)
        assert 'PRIVATE-KEY-MATERIAL' not in repr(BackendLease.for_backend(key_backend, lease_id='l', model='m'))


class TestALeaseCarriesWhatSigningNeeds:
    def test_it_is_the_backend_as_one_request_holds_it(self):
        source = backend(
            **AZURE,
            auth='client_secret',
            auth_settings={'tenant_id': 't', 'client_id': 'c'},
            credential='shh',
            model_ids={'m': 'deployment'},
        )

        lease = BackendLease.for_backend(source, lease_id='lease-1', model='m')

        assert lease.model_dump() == {
            'lease_id': 'lease-1',
            'backend_id': 'b',
            'base_url': 'https://res.openai.azure.com/openai/v1',
            'model': 'm',
            'api_key': None,
            'kind': BackendKind.AZURE_OPENAI,
            'region': None,
            'auth': AuthMethod.CLIENT_SECRET,
            'auth_settings': {'tenant_id': 't', 'client_id': 'c'},
            'credential': 'shh',
            'model_ids': {'m': 'deployment'},
        }
        assert (
            lease.signer
            == source.signer
            == Signer(
                backend_id='b',
                kind=BackendKind.AZURE_OPENAI,
                auth=AuthMethod.CLIENT_SECRET,
                settings={'tenant_id': 't', 'client_id': 'c'},
                secret='shh',
            )
        )

    @pytest.mark.parametrize(
        ('auth', 'renews'),
        [(AuthMethod.NONE, False), (AuthMethod.API_KEY, False), (AuthMethod.ACCESS_KEYS, False)]
        + [
            (method, True)
            for method in AuthMethod
            if method not in (AuthMethod.NONE, AuthMethod.API_KEY, AuthMethod.ACCESS_KEYS)
        ],
    )
    def test_only_a_credential_the_server_obtained_is_worth_renewing(self, auth, renews):
        lease = BackendLease(lease_id='l', backend_id='b', base_url='http://x', model='m', auth=auth)
        assert lease.renews_token is renews

    @pytest.mark.parametrize(
        ('kind', 'auth', 'api_key', 'headers'),
        [
            (
                BackendKind.OPENAI,
                AuthMethod.API_KEY,
                'sk',
                {'Content-Type': 'application/json', 'Authorization': 'Bearer sk'},
            ),
            (BackendKind.OPENAI, AuthMethod.NONE, None, {'Content-Type': 'application/json'}),
            (BackendKind.AZURE_OPENAI, AuthMethod.API_KEY, 'az', {'Content-Type': 'application/json', 'api-key': 'az'}),
        ],
    )
    def test_a_static_key_goes_in_the_header_its_kind_reads(self, kind, auth, api_key, headers):
        lease = BackendLease(
            lease_id='l', backend_id='b', base_url='http://x', model='m', kind=kind, auth=auth, api_key=api_key
        )
        assert lease.auth_headers() == headers


class TestClaudesBackends:
    def test_bedrock_offers_the_servers_role_an_assumed_role_and_access_keys(self):
        bedrock = METHODS[BackendKind.BEDROCK]
        role = bedrock[AuthMethod.ASSUME_ROLE]
        assert (role.settings, role.optional, role.secret) == (('role_arn',), ('external_id',), None)
        assert (bedrock[AuthMethod.ACCESS_KEYS].settings, bedrock[AuthMethod.ACCESS_KEYS].secret) == (
            ('access_key_id',),
            'credential',
        )

    @pytest.mark.parametrize(
        ('fields', 'base_url'),
        [
            ({'kind': 'bedrock', 'region': 'eu-central-1'}, 'https://bedrock-runtime.eu-central-1.amazonaws.com'),
            (
                {'kind': 'vertex_anthropic', 'project': 'p', 'region': 'us-east5'},
                'https://us-east5-aiplatform.googleapis.com/v1/projects/p/locations/us-east5/publishers/anthropic/models',
            ),
            ({'kind': 'anthropic', 'api_key': 'k'}, 'https://api.anthropic.com'),
            ({'kind': 'anthropic', 'api_key': 'k', 'base_url': 'https://gw.internal'}, 'https://gw.internal'),
        ],
    )
    def test_an_address_its_kind_fixes_is_built(self, fields, base_url):
        assert backend(**fields).base_url == base_url

    @pytest.mark.parametrize(
        ('fields', 'reason'),
        [
            ({'kind': 'bedrock'}, "Backend 'b' (bedrock): a Bedrock backend needs its region"),
            (
                {'kind': 'anthropic', 'api_key': 'k', 'region': 'r'},
                "Backend 'b' (anthropic): region is for a Vertex or Bedrock backend only",
            ),
            (
                {'kind': 'bedrock', 'region': 'r', 'project': 'p'},
                "Backend 'b' (bedrock): project is for a Vertex backend only",
            ),
            (
                {'kind': 'bedrock', 'region': 'r', 'auth': 'assume_role'},
                "Backend 'b' (bedrock) with assume_role auth: needs role_arn",
            ),
            (
                {'kind': 'bedrock', 'region': 'r', 'auth': 'access_keys', 'auth_settings': {'access_key_id': 'A'}},
                "Backend 'b' (bedrock) with access_keys auth: needs its credential",
            ),
            ({'kind': 'anthropic'}, "Backend 'b' (anthropic) with api_key auth: needs its key"),
        ],
    )
    def test_a_claude_backend_missing_what_it_needs_is_refused_saying_what(self, fields, reason):
        with pytest.raises(BackendInvalid) as refused:
            backend(**fields)
        assert refused.value.reasons == [reason]

    def test_an_assumed_roles_external_id_is_optional_and_kept_when_given(self):
        role = {'kind': 'bedrock', 'region': 'r', 'auth': 'assume_role'}
        assert backend(**role, auth_settings={'role_arn': 'arn:aws:iam::1:role/r'}).auth is AuthMethod.ASSUME_ROLE
        with_id = backend(**role, auth_settings={'role_arn': 'arn:aws:iam::1:role/r', 'external_id': 'x'})
        assert with_id.auth_settings == {'role_arn': 'arn:aws:iam::1:role/r', 'external_id': 'x'}

    def test_a_bedrock_lease_carries_its_region_for_signing(self):
        source = backend(kind='bedrock', region='eu-central-1')
        assert BackendLease.for_backend(source, lease_id='l', model='m').region == 'eu-central-1'
