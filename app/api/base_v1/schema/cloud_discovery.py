"""What `GET /v1/me/cloud` answers: whether a person has a cloud igni to use from the desktop, and where.

The contract is igni-sdk's `contracts/cloud-discovery.schema.json`
(igni-core `docs/design/local-cloud-switch.md` §2.1). Additive only:
clients ignore fields they do not know, read an unknown `cloud` as
`unavailable` and an unknown `mode` as `local`, and show an unknown
`reason` as "unavailable".
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from app.db.sql.models.org_group import GroupMode
from app.db.sql.models.person_event_owner import EventsOwner

#: Why there is no cloud igni for this person. An open set.
CloudUnavailableReason = Literal['fleet_not_configured', 'group_local_only', 'inactive', 'not_licensed']


class CloudGroup(BaseModel):
    """The person's group, for display."""

    id: str
    name: str


class CloudDiscovery(BaseModel):
    """A person's cloud igni, as far as igni server knows: whether, where, and who owns their events.

    Not its live state — asleep, waking, running, signed in: the gateway's
    own `GET /v1/me` answers that, to a token it already accepts.
    """

    cloud: Literal['available', 'unavailable'] = Field(
        description='Whether this person has a cloud igni they may use from the desktop'
    )
    reason: CloudUnavailableReason | None = Field(description='Why it is unavailable; null when it is available')
    gateway_url: str | None = Field(
        description="Fleet's gateway (the same value as `igni_gateway`); null when Fleet is not configured"
    )
    mode: GroupMode = Field(description="Where igni runs for the person's group; the same value the pack carries")
    events_owner: EventsOwner = Field(description="Who pulls this person's service events")
    group: CloudGroup | None = Field(description='For display; null for a person in no group')
    producer: str = Field(description="The gateway's producer id, for logs and support")

    def etag(self) -> str:
        """A validator over the whole answer: it changes exactly when a field does."""
        digest = hashlib.md5(json.dumps(self.model_dump(mode='json'), sort_keys=True).encode(), usedforsecurity=False)
        return f'"{digest.hexdigest()}"'


class SetEventsOwner(BaseModel):
    """`PUT /v1/me/cloud/events-owner`: which side pulls this person's events from now on."""

    owner: EventsOwner


class CloudDiscoveryChange(BaseModel):
    """The answer after the events owner was set, and the owner served before — for the audit row."""

    discovery: CloudDiscovery
    owner_was: EventsOwner
