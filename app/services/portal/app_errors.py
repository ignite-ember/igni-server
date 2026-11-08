"""Why the app services refuse a request. `AppRefusals` decides what HTTP makes of each.

A JWK set that cannot be registered is refused as `KeysRefused`, the
identity layer's own error, wherever it is read.
"""

from __future__ import annotations


class AppNotFound(LookupError):
    """No such app — also an app of another organisation."""

    def __init__(self) -> None:
        super().__init__('App not found')


class OwnerNotInOrg(ValueError):
    def __init__(self) -> None:
        super().__init__('The owner must be an active member of the organisation')


class AppKeyTaken(Exception):
    """Another registered client already holds the app's first key.

    *raced* when it was registered between the check and the add, which the
    portal has always answered more briefly.
    """

    def __init__(self, *, raced: bool = False) -> None:
        super().__init__(
            'Another client already registered this key'
            if raced
            else 'Another client already registered this key. Each app generates a key pair of its own.'
        )


class ExtensionNotShipped(LookupError):
    def __init__(self) -> None:
        super().__init__('No approved service ships this extension')
