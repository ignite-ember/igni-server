"""Why the team store refuses a membership change. The caller decides what HTTP makes of each."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class MembershipNotFound(LookupError):
    """Nobody with that id is a member of that organisation."""

    def __init__(self) -> None:
        super().__init__('Member not found')


class MembershipConflict(Exception):
    """The change contradicts a membership already stored."""


class InvitationPending(MembershipConflict):
    """The address already has a pending invitation to this organisation.

    The race backstop for `TeamService.invite_member`'s own pre-check,
    which is a check-then-act. Every invitation mints a fresh synthetic
    `user_id`, so the `(org_id, user_id)` index cannot catch a duplicate;
    the partial index named here can. Recognised by that name rather than
    by a phrase in the driver's message.
    """

    constraint = 'uq_org_memberships_pending_invite'

    def __init__(self, email: str) -> None:
        super().__init__(f'{email} already has a pending invitation to this organization.')
        self.email = email

    @classmethod
    def raised_by(cls, exc: IntegrityError) -> bool:
        return cls.constraint in str(exc.orig)


class AlreadyAMember(MembershipConflict):
    """The account already has a membership in this organisation."""

    def __init__(self) -> None:
        super().__init__('This account is already in this organization.')
