"""PKCE (RFC 7636) for the authorization code grant."""

from __future__ import annotations

import base64
import hashlib


class Pkce:
    """S256 only. `plain` would put the verifier in the redirect."""

    @staticmethod
    def matches(verifier: str | None, challenge: str) -> bool:
        if not verifier or not 43 <= len(verifier) <= 128:
            return False
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode() == challenge
