"""Why the account store refuses. The caller decides what HTTP makes of each."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class PersonalAddress(ValueError):
    """An address at a personal mail provider, which may not open an account here.

    The message names the reason. "Invalid email" on a perfectly valid
    gmail address reads as a bug in the form, and the person retypes it
    twice before giving up.
    """

    def __init__(self) -> None:
        super().__init__(
            'Please sign up with your work address. igni shares documents with '
            'the people at your company, and it works out who they are from the '
            'domain of your email — which a personal address cannot tell it.'
        )


class AccountNotFound(LookupError):
    """No account has that id."""

    def __init__(self, detail: str = 'User not found') -> None:
        super().__init__(detail)


class AccountConflict(Exception):
    """The change contradicts an account already stored."""


class AddressTaken(AccountConflict):
    """Another account already has this address.

    Raised by the store's own pre-check and, for two submissions of one
    address racing past it, by the unique index named here. Recognised
    by that name rather than by a phrase in the driver's message: the
    message for a missing address names the same column, and is not a
    duplicate.
    """

    constraint = 'users_email_key'

    def __init__(self, detail: str = 'User already registered') -> None:
        super().__init__(detail)

    @classmethod
    def raised_by(cls, exc: IntegrityError) -> bool:
        return cls.constraint in str(exc.orig)


class AlreadyInAnOrganisation(AccountConflict):
    """The account is in an organisation, whose domain its address decides, so the address stays."""

    def __init__(self) -> None:
        super().__init__('Your address decides your organisation, so it is not changed here.')


class LastSuperAdmin(Exception):
    """Removing this super admin would leave the deployment with none.

    Nobody could then create an organisation or make anybody else a
    super admin, and there is no way back from inside the product.
    """
