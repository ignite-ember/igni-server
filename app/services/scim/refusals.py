"""Why provisioning refuses somebody. The SCIM router decides what HTTP makes of it."""

from __future__ import annotations


class OffEveryDomain(Exception):
    """The address is on no domain any organisation here owns, so no account is created for it."""

    def __init__(self, address: str) -> None:
        super().__init__(
            f"{address} isn't on a domain any organisation here owns. A super admin adds the domain in the portal."
        )
