"""Why an extension upload, or a super admin's approval of one, is refused."""

from __future__ import annotations


class ExtensionRefused(ValueError):
    """The upload cannot be accepted; the message says why."""


class ExtensionConflict(RuntimeError):
    """Another approved service already ships an extension with this id."""
