"""No response hands back a credential, and the shapes cannot drift into it.

A systematic sweep of every route's response model for secret-shaped
field names. It came back clean apart from one thing, and the value of
these tests is mostly that the sweep now runs on every commit rather than
once, by me, today.

What the sweep found:

* ``token_last_4`` on the GitLab integration response — deliberate and
  safe, four characters so a human can tell two tokens apart;
* ``token`` on ``POST /internal/v1/git-credentials`` — a real credential,
  and properly gated: the internal router is mounted with
  ``dependencies=[Depends(require_service_token)]``, so an endpoint added
  there cannot be unauthenticated by omission. A valid *user* token is
  rejected too, which is the distinction that matters;
* ``access_token`` on ``AuthResponse`` — dead, never populated, and
  removed. See that class for why a nullable token field on a
  "send me a code" response is worse than harmless.

Everything else matching the pattern was a token *count*.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

#: Field names that would be a credential if a response carried one.
_SECRETISH = re.compile(r'secret|token|password|private_key|api_key|credential|pem', re.I)

#: Names that match the pattern and are not credentials.
_ALLOWED = {
    # Login endpoints exist to return a token.
    'access_token',
    'token_type',
    # Token *counts*, from usage accounting.
    'prompt_tokens',
    'completion_tokens',
    'total_tokens',
    'input_tokens',
    'output_tokens',
    'total_input_tokens',
    'total_output_tokens',
    # Booleans and timestamps: they describe a credential without
    # carrying one, which is how a UI shows "configured" or "expiring
    # soon" without ever handling the value.
    'needs_credentials',
    'has_api_key',
    'token_expires_at',
    # Four characters, so an admin can tell two tokens apart.
    'token_last_4',
    # The internal git-credential mint, gated at the router.
    'token',
}


def _secret_shaped_fields() -> list[tuple[str, str]]:
    from app.main import app

    seen: set[type] = set()
    found: list[tuple[str, str]] = []

    def walk(model: type, where: str, depth: int = 0) -> None:
        if depth > 4 or model in seen:
            return
        seen.add(model)
        for name, field in getattr(model, 'model_fields', {}).items():
            if _SECRETISH.search(name) and name not in _ALLOWED:
                found.append((where, name))
            annotation = field.annotation
            for candidate in (annotation, *getattr(annotation, '__args__', ())):
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    walk(candidate, f'{where}.{name}', depth + 1)

    for route in app.routes:
        model = getattr(route, 'response_model', None)
        methods = getattr(route, 'methods', set()) - {'HEAD', 'OPTIONS'}
        where = f'{sorted(methods)[0] if methods else "?"} {getattr(route, "path", "?")}'
        candidates = [model] if isinstance(model, type) else list(getattr(model, '__args__', ()) or [])
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                walk(candidate, where)

    return found


class TestTheSweep:
    def test_no_unexpected_secret_shaped_response_field(self):
        """The standing check. A new response field named like a
        credential has to be either not one, or added to ``_ALLOWED``
        with a reason — which is a moment's thought at the point somebody
        could otherwise ship a leak."""
        offenders = _secret_shaped_fields()

        assert not offenders, (
            'these response fields are named like credentials. Rename them, stop '
            f'returning them, or allowlist them with a reason: {offenders}'
        )

    def test_the_sweep_can_actually_find_something(self):
        """A sweep that cannot fail is a comfort, not a check."""
        from pydantic import BaseModel as PydanticModel

        class Leaky(PydanticModel):
            private_key: str

        assert _SECRETISH.search('private_key')
        assert 'private_key' not in _ALLOWED
        assert Leaky.model_fields  # the shape the sweep walks


class TestAuthResponseCarriesNoToken:
    def test_the_field_is_gone(self):
        """Removed rather than left at None. See ``AuthResponse`` for why
        a nullable token on a "send me a code" response is a trap."""
        from app.api.base_v1.schema.auth import AuthResponse

        assert 'access_token' not in AuthResponse.model_fields

    async def test_sending_a_code_returns_no_token(self, client, db):
        response = await client.post('/send-sign-up-code', json={'email': 'new@x.com'})

        assert response.status_code == 200
        assert 'access_token' not in response.json()
        # And no code either — the whole point.
        assert not re.search(r'\b\d{6}\b', response.text)


