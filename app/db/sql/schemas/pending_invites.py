"""The pending invitations to claim when an account is made, and the ones they supersede."""

from __future__ import annotations

from collections.abc import Sequence

from app.db.sql.models.org_membership import OrgMembership


class PendingInvites:
    """Pending invitations for one address, split into the one to keep per org and the rest.

    Used by :meth:`Admission.admit` (``app.db.sql.admission``), and kept
    apart so it can be tested without a database. The state it guards
    against — two pending rows for one address in one org — can no longer
    be *created*, because ``uq_org_memberships_pending_invite`` refuses it;
    and reproducing it in a test by dropping the index deadlocks, since the
    DDL lock blocks the session the claim opens. Pure logic, tested directly.

    Newest wins. Re-inviting somebody is an admin restating current
    intent, so the last invite is the live one. Keeping the
    higher-privilege row instead would turn a corrective demotion into a
    silent grant, and keeping the oldest would ignore the admin's most
    recent decision.

    Ties on ``created_at`` are broken by ``id`` so the outcome is
    deterministic rather than dependent on scan order.
    """

    def __init__(self, rows: Sequence[OrgMembership]) -> None:
        self._rows = list(rows)

    def newest_per_org(self) -> tuple[dict[str, OrgMembership], list[OrgMembership]]:
        """The row to keep for each org, by org id, and every row it supersedes."""
        keep: dict[str, OrgMembership] = {}
        superseded: list[OrgMembership] = []
        for row in sorted(self._rows, key=lambda r: (r.created_at, r.id), reverse=True):
            org_id = str(row.org_id)
            if org_id in keep:
                superseded.append(row)
            else:
                keep[org_id] = row
        return keep, superseded
