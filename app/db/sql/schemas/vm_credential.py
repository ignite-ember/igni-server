"""What the VM credentials table hands back beyond its rows."""

from __future__ import annotations

from pydantic import BaseModel


class PendingPickup(BaseModel):
    """An app's or a space's credential, taken off its row the one time it is picked up."""

    family_id: str
    #: The credential, still encrypted.
    sealed: str
