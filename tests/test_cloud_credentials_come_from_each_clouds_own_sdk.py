"""Each auth method gets its credential from its cloud's own SDK, and the cache renews it.

The sources are run for real here, over fake SDK classes standing in for
google-auth and azure-identity: which class each method builds, and from
which setting or secret, is the part an operator relies on. The cache is
held to its contract: a token is reused until the renewal margin, renewed
after a refusal, and a changed method, setting or secret is a new token at
once. No cloud is reached.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
import types

import google.auth
from google.auth import impersonated_credentials
from google.oauth2 import service_account
import pytest

from app.services.ai.providers import aws
from app.services.ai.providers.auth import AuthMethod
from app.services.ai.providers.aws import (
    AMBIENT_REREAD_SECONDS,
    AwsAccessKeysSource,
    AwsAssumedRoleSource,
    AwsDefaultSource,
    AwsKeys,
)
from app.services.ai.providers.azure import (
    AzureClientSecretSource,
    AzureDefaultSource,
    AzureManagedIdentitySource,
)
from app.services.ai.providers.credentials import KEY_SOURCES, Signer, TOKEN_SOURCES
from app.services.ai.providers.google import (
    GOOGLE_SCOPE,
    GoogleDefaultSource,
    GoogleImpersonatedSource,
    GoogleKeySource,
)
from app.services.ai.providers.kinds import BackendKind
from app.services.ai.providers.tokens import CachedToken, CredentialUnavailable, REFRESH_MARGIN_SECONDS
from tests.cloud_fakes import FakeCloud, SERVICE_ACCOUNT


def _signer(auth: AuthMethod = AuthMethod.AMBIENT, settings: dict | None = None, secret: str | None = None) -> Signer:
    return Signer(backend_id='b', kind=BackendKind.VERTEX_OPENAI, auth=auth, settings=settings or {}, secret=secret)


class TestEachKindAndMethodHasItsSource:
    def test_the_sources_cover_every_method_that_signs_with_a_token(self):
        google = {
            AuthMethod.AMBIENT: GoogleDefaultSource,
            AuthMethod.IMPERSONATE: GoogleImpersonatedSource,
            AuthMethod.SERVICE_ACCOUNT_KEY: GoogleKeySource,
        }
        azure = {
            AuthMethod.AMBIENT: AzureDefaultSource,
            AuthMethod.MANAGED_IDENTITY: AzureManagedIdentitySource,
            AuthMethod.CLIENT_SECRET: AzureClientSecretSource,
        }
        assert dict(TOKEN_SOURCES) == {
            **{
                (kind, method): source
                for kind in (BackendKind.VERTEX_OPENAI, BackendKind.VERTEX_ANTHROPIC)
                for method, source in google.items()
            },
            **{
                (kind, method): source
                for kind in (BackendKind.AZURE_OPENAI, BackendKind.AZURE_ANTHROPIC)
                for method, source in azure.items()
            },
        }

    def test_bedrock_signs_with_keys_from_its_own_sources(self):
        assert dict(KEY_SOURCES) == {
            (BackendKind.BEDROCK, AuthMethod.AMBIENT): AwsDefaultSource,
            (BackendKind.BEDROCK, AuthMethod.ASSUME_ROLE): AwsAssumedRoleSource,
            (BackendKind.BEDROCK, AuthMethod.ACCESS_KEYS): AwsAccessKeysSource,
        }


@pytest.fixture
def azure_sdk(monkeypatch):
    """azure.identity.aio's three credential classes, recording how each was built."""
    built: list[tuple[str, dict]] = []

    def credential_class(name: str):
        class Credential:
            def __init__(self, **kwargs):
                built.append((name, kwargs))

            async def get_token(self, scope):
                assert scope == 'https://cognitiveservices.azure.com/.default'
                return types.SimpleNamespace(token=f'{name}-token', expires_on=2_000_000_000)

        return Credential

    aio = types.ModuleType('azure.identity.aio')
    for name in ('DefaultAzureCredential', 'ManagedIdentityCredential', 'ClientSecretCredential'):
        setattr(aio, name, credential_class(name))
    monkeypatch.setitem(sys.modules, 'azure.identity.aio', aio)
    return built


