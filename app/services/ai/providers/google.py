"""Vertex AI's access token, from Google's default chain, an impersonated account, or a key.

google-auth is imported inside each source, not at the top: it is loaded
only when a Vertex backend is configured, so a deployment with none never
imports it. It refreshes synchronously, over `requests`, so the refresh
runs in a worker thread.
"""

from __future__ import annotations

from abc import abstractmethod
import asyncio
import calendar
from collections.abc import Mapping
import json
import time
from typing import Any

from app.services.ai.providers.tokens import AccessToken, TokenSource

#: The OAuth scope Vertex AI accepts.
GOOGLE_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'

#: How long a token whose expiry Google did not state is trusted.
_UNSTATED_LIFETIME_SECONDS = 300


class GoogleTokenSource(TokenSource[str]):
    """A Google credential, refreshed for a Vertex AI bearer token."""

    def __init__(self, *, settings: Mapping[str, str], secret: str | None) -> None:
        super().__init__(settings=settings, secret=secret)
        #: Built on the first fetch, then refreshed in place.
        self._credentials: Any = None

    async def fetch(self) -> AccessToken[str]:
        return await asyncio.to_thread(self._refreshed)

    def _refreshed(self) -> AccessToken[str]:
        # Optional dependency: loaded only when a Vertex backend is configured.
        import google.auth.transport.requests

        if self._credentials is None:
            self._credentials = self._built()
        self._credentials.refresh(google.auth.transport.requests.Request())
        expiry = self._credentials.expiry
        # google-auth's expiry is a naive UTC datetime.
        expires_at = (
            float(calendar.timegm(expiry.utctimetuple()))
            if expiry is not None
            else time.time() + _UNSTATED_LIFETIME_SECONDS
        )
        return AccessToken(value=self._credentials.token, expires_at=expires_at)

    @abstractmethod
    def _built(self) -> Any:
        """The google-auth credentials object this source refreshes."""


class GoogleDefaultSource(GoogleTokenSource):
    """The server's own identity: Workload Identity, an attached service account, or ADC."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when a Vertex backend is configured.
        import google.auth

        credentials, _project = google.auth.default(scopes=[GOOGLE_SCOPE])
        return credentials


class GoogleImpersonatedSource(GoogleTokenSource):
    """The server's own identity acting as the named service account."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when a Vertex backend is configured.
        import google.auth
        from google.auth import impersonated_credentials

        source, _project = google.auth.default(scopes=[GOOGLE_SCOPE])
        return impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=self._settings['service_account'],
            target_scopes=[GOOGLE_SCOPE],
        )


class GoogleKeySource(GoogleTokenSource):
    """A service account's JSON key, stored encrypted on the backend."""

    def _built(self) -> Any:
        # Optional dependency: loaded only when a Vertex backend is configured.
        from google.oauth2 import service_account

        info = json.loads(self._secret or '')
        return service_account.Credentials.from_service_account_info(info, scopes=[GOOGLE_SCOPE])
