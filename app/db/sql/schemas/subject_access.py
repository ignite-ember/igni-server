"""What the subject access reads return.

`SubjectAccessDB.things_they_created` returned a `dict[str, Any]` with
twenty-two keys that never change: one per kind of thing a person can
author, own, register or decide. A fixed key set is a model, and making
each group a required field means a query added to the read without a
field here — or a field here with no query — fails on the first export
instead of shipping a report that quietly lacks a group.

The rows inside each group stay `Rows` (see its note): the envelope is
a contract, the rows are an inventory.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

#: One table's rows, as the database layer returns them: the column set
#: of whatever table was asked about. An inventory, not a contract —
#: typing these would mean a model per table, maintained in step with
#: every migration, on a path where being out of step means
#: under-reporting somebody's data.
Rows = list[dict[str, Any]]


class ThingsTheyCreated(BaseModel):
    """Everything one person authored, owned, registered or decided in one organisation.

    The field order is the wire order: it matches the order
    `SubjectAccessDB.things_they_created` reads them in, so the JSON is
    the same as when this was a dict. Adding a group means adding a
    field here **and** its query there — every field is required, so
    forgetting either side fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')

    groups: Rows
    group_entries: Rows
    group_extensions: Rows
    apps: Rows
    app_extensions: Rows
    vm_profiles: Rows
    vm_profile_requests: Rows
    vm_commands: Rows
    vm_jobs: Rows
    events: Rows
    event_attachments: Rows
    vm_credentials: Rows
    api_keys: Rows
    producer_volumes: Rows
    mcp_approvals: Rows
    notifications: Rows
    producer_exports: Rows
    revocations: Rows
    legal_holds: Rows
    app_legal_holds: Rows
    producer_feed: Rows
    inference_nodes: Rows