class TestAzureMethodsBuildTheirOwnCredential:
    @pytest.mark.parametrize(
        ('source', 'settings', 'secret', 'built'),
        [
            (AzureDefaultSource, {}, None, ('DefaultAzureCredential', {})),
            (
                AzureManagedIdentitySource,
                {'client_id': 'uami'},
                None,
                ('ManagedIdentityCredential', {'client_id': 'uami'}),
            ),
            (
                AzureClientSecretSource,
                {'tenant_id': 't', 'client_id': 'c'},
                'shh',
                ('ClientSecretCredential', {'tenant_id': 't', 'client_id': 'c', 'client_secret': 'shh'}),
            ),
        ],
    )
    async def test_each_method_builds_its_credential_once_and_asks_it_for_the_token(
        self, azure_sdk, source, settings, secret, built
    ):
        made = source(settings=settings, secret=secret)

        first, second = await made.fetch(), await made.fetch()

        assert azure_sdk == [built]
        assert (first.value, first.expires_at) == (f'{built[0]}-token', 2_000_000_000.0)
        assert second.value == first.value


@pytest.fixture
def google_sdk(monkeypatch):
    """google.auth's default chain, impersonation and keys, recording how each was built."""
    made: list[tuple[str, dict]] = []

    class Credentials:
        def __init__(self, name: str, **kwargs):
            made.append((name, kwargs))
            self.token, self.expiry = f'{name}-token', dt.datetime(2033, 5, 18, 3, 33, 20)

        def refresh(self, _request):
            pass

    monkeypatch.setattr(google.auth, 'default', lambda scopes: (Credentials('default', scopes=scopes), 'p'))
    monkeypatch.setattr(
        impersonated_credentials,
        'Credentials',
        lambda **kw: Credentials('impersonated', target=kw['target_principal'], scopes=kw['target_scopes']),
    )
    monkeypatch.setattr(
        service_account.Credentials,
        'from_service_account_info',
        staticmethod(lambda info, scopes: Credentials('key', email=info['client_email'], scopes=scopes)),
    )
    return made


class TestGoogleMethodsBuildTheirOwnCredential:
    async def test_the_default_chain_is_the_servers_own_identity(self, google_sdk):
        token = await GoogleDefaultSource(settings={}, secret=None).fetch()
        assert google_sdk == [('default', {'scopes': [GOOGLE_SCOPE]})]
        assert (token.value, token.expires_at) == ('default-token', 2_000_000_000.0)

    async def test_impersonation_acts_as_the_named_account_from_the_servers_identity(self, google_sdk):
        token = await GoogleImpersonatedSource(settings={'service_account': 'sa@p.iam'}, secret=None).fetch()
        assert google_sdk == [
            ('default', {'scopes': [GOOGLE_SCOPE]}),
            ('impersonated', {'target': 'sa@p.iam', 'scopes': [GOOGLE_SCOPE]}),
        ]
        assert token.value == 'impersonated-token'

    async def test_a_key_is_read_from_the_stored_secret(self, google_sdk):
        token = await GoogleKeySource(settings={}, secret=SERVICE_ACCOUNT).fetch()
        assert google_sdk == [('key', {'email': 'x', 'scopes': [GOOGLE_SCOPE]})]
        assert token.value == 'key-token'


@pytest.fixture
def aws_sdk(monkeypatch):
    """boto3's Session: a default chain with frozen keys, and an STS that records each assume_role."""
    assumed: list[dict] = []

    class Chain:
        def get_frozen_credentials(self):
            return types.SimpleNamespace(access_key='AKIA-ROLE', secret_key='role-secret', token='role-token')

    class Sts:
        def assume_role(self, **request):
            assumed.append(request)
            return {
                'Credentials': {
                    'AccessKeyId': 'ASIA-ASSUMED',
                    'SecretAccessKey': 'assumed-secret',
                    'SessionToken': 'assumed-token',
                    'Expiration': dt.datetime(2033, 5, 18, 3, 33, 20, tzinfo=dt.timezone.utc),
                }
            }

    class Session:
        def get_credentials(self):
            return Chain()

        def client(self, name):
            assert name == 'sts'
            return Sts()

    monkeypatch.setattr(aws.boto3, 'Session', Session)
    return assumed


