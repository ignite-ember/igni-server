"""Why authentication refuses. `AuthRefusals` decides what HTTP makes of each."""

from __future__ import annotations


class AuthRefused(Exception):
    """Authentication cannot go ahead; the message is the caller's to read."""


class InvalidCredentials(AuthRefused):
    """No account, no password, or the wrong one: deliberately indistinguishable."""

    def __init__(self) -> None:
        super().__init__('Invalid email or password')


class InvalidSession(AuthRefused):
    """The token names nobody."""

    def __init__(self) -> None:
        super().__init__('Invalid or expired token')


class NotAMember(AuthRefused):
    """The person is not an active member of the organisation they named."""

    def __init__(self) -> None:
        super().__init__('Not a member of this organization')
