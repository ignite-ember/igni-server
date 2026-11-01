"""OIDC sign-in against the customer's own identity provider.

Google, the other provider here, is a specific company reachable only
from a network with internet access. An air-gapped deployment cannot
reach it, so without this
the only way in is a local password, which no enterprise will accept.

This talks to whatever the customer runs: Keycloak, Okta, Entra,
Auth0, Authentik, Ping. One issuer URL configures it, because OIDC
discovery publishes the rest.

## What is security-critical here

Everything about validating the ID token, and it is worth being explicit
because a permissive check reads exactly like a working one.

* **The signature**, against the issuer's published JWKS. An unsigned or
  wrongly-signed token is the whole attack.
* **`iss`**, exactly matching the configured issuer. Otherwise any IdP
  that will sign a token for anyone gets to mint our users.
* **`aud`**, matching our client id. A token issued for a *different*
  client of the same IdP must not be accepted here.
* **`nonce`**, matching the one we generated for this login. This is
  what stops a token obtained elsewhere from being replayed into our
  callback.

`app.security.jose.decode` checks signature, `iss`, `aud` and `exp`. It
does **not** check `nonce` — that one is ours, and it is done explicitly
below.

## What this deliberately does not do

No JIT group-to-role mapping. Claims describing groups vary wildly
between IdPs and mapping them wrong grants access rather than denying
it, so a first login creates an ordinary member and an admin promotes
them. That is a smaller and more auditable surface than guessing.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from httpx._client import USE_CLIENT_DEFAULT, UseClientDefault
from pydantic import BaseModel

from app.config import settings
from app.security import jose

logger = logging.getLogger(__name__)

# Discovery and JWKS are cached: they change when the IdP rotates keys,
# which is rare, and fetching them per login would put the provider on
# the critical path of every sign-in.
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600

_HTTP_TIMEOUT = 10.0


class OidcNotConfigured(RuntimeError):
    """OIDC is not set up on this deployment.

    A configuration state rather than a failure — a deployment using
    local accounts and Google sign-in is perfectly valid — so callers
    check ``OidcClient.is_configured`` instead of catching this.
    """


#: The signing algorithms an ``id_token`` may use.
#:
#: Asymmetric only. The token is signed by the issuer with a key we know
#: only the public half of, so an HMAC algorithm has no legitimate place
#: here — and ``none`` least of all.
#:
#: Pinned because ``jwt.decode`` was called without ``algorithms=``, and
#: python-jose reads that as "any ``alg`` the token header claims":
#: ``if algorithms is not None and alg not in algorithms``. Both classic
#: attacks were tried against it and both failed — ``alg: none`` is
#: rejected because jose cannot find an algorithm for a JWK, and the
#: RS256-to-HS256 confusion cannot even be *constructed*, since jose
#: refuses to use asymmetric key material as an HMAC secret. So this is
#: not a fix for a live hole.
#:
#: It is here because that protection is incidental: it comes from two
#: guards inside a dependency rather than from anything this code says.
#: Naming the set costs nothing and means a library change cannot quietly
#: reopen it. That change has since happened (python-jose to PyJWT, for
#: PYSEC-2026-4298), and `app.security.jose` now refuses any non-asymmetric
#: algorithm outright, whoever asks for it.
ID_TOKEN_ALGORITHMS = ['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512', 'PS256', 'PS384', 'PS512']


class OidcError(RuntimeError):
    """The login could not be completed.

    Deliberately one class for every failure a user could hit. The
    message goes to the log; the caller shows something generic, because
    "the signature was wrong" and "the nonce did not match" are useful to
    an operator and useful to an attacker in exactly the same way.
    """


class OidcClaims(BaseModel):
    """The identity an ID token asserts."""

    subject: str
    email: str
    email_verified: bool = False
    full_name: str | None = None


class _Cached(BaseModel):
    value: dict[str, Any]
    fetched_at: float

    def fresh(self, ttl: int) -> bool:
        return (time.monotonic() - self.fetched_at) < ttl


class OidcClient:
    """One OIDC provider, discovered from its issuer URL."""

    def __init__(
        self,
        *,
        issuer: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        self._issuer = (issuer if issuer is not None else settings.oidc_issuer).rstrip('/')
        self._client_id = client_id if client_id is not None else settings.oidc_client_id
        self._client_secret = client_secret if client_secret is not None else settings.oidc_client_secret
        self._redirect_uri = (
            redirect_uri
            if redirect_uri is not None
            else f'{settings.oauth_redirect_base_url.rstrip("/")}/v1/auth/oidc/callback'
        )
        self._discovery: _Cached | None = None
        self._jwks: _Cached | None = None

    def _validate_issuer_scheme(self) -> None:
        """Refuse a plaintext issuer.

        Everything OIDC guarantees rests on the discovery document and
        the JWKS being genuine, and over `http://` neither is: whoever
        can see the traffic can substitute their own signing keys and
        mint any user on this deployment. TLS is the whole trust anchor,
        so this is not hardening.

        `http://localhost` and `http://127.0.0.1` are allowed, because
        that is how anybody tests against a local Keycloak and the
        traffic never leaves the machine.
        """
        if not self._issuer:
            return

        if self._issuer.startswith('https://'):
            return

        host = self._issuer.removeprefix('http://').split('/')[0].split(':')[0]
        if host in ('localhost', '127.0.0.1', '::1'):
            return

        raise OidcError(
            f'oidc_issuer is {self._issuer!r}. OIDC over plaintext HTTP is not accepted: the '
            'discovery document and the signing keys arrive over that connection, so anybody '
            'who can see the traffic can substitute their own keys and sign in as anybody. '
            'Use https, or localhost for a local provider.'
        )

    @property
    def is_configured(self) -> bool:
        """Whether this deployment has an IdP configured."""
        return bool(self._issuer and self._client_id and self._client_secret)

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise OidcNotConfigured('Set oidc_issuer, oidc_client_id and oidc_client_secret to enable OIDC sign-in')
        self._validate_issuer_scheme()

    # ── Discovery ────────────────────────────────────────────────────

    async def discover(self) -> dict[str, Any]:
        """The provider's discovery document, cached.

        Fetched from ``{issuer}/.well-known/openid-configuration``, which
        is where the spec puts it and where every provider actually
        serves it.
        """
        self._require_configured()
        if self._discovery is not None and self._discovery.fresh(_DISCOVERY_TTL_SECONDS):
            return self._discovery.value

        url = f'{self._issuer}/.well-known/openid-configuration'
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(url)
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(f'Could not read OIDC discovery from {url}: {exc}') from exc

        # A discovery document whose issuer disagrees with the URL it was
        # served from is either a misconfiguration or a redirect to
        # somewhere we did not intend to trust.
        declared = str(document.get('issuer', '')).rstrip('/')
        if declared != self._issuer:
            raise OidcError(f'OIDC discovery at {url} declares issuer {declared!r}, which is not the configured issuer')

        self._discovery = _Cached(value=document, fetched_at=time.monotonic())
        return self._discovery.value

    async def _jwks_keys(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._jwks is not None and self._jwks.fresh(_JWKS_TTL_SECONDS):
            return self._jwks.value

        document = await self.discover()
        uri = document.get('jwks_uri')
        if not uri:
            raise OidcError('The OIDC discovery document has no jwks_uri')

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(uri)
                response.raise_for_status()
                keys = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(f'Could not read the OIDC signing keys from {uri}: {exc}') from exc

        self._jwks = _Cached(value=keys, fetched_at=time.monotonic())
        return self._jwks.value

    # ── The login round-trip ─────────────────────────────────────────

    @staticmethod
    def generate_pkce_verifier() -> str:
        """A PKCE code verifier for one login.

        Stored with the state and sent at the token exchange. This is a
        *confidential* client — it holds a secret — so PKCE is not
        strictly required by RFC 7636, and RFC 9700 (the current OAuth
        security best-current-practice) asks for it anyway, because the
        attack it stops is not about client secrecy.

        The attack is authorization code injection: somebody who obtains
        a code belonging to another user — from a referrer header, a
        proxy log, a shared browser, a malicious app on the same
        redirect — replays it into our callback. The secret does not help,
        because our own server does the exchange. The verifier does: the
        provider will only exchange a code for the client that presented
        the matching challenge when the code was requested.
        """
        return secrets.token_urlsafe(64)

    @staticmethod
    def pkce_challenge(verifier: str) -> str:
        """The S256 challenge for a verifier.

        S256, never `plain`. `plain` sends the verifier itself to the
        authorization endpoint, which puts it in the same places the code
        ends up — so it defends against nothing.
        """
        digest = hashlib.sha256(verifier.encode('ascii')).digest()
        return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

    @staticmethod
    def generate_nonce() -> str:
        """A nonce for one login.

        Stored alongside the CSRF state and compared against the ID
        token's claim on the way back. Without it, a token the attacker
        obtained from the same IdP by other means could be replayed into
        our callback.
        """
        return secrets.token_urlsafe(24)

    async def authorize_url(self, *, state: str, nonce: str, code_challenge: str | None = None) -> str:
        """Where to send the browser to start a login."""
        document = await self.discover()
        endpoint = document.get('authorization_endpoint')
        if not endpoint:
            raise OidcError('The OIDC discovery document has no authorization_endpoint')

        parameters = {
            'response_type': 'code',
            'client_id': self._client_id,
            'redirect_uri': self._redirect_uri,
            'scope': settings.oidc_scopes,
            'state': state,
            'nonce': nonce,
        }
        if code_challenge:
            parameters['code_challenge'] = code_challenge
            parameters['code_challenge_method'] = 'S256'

        return f'{endpoint}?{urlencode(parameters)}'

    async def exchange(self, *, code: str, nonce: str, code_verifier: str | None = None) -> OidcClaims:
        """Turn an authorization code into a validated identity.

        ``nonce`` is the one generated for this login; a token carrying
        anything else is rejected. ``code_verifier`` is the PKCE secret
        whose challenge went out with the authorization request.
        """
        self._require_configured()
        document = await self.discover()
        endpoint = document.get('token_endpoint')
        if not endpoint:
            raise OidcError('The OIDC discovery document has no token_endpoint')

        form = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self._redirect_uri,
            'client_id': self._client_id,
        }
        if code_verifier:
            form['code_verifier'] = code_verifier

        # How to present the client secret. `client_secret_post` was
        # hardcoded, which works with most providers and not with all —
        # the spec's *default* is `client_secret_basic`, and a provider
        # registered for basic only answers 401 with a message about the
        # client, which reads like a wrong secret rather than a wrong
        # method. Discovery says which are supported, so ask.
        supported = document.get('token_endpoint_auth_methods_supported') or ['client_secret_basic']
        # `USE_CLIENT_DEFAULT` rather than `None`: httpx treats an explicit
        # `auth=None` as "override any client-level auth with nothing",
        # which is the same thing here but is not what the type says.
        auth: tuple[str, str] | UseClientDefault = USE_CLIENT_DEFAULT
        if 'client_secret_post' in supported:
            form['client_secret'] = self._client_secret
        elif 'client_secret_basic' in supported:
            auth = (self._client_id, self._client_secret)
        else:
            raise OidcError(
                f'This provider supports only {supported} for client authentication at the token '
                'endpoint; igni can present a secret as client_secret_post or client_secret_basic.'
            )

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(
                    endpoint,
                    data=form,
                    auth=auth,
                    headers={'Accept': 'application/json'},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(f'The OIDC token exchange failed: {exc}') from exc

        id_token = payload.get('id_token')
        if not id_token:
            # Without an ID token there is no verifiable identity — only
            # an access token, which says what we may do and not who we
            # are. Falling back to userinfo here would accept an
            # unsigned assertion.
            raise OidcError('The OIDC token response carried no id_token')

        return await self.validate_id_token(id_token, nonce=nonce)

    async def validate_id_token(self, id_token: str, *, nonce: str) -> OidcClaims:
        """Verify an ID token and read the identity out of it.

        See the module docstring for what is checked and why. The order
        matters only in that everything is checked before any claim is
        trusted.
        """
        keys = await self._jwks_keys(force=self._kid_is_unknown(id_token, await self._jwks_keys()))
        try:
            claims = jose.decode(
                id_token,
                keys,
                algorithms=ID_TOKEN_ALGORITHMS,
                audience=self._client_id,
                issuer=self._issuer,
                require=('exp', 'iss', 'aud'),
            )
        except jose.TokenError as exc:
            raise OidcError(f'The OIDC id_token failed validation: {exc}') from exc

        # Nothing checks nonce for us; this is the replay guard and it has
        # to be explicit.
        if claims.get('nonce') != nonce:
            raise OidcError('The OIDC id_token nonce does not match this login')

        return self._claims_from(claims)

    @staticmethod
    def _kid_is_unknown(id_token: str, keys: dict[str, Any]) -> bool:
        """Whether this token was signed by a key we have not seen.

        Key rotation is the operational failure this exists for. JWKS is
        cached for an hour, so without this a provider that rotates its
        signing key refuses every login until the cache expires — and the
        error it produces is "signature verification failed", which sends
        an operator looking for a wrong secret rather than a stale cache.

        Conservative by construction: it only reports true when the token
        names a `kid` and the cached set demonstrably does not contain it.
        An unreadable header, or a token with no `kid`, reports false and
        the normal validation runs. This decides whether to *refetch*, so
        being wrong here is at worst one extra HTTP request — but it is
        deliberately not a path that can be pushed into refetching on
        every request, which would put the provider on the critical path
        of every login and hand an attacker a way to hammer it.
        """
        try:
            header = jose.unverified_header(id_token)
        except jose.TokenError:
            return False

        kid = header.get('kid')
        if not kid:
            return False

        known = {key.get('kid') for key in keys.get('keys', []) if isinstance(key, dict)}
        return bool(known) and kid not in known

    def _claims_from(self, claims: dict[str, Any]) -> OidcClaims:
        """Map provider claims onto our identity, honouring configuration.

        The claim names are configurable because providers disagree:
        most put the address in ``email``, some in ``preferred_username``
        or ``upn``. Guessing wrongly means every user signs in as
        somebody with no address.
        """
        subject = claims.get('sub')
        if not subject:
            raise OidcError('The OIDC id_token has no sub claim')

        email = claims.get(settings.oidc_email_claim)
        if not email:
            raise OidcError(
                f'The OIDC id_token has no {settings.oidc_email_claim!r} claim; '
                'set oidc_email_claim to whichever claim your provider uses'
            )

        return OidcClaims(
            subject=str(subject),
            email=str(email),
            # Absent means unknown, not verified. Treating a missing
            # claim as verified would let an IdP that does not verify
            # addresses silently take over accounts by email match.
            email_verified=bool(claims.get('email_verified', False)),
            full_name=str(claims[settings.oidc_name_claim]) if claims.get(settings.oidc_name_claim) else None,
        )
