"""Why a social sign-in fails. `OAuthRefusals` decides what HTTP makes of the refusals."""

from __future__ import annotations

from app.services.auth.oauth_models import OAuthProvider


class OAuthExchangeFailed(Exception):
    """The provider could not be reached, refused the code, or answered nonsense.

    The cause is chained for the log. It is not for the person signing
    in: an upstream error body or a URL is nothing they can act on.
    """

    def __init__(self, provider: OAuthProvider) -> None:
        super().__init__(f'The {provider.value} sign-in exchange failed')
        self.provider = provider


class OAuthRefused(Exception):
    """The sign-in cannot go ahead; the message is the caller's to read."""


class InvalidOAuthState(OAuthRefused):
    """A state token this server did not issue, or has already consumed."""

    def __init__(self) -> None:
        super().__init__('Invalid OAuth state')


class ProviderAnswerRefused(OAuthRefused):
    """The provider answered, well-formed, without what a sign-in needs."""
