"""OAuth errors, in the shape RFC 6749 §5.2 gives them."""

from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse


class OAuthError(Exception):
    """An error the token endpoint answers with `{error, error_description}`."""

    def __init__(self, error: str, description: str, *, status_code: int = status.HTTP_400_BAD_REQUEST) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code

    @classmethod
    def invalid_client(cls, description: str) -> OAuthError:
        return cls('invalid_client', description, status_code=status.HTTP_401_UNAUTHORIZED)

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
            content={'error': self.error, 'error_description': self.description},
            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'},
        )
