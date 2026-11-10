"""Why the domain store, or admission to an account, refuses. The caller decides what HTTP makes of each."""

from __future__ import annotations


class DomainTaken(Exception):
    """A domain another organisation already owns. `domain` is ``None`` when one of several was."""

    def __init__(self, domain: str | None = None) -> None:
        if domain is None:
            super().__init__('One of those domains already belongs to another organisation.')
        else:
            super().__init__(f'{domain} already belongs to another organisation.')
        self.domain = domain


class DomainNotHeld(LookupError):
    """The organisation does not own the domain it asked to release."""

    def __init__(self, domain: str) -> None:
        super().__init__('No such domain here.')
        self.domain = domain


class DomainInUse(Exception):
    """A member's address is on the domain the organisation asked to release."""

    def __init__(self, domain: str) -> None:
        super().__init__(f'Members of this organisation use {domain}. Remove them first.')
        self.domain = domain


class AdmissionRefused(PermissionError):
    """An address that may not have an account here. The message is the person's to read."""


class NotACompanyAddress(AdmissionRefused):
    """The address is on no organisation's domain."""

    def __init__(self) -> None:
        super().__init__(
            "This address isn't on a domain any organisation here uses. Sign up with your company address."
        )


class NotSetUpYet(AdmissionRefused):
    """Somebody signing up before the first account has set the deployment up."""

    def __init__(self) -> None:
        super().__init__(
            "This deployment isn't set up yet. Its first account creates the organisation; ask whoever installed it."
        )
