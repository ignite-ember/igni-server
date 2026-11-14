"""Azure's Entra ID token, from the default chain, a user-assigned identity, or an app's secret.

azure-identity is imported inside each source, not at the top: it is
loaded only when an Azure backend is configured, so a deployment with
none never imports it.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import Any

from app.services.ai.providers.tokens import AccessToken, TokenSource

#: The Entra ID scope Azure OpenAI (and Foundry) accept.
AZURE_SCOPE = 'https://cognitiveservices.azure.com/.default'


class AzureTokenSource(TokenSource[str]):
    """An azure-identity credential, asked for a bearer token."""

    def __init__(self, *, settings: Mapping[str, str], secret: str | None) -> None:
        super().__init__(settings=settings, secret=secret)
        #: Built on the first fetch, then refreshed in place.
        self._credential: Any = None

    async def fetch(self) -> AccessToken[str]:
        if self._credential is None:
            self._credential = self._built()
        token = await self._credential.get_token(AZURE_SCOPE)
        return AccessToken(value=token.token, expires_at=float(token.expires_on))

    @abstractmethod
    def _built(self) -> Any:
        """The azure-identity credential this source asks."""


class AzureDefaultSource(AzureTokenSource):
    """The server's own identity, through the default chain: its system Managed Identity first."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when an Azure backend is configured.
        from azure.identity.aio import DefaultAzureCredential

        return DefaultAzureCredential()


class AzureManagedIdentitySource(AzureTokenSource):
    """A user-assigned Managed Identity, chosen by its client id."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when an Azure backend is configured.
        from azure.identity.aio import ManagedIdentityCredential

        return ManagedIdentityCredential(client_id=self._settings['client_id'])


class AzureClientSecretSource(AzureTokenSource):
    """An Entra app registration's client secret, stored encrypted on the backend."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when an Azure backend is configured.
        from azure.identity.aio import ClientSecretCredential

        return ClientSecretCredential(
            tenant_id=self._settings['tenant_id'],
            client_id=self._settings['client_id'],
            client_secret=self._secret or '',
        )
