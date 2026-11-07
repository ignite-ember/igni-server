"""What the group services refuse, as typed domain errors.

The store's own refusals live beside it in `app.db.sql.schemas.group`;
these are the ones only the services decide. Neither layer picks a status
code: `app.services.portal.group_refusals` turns both into the portal's
responses, using each message as written here.
"""

from __future__ import annotations

from app.db.sql.schemas.group import GroupConflict


class GroupIsStandard(GroupConflict):
    """The Default group was asked to change.

    It is the copy of what igni ships and the thing every other group is
    started from, so it is immutable: the way to have your own version of
    it is to copy it. A conflict rather than a permission failure — the
    admin is not lacking a right, the resource cannot change, and the
    portal needs to tell those apart to say anything useful.
    """

    def __init__(self) -> None:
        super().__init__(
            'This group holds what igni ships and cannot be changed. Create a group from it to make your own version.'
        )


class ModelNotServed(GroupConflict):
    """A group or agent names a model no node in the chat pool answers to."""

    def __init__(self, model: str, served: set[str]) -> None:
        super().__init__(f'No inference node serves {model!r}. Available: {", ".join(sorted(served)) or "none"}.')
        self.model = model
        self.served = served


class NotAnOrgMember(LookupError):
    """The person to place is not an active member of the organisation."""

    def __init__(self) -> None:
        super().__init__('This user is not a member of the organization.')


class EntryRefused(ValueError):
    """An entry request breaks one of the rules for what a group can hold."""


class UnknownEntryKind(EntryRefused):
    """The kind is not one a group can hold."""

    def __init__(self, valid: set[str]) -> None:
        super().__init__(f'Invalid kind. Must be one of: {", ".join(sorted(valid))}')


class KindTakesNoModel(EntryRefused):
    """A model was named on a kind that does not run against one."""

    def __init__(self, kind: str) -> None:
        super().__init__(f'Only agents can name a model; {kind!r} cannot.')
        self.kind = kind


class UnknownContentType(EntryRefused):
    """The content type is not one igni reads at all."""

    def __init__(self, valid: set[str]) -> None:
        super().__init__(f'Invalid content_type. Must be one of: {", ".join(sorted(valid))}')


class KindNeedsContentType(EntryRefused):
    """The content type is not the one format igni reads for this kind."""

    def __init__(self, kind: str, expected: str) -> None:
        super().__init__(f"Kind '{kind}' requires content_type '{expected}' (igni's loader only parses that format).")
        self.kind = kind
        self.expected = expected


class UnsupportedPluginSource(EntryRefused):
    """A plugin's source is not a URL igni can install from."""

    def __init__(self) -> None:
        super().__init__('source_url must be an http(s), git, or ssh URL (got a non-URL or unsupported scheme).')
