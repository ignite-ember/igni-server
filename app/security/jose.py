"""Signing and verifying JWTs and JWSs, in one place.

Every token igni signs or checks goes through here, on PyJWT. It replaced
python-jose, which stopped receiving fixes: its last advisory
(PYSEC-2026-4298, an incomplete fix for CVE-2024-33663) has no release
that closes it.

The rules this module keeps for every caller:

- **Asymmetric only.** `decode` and `verify_jws` take an explicit list of
  algorithms and refuse any that is not RSA or EC, so no caller can
  re-open HMAC algorithm confusion by passing `HS256` — the class of bug
  both advisories above are about.
- **Keys arrive as JWKs** (a dict, or a `{"keys": [...]}` set), so each
  key is bound to its own type and never read as an HMAC secret.
- **One error type,** `TokenError`, for anything a token can get wrong.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import jwt
from jwt import PyJWK, PyJWS
from jwt.exceptions import PyJWKError, PyJWTError
from jwt.types import Options

#: The only algorithms a caller may ask to verify with.
ASYMMETRIC = frozenset({'RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512', 'PS256', 'PS384', 'PS512'})


class TokenError(ValueError):
    """The token is malformed, unsigned by the key, expired, or otherwise wrong."""


def _asymmetric(algorithms: Iterable[str]) -> list[str]:
    chosen = list(algorithms)
    if not chosen:
        raise ValueError('name the algorithms to accept')
    refused = [a for a in chosen if a not in ASYMMETRIC]
    if refused:
        raise ValueError(f'refusing to verify with non-asymmetric algorithms: {refused}')
    return chosen


def _candidates(key: Mapping[str, Any], kid: str | None) -> list[PyJWK]:
    """The keys of `key` (one JWK or a set) a token naming `kid` may be checked against."""
    members = key.get('keys')
    jwks = [k for k in members if isinstance(k, Mapping)] if isinstance(members, list) else [key]
    if kid:
        named = [k for k in jwks if k.get('kid') == kid]
        jwks = named or ([] if len(jwks) > 1 else jwks)
    found: list[PyJWK] = []
    for item in jwks:
        try:
            found.append(PyJWK(dict(item)))
        except PyJWKError:
            continue
    return found


def encode(claims: Mapping[str, Any], private_pem: str, *, algorithm: str, headers: Mapping[str, Any]) -> str:
    """Sign `claims` with igni's own private key."""
    return jwt.encode(dict(claims), private_pem, algorithm=_asymmetric([algorithm])[0], headers=dict(headers))


def unverified_header(token: str) -> dict[str, Any]:
    """The header, read without checking anything. Only to choose a key."""
    try:
        return dict(jwt.get_unverified_header(token))
    except PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def unverified_claims(token: str) -> dict[str, Any]:
    """The claims, read without checking anything. Never to trust them."""
    try:
        claims = jwt.decode(token, options={'verify_signature': False})
    except PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    return dict(claims)


def decode(
    token: str,
    key: Mapping[str, Any],
    *,
    algorithms: Iterable[str],
    audience: str | None = None,
    issuer: str | None = None,
    require: Iterable[str] = (),
    verify_aud: bool = True,
) -> dict[str, Any]:
    """The claims of `token`, once its signature, `exp`, `nbf`, `iss` and `aud` are checked.

    `key` is one JWK or a JWK set; in a set, the key named by the token's
    `kid` is used. `audience=None` with `verify_aud` refuses a token that
    carries an `aud`, as an unexpected audience is a token meant for
    someone else.
    """
    allowed = _asymmetric(algorithms)
    kid = unverified_header(token).get('kid')
    candidates = _candidates(key, kid if isinstance(kid, str) else None)
    if not candidates:
        raise TokenError('no key matches this token')
    options: Options = {'require': list(require), 'verify_aud': verify_aud}
    last: Exception | None = None
    for candidate in candidates:
        try:
            return dict(
                jwt.decode(
                    token,
                    candidate,
                    algorithms=allowed,
                    audience=audience,
                    issuer=issuer,
                    options=options,
                )
            )
        except jwt.InvalidSignatureError as exc:
            last = exc
            continue
        except PyJWTError as exc:
            raise TokenError(str(exc)) from exc
    raise TokenError(str(last) if last else 'signature verification failed')


def verify_jws(token: str, key: Mapping[str, Any], *, algorithms: Iterable[str]) -> bytes:
    """The payload of a compact JWS, if `key` (one JWK) signed it."""
    allowed = _asymmetric(algorithms)
    try:
        payload: bytes = PyJWS().decode(token, PyJWK(dict(key)), algorithms=allowed)
        return payload
    except (PyJWTError, PyJWKError) as exc:
        raise TokenError(str(exc)) from exc
