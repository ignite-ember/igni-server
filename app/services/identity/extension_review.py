"""What the Services page shows about one service's extension: its impact, and who wins where it collides.

Two extensions that want one thing collide, and the one higher in the
super admin's priority order has it (`app.services.identity.impact`,
`app.services.identity.priority`). Nothing is decided here and nothing
is held back: the page shows each collision this extension is in — the
winner and the losers — so a super admin who wants another outcome
reorders the priority instead.

The collisions shown are among every service's extension that is not
revoked — distributed or still pending approval — so a clash is visible
before either side reaches anybody, in the order the page offers for
them all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.sql.service_extensions import ServiceExtensionDB
from app.services.identity.impact import (
    Collision,
    Dropped,
    ExtensionImpact,
    IgniCommands,
    ImpactResolver,
    PromptTargets,
    SwitchedOff,
)
from app.services.identity.priority import ExtensionPriority


class ExtensionReview(BaseModel):
    """One service's extension as a super admin reviews it."""

    impact: ExtensionImpact
    #: Prompt targets this igni does not have: the text would land nowhere.
    unknown_targets: list[str] = Field(default_factory=list)
    #: Commands it declares that are igni's own, so never run. Refused at
    #: upload; here for a version uploaded before igni had the command.
    igni_commands: list[str] = Field(default_factory=list)
    #: Every collision it is in, with the winner and the losers by priority.
    collisions: list[Collision] = Field(default_factory=list)
    #: What it does not get, and why — each reason begins
    #: `lower priority than <extension>`.
    switched_off: list[SwitchedOff] = Field(default_factory=list)
    #: Its edits that would land nowhere: another extension overrides
    #: that prompt, and its override has no section the edit names.
    dropped: list[Dropped] = Field(default_factory=list)
    #: Other extensions' edits its own overrides leave out, by extension.
    drops_others: dict[str, list[Dropped]] = Field(default_factory=dict)


class ExtensionReviews:
    def __init__(self, store: ServiceExtensionDB | None = None, priority: ExtensionPriority | None = None) -> None:
        self._store = store or ServiceExtensionDB()
        self._priority = priority or ExtensionPriority(extensions=self._store)

    async def review(self, client_id: str) -> ExtensionReview | None:
        extension = await self._store.of(client_id)
        if extension is None or not extension.impact:
            return None
        impact = ExtensionImpact.model_validate(extension.impact)
        impacts = [ExtensionImpact.model_validate(row.impact) for row in await self._store.reviewable() if row.impact]
        if all(other.extension_id != impact.extension_id for other in impacts):
            impacts.append(impact)
        outcome = ImpactResolver(
            impacts, await self._priority.reviewable(), reserved=IgniCommands.igni().claims()
        ).resolve()
        return ExtensionReview(
            impact=impact,
            unknown_targets=impact.unknown_targets(PromptTargets.igni()),
            igni_commands=IgniCommands.igni().clashes(impact),
            collisions=outcome.involving(impact.extension_id),
            switched_off=outcome.off.get(impact.extension_id, []),
            dropped=outcome.dropped.get(impact.extension_id, []),
            drops_others={
                ext: mine
                for ext, drops in outcome.dropped.items()
                if (mine := [d for d in drops if d.by == impact.extension_id])
            },
        )


__all__ = ['ExtensionReview', 'ExtensionReviews']
