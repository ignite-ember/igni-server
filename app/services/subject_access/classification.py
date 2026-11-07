"""The column classification, asked questions of.

`PERSONAL_DATA_COLUMNS` and `NOT_PERSONAL_COLUMNS` are the policy; this
is the one place that reads them, so the export, the tests and anything
else that needs "which columns are withheld, and why" ask the same
object instead of each walking the maps.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.services.subject_access.not_personal_columns import NOT_PERSONAL_COLUMNS
from app.services.subject_access.personal_columns import PERSONAL_DATA_COLUMNS
from app.services.subject_access.schemas import ColumnClassification, ColumnVerdict


class ColumnRegistry:
    """Every classified column, personal or not."""

    def __init__(
        self,
        *,
        personal: Mapping[str, ColumnClassification] = PERSONAL_DATA_COLUMNS,
        not_personal: frozenset[str] = NOT_PERSONAL_COLUMNS,
    ) -> None:
        self._personal = personal
        self._not_personal = not_personal

    def verdict_of(self, column: str) -> ColumnVerdict | None:
        """The verdict on a personal column; `None` for one that is not personal or not classified."""
        classification = self._personal.get(column)
        return classification.verdict if classification else None

    def columns_with(self, verdict: ColumnVerdict) -> frozenset[str]:
        return frozenset(column for column, c in self._personal.items() if c.verdict is verdict)

    def withheld_reasons(self) -> dict[str, str]:
        """Each withheld column, to the sentence the subject is given for it.

        Keyed by column at runtime — the set grows with the schema — so a
        map rather than a model.
        """
        return {
            column: c.reason
            for column, c in self._personal.items()
            if c.verdict is ColumnVerdict.WITHHELD and c.reason is not None
        }

    def known_columns(self) -> frozenset[str]:
        """Every column judged in writing, personal or not."""
        return frozenset(self._personal) | self._not_personal
