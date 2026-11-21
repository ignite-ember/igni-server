"""Who pulls a person's service events: igni on their computer, or the gateway for their cloud VM.

Both used to lease from one queue, so the first to ask had an event for
two minutes and the side without the extension acknowledged it as not
handled. igni server now serves exactly one side per person (igni-core
`docs/design/local-cloud-switch.md` §7):

| The group's mode | The owner |
|---|---|
| `local` | `local` |
| `cloud` | `cloud` |
| `local_and_cloud` | what the person last chose, `local` until they first switch |

The desktop's pull while the owner is `cloud` is refused
(`EventsOwnedElsewhere`); the gateway's pull while it is `local` gets
nothing, and no VM is woken for those events. A change of owner leaves
leases alone: what the old side leased and did not acknowledge comes
back after the lease, to the new owner.
"""

from __future__ import annotations

from app.db.sql.cloud_standing import CloudStandingDB
from app.db.sql.models.org_group import CLOUD, GroupMode, LOCAL, LOCAL_AND_CLOUD
from app.db.sql.models.person_event_owner import CLOUD_OWNER, EventsOwner, LOCAL_OWNER
from app.db.sql.schemas.cloud_standing import CloudStanding
from app.services.cloud.events_owner_errors import ModeForbidsOwner


class EventOwners:
    def __init__(self, *, db: CloudStandingDB | None = None) -> None:
        self._db = db or CloudStandingDB()

    @staticmethod
    def effective(standing: CloudStanding) -> EventsOwner:
        """The side served: the mode's, where it allows one; else the person's choice, else `local`."""
        if standing.mode == LOCAL:
            return LOCAL_OWNER
        if standing.mode == CLOUD:
            return CLOUD_OWNER
        return standing.chosen or LOCAL_OWNER

    @classmethod
    def allows(cls, mode: GroupMode, owner: EventsOwner) -> bool:
        """Whether a person in a group of this mode may have this owner."""
        return mode == LOCAL_AND_CLOUD or cls.effective(CloudStanding(mode=mode)) == owner

    async def of(self, org_id: str, user_id: str) -> EventsOwner:
        return (await self.of_people(org_id, [user_id]))[user_id]

    async def of_people(self, org_id: str, user_ids: list[str]) -> dict[str, EventsOwner]:
        standing = await self._db.standing(org_id, user_ids)
        return {user_id: self.effective(found) for user_id, found in standing.items()}

    async def choose(self, org_id: str, user_id: str, owner: EventsOwner) -> EventsOwner:
        """Make *owner* the person's: `ModeForbidsOwner` if their group's mode does not allow it.

        Returns the owner served before, so the change can be audited.
        """
        standing = (await self._db.standing(org_id, [user_id]))[user_id]
        if not self.allows(standing.mode, owner):
            raise ModeForbidsOwner(owner)
        await self._db.choose(org_id, user_id, owner)
        return self.effective(standing)
