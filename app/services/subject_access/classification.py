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

#: The order the report's `withheld` keys have always gone out in: the
#: order of the dict the export used to write by hand. The JSON must not
#: change shape because the reasons moved onto their columns, whose
#: order follows the tables instead. A withheld column not named here
#: follows these, in the order the classification lists it.
_WITHHELD_WIRE_ORDER = (
    'users.hashed_password',
    'user_roles.email_verification_token',
    'registered_clients.decided_by_user_id',
    'extension_priority.set_by_user_id',
    'service_attachments.content',
    'vm_credentials.secret_hash',
    'vm_credentials.previous_hashes',
    'api_keys.secret_hash',
    'producer_feed_marks.producer_id',
    'platform_vm_caps.updated_by_user_id',
    'org_vm_quotas.updated_by_user_id',
    'producer_exports.content_oid',
    'platform_retention.updated_by_user_id',
    'org_retention.updated_by_user_id',
    'platform_space_caps.updated_by_user_id',
    'org_space_quotas.updated_by_user_id',
    'app_spaces.external_id',
    'audit_events.detail',
)


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

    def personal(self) -> Mapping[str, ColumnClassification]:
        """Every personal column, to its classification. Read-only."""
        return self._personal

    def not_personal(self) -> frozenset[str]:
        """Every column judged not personal."""
        return self._not_personal

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
        reasons = {
            column: c.reason
            for column, c in self._personal.items()
            # `reason is not None` is guaranteed for a withheld column by
            # `ColumnClassification`; kept because it narrows the type.
            if c.verdict is ColumnVerdict.WITHHELD and c.reason is not None
        }
        first = [column for column in _WITHHELD_WIRE_ORDER if column in reasons]
        rest = [column for column in reasons if column not in _WITHHELD_WIRE_ORDER]
        return {column: reasons[column] for column in first + rest}

    def known_columns(self) -> frozenset[str]:
        """Every column judged in writing, personal or not."""
        return frozenset(self._personal) | self._not_personal
