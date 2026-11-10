"""Why a VM profile request cannot be asked for, read or decided.

`VmProfileRequestRefusals` decides what HTTP makes of each.
"""

from __future__ import annotations


class VmProfileRequestRefused(Exception):
    """The request cannot go ahead; the message is the caller's to read."""


class NotTheApp(VmProfileRequestRefused):
    """The caller is not the app it asks or reads for: an app asks for itself only."""

    def __init__(self) -> None:
        super().__init__("Only the app itself asks for its VM's profile")


class BeyondCaps(VmProfileRequestRefused):
    """What is asked for is more than the platform's caps allow; the message says which."""


class NoAppRequest(VmProfileRequestRefused):
    """The app has asked for no profile."""

    def __init__(self) -> None:
        super().__init__('This app has asked for no VM profile')


class NoSuchRequest(VmProfileRequestRefused):
    """No request of this id in the organisation."""

    def __init__(self) -> None:
        super().__init__('No such VM profile request')


class RequestChanged(VmProfileRequestRefused):
    """The version the admin read is no longer the request's."""

    def __init__(self) -> None:
        super().__init__('The request has changed since it was read: read it again before approving')


class AlreadyApproved(VmProfileRequestRefused):
    def __init__(self) -> None:
        super().__init__('This request is approved already')


class AlreadyDecided(VmProfileRequestRefused):
    """Only a pending request is rejected."""

    def __init__(self, state: str) -> None:
        super().__init__(f'This request is {state} already')


class ProfileNamesTaken(VmProfileRequestRefused):
    """Every name an approval would give its profile is another profile's."""

    def __init__(self, base: str) -> None:
        super().__init__(f"Profiles named '{base}' are all taken: rename one")
