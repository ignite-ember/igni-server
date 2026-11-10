"""OAuth errors, in the shape RFC 6749 §5.2 gives them."""

from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse


class OAuthError(Exception):
    """An error the token endpoint answers with `{error, error_description}`.

    A client refused for its registration's state — waiting for approval,
    or revoked — is also told that state as `client_state`, the value the
    registration answer gives as `state`, so a program need not read the
    English description to tell "wait" from "give up".
    """

    def __init__(
        self,
        error: str,
        description: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        client_state: str | None = None,
    ) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code
        self.client_state = client_state

    @classmethod
    def invalid_client(cls, description: str, *, client_state: str | None = None) -> OAuthError:
        return cls('invalid_client', description, status_code=status.HTTP_401_UNAUTHORIZED, client_state=client_state)

    @classmethod
    def invalid_grant(cls, description: str) -> OAuthError:
        return cls('invalid_grant', description)

    @classmethod
    def invalid_request(cls, description: str) -> OAuthError:
        return cls('invalid_request', description)

    @classmethod
    def invalid_target(cls, description: str) -> OAuthError:
        return cls('invalid_target', description)

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=self._body(),
            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
        )

    def _body(self) -> dict[str, str]:
        body = {'error': self.error, 'error_description': self.description}
        if self.client_state is not None:
            body['client_state'] = self.client_state
        return body
