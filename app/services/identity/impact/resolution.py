"""What settling a set of extensions' collisions comes to: who has each claim, and what each loses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Collision(BaseModel):
    """Two or more extensions wanting one thing, and which of them has it."""

    model_config = ConfigDict(frozen=True)

    claim: str
    extensions: list[str]
    #: The highest in the priority order. `None` only for an exclusive
    #: override a higher extension's edits switched off, with no other
    #: override left — then nobody holds the whole prompt.
    winner: str | None = None
    #: Sorted: the extensions that lost something in it, each by being
    #: lower in priority than another in it.
    losers: list[str] = Field(default_factory=list)


class SwitchedOff(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim: str
    reason: str


class Dropped(SwitchedOff):
    """An edit that lands nowhere, the override that leaves it out, and why."""

    #: The extension whose override has no section the edit names.
    by: str


class ResolvedImpact(BaseModel):
    collisions: list[Collision] = Field(default_factory=list)
    off: dict[str, list[SwitchedOff]] = Field(default_factory=dict)
    #: Inserts and replacements aimed at a section another extension's
    #: override does not have: listed, not a collision.
    dropped: dict[str, list[Dropped]] = Field(default_factory=dict)

    def allows(self, extension_id: str, claim: str) -> bool:
        return all(entry.claim != claim for entry in self.off.get(extension_id, []))

    def involving(self, extension_id: str) -> list[Collision]:
        """Every collision this extension is in."""
        return [c for c in self.collisions if extension_id in c.extensions]

    def switch_off(self, extension_id: str, *, claim: str, reason: str) -> None:
        self.off.setdefault(extension_id, []).append(SwitchedOff(claim=claim, reason=reason))

    def drop(self, extension_id: str, dropped: Dropped) -> None:
        self.dropped.setdefault(extension_id, []).append(dropped)


__all__ = ['Collision', 'Dropped', 'ResolvedImpact', 'SwitchedOff']