class TestAwsMethodsGetTheirOwnKeys:
    async def test_the_servers_role_is_read_from_the_default_chain_and_read_again_soon(self, aws_sdk):
        token = await AwsDefaultSource(settings={}, secret=None).fetch()
        assert token.value == AwsKeys(
            access_key_id='AKIA-ROLE', secret_access_key='role-secret', session_token='role-token'
        )
        assert token.expires_at <= time.time() + AMBIENT_REREAD_SECONDS
        assert aws_sdk == []

    @pytest.mark.parametrize(
        ('settings', 'request_'),
        [
            (
                {'role_arn': 'arn:aws:iam::1:role/r'},
                {'RoleArn': 'arn:aws:iam::1:role/r', 'RoleSessionName': 'igni-server', 'DurationSeconds': 3600},
            ),
            (
                {'role_arn': 'arn:aws:iam::1:role/r', 'external_id': 'shared-ext'},
                {
                    'RoleArn': 'arn:aws:iam::1:role/r',
                    'RoleSessionName': 'igni-server',
                    'DurationSeconds': 3600,
                    'ExternalId': 'shared-ext',
                },
            ),
        ],
    )
    async def test_an_assumed_role_is_asked_of_sts_with_its_external_id_only_when_set(
        self, aws_sdk, settings, request_
    ):
        token = await AwsAssumedRoleSource(settings=settings, secret=None).fetch()
        assert aws_sdk == [request_]
        assert token.value == AwsKeys(
            access_key_id='ASIA-ASSUMED', secret_access_key='assumed-secret', session_token='assumed-token'
        )
        assert token.expires_at == 2_000_000_000.0

    async def test_stored_access_keys_are_the_keys_and_do_not_expire(self, aws_sdk):
        token = await AwsAccessKeysSource(settings={'access_key_id': 'AKIA-USER'}, secret='user-secret').fetch()
        assert token.value == AwsKeys(access_key_id='AKIA-USER', secret_access_key='user-secret')
        assert token.expires_at == float('inf')
        assert aws_sdk == []

    def test_the_secret_never_reaches_a_repr(self):
        assert 'user-secret' not in repr(AwsKeys(access_key_id='A', secret_access_key='user-secret', session_token='t'))


class TestATokenIsCachedAndRenewed:
    async def test_a_token_is_reused_until_the_margin_before_it_expires(self):
        now = [1000.0]
        cloud = FakeCloud(lifetime=3600.0, clock=lambda: now[0])
        cached = CachedToken(
            cloud.source(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)(settings={}, secret=None), clock=lambda: now[0]
        )

        assert await cached.value() == 'tok-1'
        now[0] += 3600.0 - REFRESH_MARGIN_SECONDS - 1
        assert await cached.value() == 'tok-1'
        now[0] += 1
        assert await cached.value() == 'tok-2'

    async def test_invalidating_fetches_a_new_one(self):
        cloud = FakeCloud()
        cached = CachedToken(cloud.source(BackendKind.VERTEX_OPENAI, AuthMethod.AMBIENT)(settings={}, secret=None))
        await cached.value()
        cached.invalidate()
        assert await cached.value() == 'tok-2'

    async def test_the_same_signer_reuses_its_token_and_the_cloud_is_asked_once(self):
        cloud = FakeCloud()
        credentials = cloud.credentials()
        assert (await credentials.bearer(_signer()), await credentials.bearer(_signer())) == ('tok-1', 'tok-1')
        assert len(cloud.fetches) == 1

    @pytest.mark.parametrize(
        ('first', 'second'),
        [
            (
                _signer(AuthMethod.SERVICE_ACCOUNT_KEY, secret=SERVICE_ACCOUNT),
                _signer(AuthMethod.SERVICE_ACCOUNT_KEY, secret='{"type": "service_account", "k": 2}'),
            ),
            (
                _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'a@p.iam'}),
                _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'b@p.iam'}),
            ),
            (_signer(AuthMethod.AMBIENT), _signer(AuthMethod.IMPERSONATE, settings={'service_account': 'a@p.iam'})),
        ],
    )
    async def test_a_changed_method_setting_or_secret_gets_a_token_of_its_own_at_once(self, first, second):
        credentials = FakeCloud().credentials()
        assert (await credentials.bearer(first), await credentials.bearer(second)) == ('tok-1', 'tok-2')

    async def test_a_refused_token_is_dropped_so_the_next_ask_renews_it(self):
        cloud = FakeCloud()
        credentials = cloud.credentials()
        await credentials.bearer(_signer())
        credentials.invalidate(_signer())
        assert await credentials.bearer(_signer()) == 'tok-2'

    async def test_an_sdk_failure_is_one_named_error_naming_no_secret(self):
        with pytest.raises(CredentialUnavailable) as raised:
            await (
                FakeCloud(fail=True)
                .credentials()
                .bearer(_signer(AuthMethod.SERVICE_ACCOUNT_KEY, secret=SERVICE_ACCOUNT))
            )
        assert str(raised.value) == 'No credentials for the vertex_openai backend'
        assert raised.value.kind is BackendKind.VERTEX_OPENAI
