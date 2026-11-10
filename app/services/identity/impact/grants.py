"""What an extension may be granted beyond its contributions: service grants and igni's hook events."""

from __future__ import annotations

from enum import StrEnum


class ServiceGrant(StrEnum):
    """What a service may do towards producers' agents beyond a plain event: `permissions.service`.

    `docs/service-identity.md` §15. Shown to the super admin who approves
    the service, and checked by igni server on every item the service sends.
    """

    #: Post text it wrote, as the extension's message.
    MESSAGES = 'messages'
    #: Upload files and name them on items.
    ATTACHMENTS = 'attachments'
    #: Name a thread (each opens a chat), open chats, act on them.
    CHATS = 'chats'


#: igni's hook events (igni's `docs/HOOKS.md`) an extension may answer.
HOOK_EVENTS: frozenset[str] = frozenset(
    {
        'PreToolUse',
        'PostToolUse',
        'PostToolUseFailure',
        'UserPromptSubmit',
        'SessionStart',
        'SessionEnd',
        'Stop',
        'SubagentStart',
        'SubagentStop',
        'Notification',
        'PreCompact',
        'PostCompact',
        'InstructionsLoaded',
        'TaskCreated',
        'TaskCompleted',
        'StopFailure',
        'PermissionRequest',
        'PermissionDenied',
        'PlanSubmit',
    }
)

#: The hook events whose answer decides something: the extension may stop
#: the event or change it. It is only told the rest.
DECIDING_HOOKS: frozenset[str] = frozenset({'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'PlanSubmit'})


__all__ = ['DECIDING_HOOKS', 'HOOK_EVENTS', 'ServiceGrant']
