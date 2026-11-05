"""A sign-in button is offered only when a sign-in could succeed.

`/v1/auth/methods` exists so a sign-in page renders what *this*
deployment offers instead of hardcoded buttons. It answered the
question with `settings.oauth_providers` — the list an operator says
they *want* — which is not the same as whether a button can work.

A fresh self-hosted install that listed a provider with no credentials
advertised it anyway and sent anyone who clicked to:

    https://accounts.google.com/o/oauth2/v2/auth?client_id=&redirect_uri=...

An error page on somebody else's domain, about a request this server
built. That is the exact failure the endpoint was written to prevent,
one step further along: it fixed the case where an operator *narrows*
the list and missed the case where they have not configured anything,
which is every install on its first day.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.auth import providers

pytestmark = pytest.mark.anyio

_METHODS = '/auth/methods'


def _google(*, ready: bool):
    return patch.object(providers, '_google_ready', lambda: ready)


class TestWhatIsAdvertised:
    async def test_an_unconfigured_provider_is_not_offered(self, client):
        with _google(ready=False):
            answer = (await client.get(_METHODS)).json()

        assert answer['oauth_providers'] == []

    async def test_a_configured_one_is(self, client):
        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with _google(ready=True):
                answer = (await client.get(_METHODS)).json()

        assert answer['oauth_providers'] == ['google']

    async def test_disabling_still_wins_over_being_configured(self, client):
        # Credentials present, operator said no. The operator decides.
        with patch.object(providers.settings, 'oauth_providers', []):
            with _google(ready=True):
                answer = (await client.get(_METHODS)).json()

        assert answer['oauth_providers'] == []

    async def test_password_is_always_offered(self, client):
        # There is no setting that turns local accounts off, so the
        # page must never be left with nothing at all.
        with _google(ready=False):
            answer = (await client.get(_METHODS)).json()

        assert answer['password'] is True


class TestWhatHappensIfSomebodyAsksAnyway:
    async def test_an_unconfigured_provider_refuses_instead_of_redirecting(self, client):
        # The old behaviour was a 302 carrying `client_id=`. A refusal
        # here says why; an error page on the provider's domain does not.
        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with _google(ready=False):
                response = await client.get('/auth/google', follow_redirects=False)

        assert response.status_code == 400
        assert 'no credentials' in response.json()['detail']

    async def test_a_configured_one_still_redirects(self, client):
        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with _google(ready=True):
                response = await client.get('/auth/google', follow_redirects=False)

        assert response.status_code == 302

    @pytest.mark.parametrize('ready', [True, False])
    async def test_the_button_and_the_redirect_cannot_disagree(self, client, ready):
        # One predicate behind both, so a provider can never be
        # advertised and then refused, or refused and then advertised.
        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with _google(ready=ready):
                offered = 'google' in (await client.get(_METHODS)).json()['oauth_providers']
                refused = (await client.get('/auth/google', follow_redirects=False)).status_code == 400

        assert refused is not offered, f'offered={offered} refused={refused}'


class TestItNeverBreaksTheSignInPage:
    async def test_a_lookup_that_throws_drops_the_provider_rather_than_the_page(self, client):
        # A page that fails to render because a provider lookup threw is
        # worse than a provider missing from it — password and SSO still
        # work.
        def _explode() -> bool:
            raise RuntimeError('configuration is unreadable')

        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with patch.object(providers, '_google_ready', _explode):
                response = await client.get(_METHODS)

        assert response.status_code == 200
        assert response.json()['oauth_providers'] == []
        assert response.json()['password'] is True

    async def test_an_unknown_provider_name_is_not_usable(self):
        with patch.object(providers.settings, 'oauth_providers', ['bitbucket']):
            assert await providers.is_usable('bitbucket') is False


class TestWhatAFreshDeploymentOffers:
    """SSO and email are the strategy. Social is the exception.

    A company authenticates its people through its identity provider,
    the way it does for every other enterprise application it has
    connected. GitHub and GitLab are where its code lives rather than
    who its people are, and belong to CodeIndex.
    """

    def test_google_is_the_only_default(self):
        # Google credentials exist here for exactly one reason, so
        # configuring them *is* the decision to offer it. A second
        # switch would only be a way to configure Google and then
        # wonder why no button appeared.
        assert Settings().oauth_providers == ['google']

    @pytest.mark.parametrize('name', ['github', 'gitlab'])
    async def test_a_git_provider_is_not_a_way_in_even_if_listed(self, client, name):
        with patch.object(providers.settings, 'oauth_providers', [name, 'google']):
            with _google(ready=False):
                offered = (await client.get(_METHODS)).json()['oauth_providers']
                response = await client.get(f'/auth/{name}', follow_redirects=False)

        assert name not in offered
        assert response.status_code == 400


class TestAnAirGappedDeploymentOffersNoGoogle:
    """B39. An air-gapped deployment cannot reach Google, so a Google
    button there is a dead end and an outbound call a reviewer asks about.
    The chart now renders `OAUTH_PROVIDERS=[]` unless a client ID is set;
    the server offers nothing with no client ID even if Google is listed."""

    async def test_no_client_id_means_no_button_even_with_google_listed(self, client):
        with patch.object(providers.settings, 'oauth_providers', ['google']):
            with patch.object(providers.settings, 'oauth_google_client_id', ''):
                with patch.object(providers.settings, 'oauth_google_client_secret', 'a-secret'):
                    answer = (await client.get(_METHODS)).json()
                    response = await client.get('/auth/google', follow_redirects=False)

        assert answer['oauth_providers'] == []
        assert response.status_code == 400

    async def test_what_the_chart_renders_by_default_turns_it_off(self, client, monkeypatch):
        monkeypatch.setenv('OAUTH_PROVIDERS', '[]')
        rendered = Settings().oauth_providers
        with patch.object(providers.settings, 'oauth_providers', rendered):
            with _google(ready=True):
                answer = (await client.get(_METHODS)).json()

        assert rendered == []
        assert answer['oauth_providers'] == []
