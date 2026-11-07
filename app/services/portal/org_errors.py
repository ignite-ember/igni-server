"""Why the organisation service refuses a request. The router decides what HTTP makes of each."""

from __future__ import annotations


class InvalidSlug(ValueError):
    def __init__(self) -> None:
        super().__init__('Slug must be 3-32 chars, lowercase alphanumeric and hyphens, no leading/trailing hyphens')


class NotOrgAdmin(PermissionError):
    def __init__(self) -> None:
        super().__init__('Admin access required for this organization')


class OrgNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__('No such organization.')


class OrgStillHasMembers(Exception):
    """Somebody other than the deleting admin is still active or invited."""

    def __init__(self, count: int) -> None:
        super().__init__(f'Remove all members before deleting. {count} member(s) still in this organization.')
        self.count = count
