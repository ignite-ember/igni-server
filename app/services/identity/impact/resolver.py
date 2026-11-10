"""Finding the collisions among a set of extensions and settling each by priority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.services.identity.impact.claims import Claim, EditOp
from app.services.identity.impact.extension_impact import ExtensionImpact
from app.services.identity.impact.priority_order import lower_than, Priority
from app.services.identity.impact.resolution import Collision, Dropped, ResolvedImpact


class ImpactResolver:
    """Finds the collisions among a set of extensions and settles each by priority."""

    def __init__(
        self,
        impacts: Iterable[ExtensionImpact],
        priority: Iterable[str] = (),
        *,
        reserved: Iterable[str] = (),
    ) -> None:
        """`priority` is the super admin's order, highest first; `reserved` the
        claims igni keeps for itself: an extension claiming one loses it, always."""
        self._impacts = {impact.extension_id: impact for impact in impacts}
        self._priority = Priority(priority)
        self._reserved = frozenset(reserved)

    def resolve(self) -> ResolvedImpact:
        return _Resolution(self._impacts, self._priority).settle(self._reserved)


class _Resolution:
    """One resolve: what each extension still has, and the outcome so far."""

    def __init__(self, impacts: Mapping[str, ExtensionImpact], priority: Priority) -> None:
        self._impacts = impacts
        self._priority = priority
        self._active = {ext: set(impact.items()) for ext, impact in impacts.items()}
        self._outcome = ResolvedImpact()

    def settle(self, reserved: frozenset[str]) -> ResolvedImpact:
        for ext in sorted(self._active):
            for key in sorted(self._active[ext] & reserved):
                self._switch_off(
                    ext, lost={key}, reason=f"{key} is igni's own; igni answers it before any extension could"
                )
        for key in self._keys():
            document = Claim.whole_prompt(key)
            if document is not None:
                self._settle_prompt(key, document)
                continue
            holders = self._holders(key)
            if len(holders) < 2:
                continue
            winner = holders[0]
            self._outcome.collisions.append(
                Collision(claim=key, extensions=sorted(holders), winner=winner, losers=sorted(holders[1:]))
            )
            for loser in holders[1:]:
                self._switch_off(loser, lost={key}, reason=f'{lower_than(winner)}, which also claims {key}')
        self._drop()
        return self._outcome

    def _settle_prompt(self, key: str, document: str) -> None:
        """Overrides of one prompt, and an exclusive one against the edits.

        An exclusive override holds only above every extension editing its
        prompt; outranked, it is off and the edits stand. Of the overrides
        left the highest has the prompt, and if it is exclusive everybody
        else's edits of it are off.
        """
        overriders = self._holders(key)
        if not overriders:
            return
        editors = self._priority.ordered(
            ext for ext in self._active if ext not in overriders and self._edits_of(ext, document)
        )
        exclusive = [ext for ext in overriders if self._exclusive(ext, document)]
        participants = set(overriders) | (set(editors) if exclusive else set())
        if len(participants) < 2:
            return
        standing: list[str] = []
        losers: set[str] = set()
        for ext in overriders:
            higher = [e for e in editors if self._priority.above(e, ext)]
            if self._exclusive(ext, document) and higher:
                self._switch_off(
                    ext,
                    lost={key},
                    reason=f'{lower_than(higher[0])}, which edits {document}; an exclusive override '
                    'holds only above every extension editing its prompt',
                )
                losers.add(ext)
                continue
            standing.append(ext)
        winner = standing[0] if standing else None
        for ext in standing[1:]:
            losers.add(ext)
            self._switch_off(ext, lost={key}, reason=f'{lower_than(winner)}, which also overrides {document}')
        if winner is not None and self._exclusive(winner, document):
            for ext in editors:
                losers.add(ext)
                self._switch_off(
                    ext,
                    lost=self._edits_of(ext, document),
                    reason=f'{lower_than(winner)}, whose override of {document} is exclusive',
                )
        self._outcome.collisions.append(
            Collision(claim=key, extensions=sorted(participants), winner=winner, losers=sorted(losers))
        )

    def _holders(self, key: str) -> list[str]:
        """Highest first: the extensions that still have it."""
        return self._priority.ordered(ext for ext, items in self._active.items() if key in items)

    def _keys(self) -> list[str]:
        """Every claim still held, the whole prompts first."""
        everything: set[str] = set().union(*self._active.values())
        claims = {item for item in everything if not Claim.is_addition(item)}
        wholes = sorted(c for c in claims if Claim.whole_prompt(c) is not None)
        rest = sorted(c for c in claims if Claim.whole_prompt(c) is None)
        return wholes + rest

    def _exclusive(self, ext: str, document: str) -> bool:
        override = self._impacts[ext].override(document)
        return override is not None and override.exclusive

    def _edits_of(self, ext: str, document: str) -> set[str]:
        return {i for i in self._active[ext] if Claim.is_edit(i) and Claim.prompt_document(i) == document}

    def _switch_off(self, extension_id: str, *, lost: set[str], reason: str) -> None:
        for item in sorted(lost & self._active[extension_id]):
            self._active[extension_id].discard(item)
            self._outcome.switch_off(extension_id, claim=item, reason=reason)

    def _drop(self) -> None:
        """Inserts and replacements aimed at sections an override in force does not have."""
        for owner in sorted(self._active):
            for override in self._impacts[owner].prompt_overrides:
                if override.key not in self._active[owner]:
                    continue
                while_ = f' while {owner} holds {override.when}' if override.when else ''
                for ext in sorted(self._active):
                    if ext == owner:
                        continue
                    for edit in self._impacts[ext].edits_of(override.target):
                        if edit.op is EditOp.APPEND or edit.key not in self._active[ext]:
                            continue
                        if edit.section is not None and not override.has(edit.section):
                            self._outcome.drop(
                                ext,
                                Dropped(
                                    claim=edit.key,
                                    by=owner,
                                    reason=f'{owner} overrides {override.target}{while_}, '
                                    f'and its prompt has no section {edit.section!r}',
                                ),
                            )


__all__ = ['ImpactResolver']
