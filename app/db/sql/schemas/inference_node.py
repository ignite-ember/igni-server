"""What can be changed about a registered node, and what can go wrong.

`InferenceNodeDB.update` took `**changes` and `setattr`'d whatever it
was handed, dispatching on field-name string literals along the way. A
misspelled keyword did not fail — it set a new attribute on the ORM
object, which SQLAlchemy ignores, so the edit silently did nothing.

The caller that exists today builds those kwargs from a validated
Pydantic model, so the hazard was never reached in practice. It was
reachable by the next caller, which is the point of naming the contract
rather than accepting a mapping.
"""

from __future__ import annotations

from pydantic import BaseModel


class NodeIdAlreadyRegistered(Exception):
    """Two nodes tried to register under one id.

    A domain error rather than an `HTTPException`, so the persistence
    layer does not decide a status code and a caller that is not an HTTP
    request — a startup task, a test — gets something it can catch by
    name.

    Ordinary rather than exceptional: registration is driven by a node's
    own startup, so two instances racing on the same id is the normal
    way to reach this. The pre-check plus insert is a check-then-act,
    and eight concurrent registrations produced one 201 and seven 500s
    before the race was handled here.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(f"A node with id '{node_id}' is already registered.")
        self.node_id = node_id


class NodeChanges(BaseModel):
    """A partial edit to a node. Absent means unchanged.

    `api_key` carries the one asymmetry worth stating: absent leaves the
    stored credential alone and an explicit empty string clears it. The
    portal never sends the key back, so treating absent as "clear" would
    wipe it on every edit of anything else.
    """

    base_url: str | None = None
    models: list[str] | None = None
    api_key: str | None = None
    max_concurrent: int | None = None
    pools: list[str] | None = None
    multimodal: bool | None = None
    enabled: bool | None = None
    region: str | None = None
    project: str | None = None
    # As ``api_key``: absent leaves it, an empty string clears it.
    credential: str | None = None
    model_ids: dict[str, str] | None = None
    auth: str | None = None
    auth_settings: dict[str, str] | None = None

    def clears_api_key(self) -> bool:
        return self.api_key == ''

    def clears_credential(self) -> bool:
        return self.credential == ''
