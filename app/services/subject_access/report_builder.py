"""One report, while it is being assembled."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.db.sql.schemas.subject_access import AccountRow, SubjectRow
from app.db.sql.schemas.subject_access_created import ThingsTheyCreated
from app.services.identity.service_subjects import ExportAnswers
from app.services.subject_access.schemas import (
    ProcessingContext,
    Section,
    SubjectAccessReport,
    SubjectAccessSections,
)

_TRUNCATED = (
    '{cap} rows returned, which is the maximum this export produces. '
    'There are more. Narrow the request or query the database directly '
    'for the complete set.'
)

_SERVICE_MISSING = (
    '{name} did not answer; what it holds about this person is missing from this '
    'export. Ask again, or ask an operator to check it.'
)

_NO_ACCOUNT = (
    'No user account exists for this address. Any records below come from attempts to sign in with this address.'
)

_SCOPE_ORGANISATION = (
    'Prepared by an administrator of organisation {org_id}, from its records. '
    'Sections that are not organisation-specific — the account and sign-in '
    'attempts — describe the person across this deployment. Another '
    "organisation's records about the same address are not included: they are "
    "that organisation's to disclose."
)

_SCOPE_DEPLOYMENT = (
    'Prepared without an organisation, so only records that are not '
    "organisation-specific are included. Each organisation's own records — groups, "
    'usage, activity — are not.'
)


class SubjectAccessReportBuilder:
    """The sections, truncations and scope of one export, until `build()`.

    One per export. Every sentence the report says about its own
    completeness is written here, next to the state it describes.
    """

    def __init__(
        self,
        *,
        subject: str,
        generated_at: datetime,
        processing: ProcessingContext,
        withheld: dict[str, str],
    ) -> None:
        self._subject = subject
        self._generated_at = generated_at
        self._processing = processing
        self._withheld = withheld
        self._sections = SubjectAccessSections()
        self._truncated: dict[str, str] = {}
        self._scope: str | None = None

    def account(self, rows: list[AccountRow]) -> str | None:
        """The account section; the account's user id, or `None` when there is none.

        With no account the report says why it goes on: the person may
        still appear in authentication attempts, so the report continues
        rather than returning empty — "we hold nothing" would be a false
        answer to Article 15.
        """
        self._sections.account = rows
        if not rows:
            self._sections.note = _NO_ACCOUNT
            return None
        return rows[0].user_id

    def section(self, name: Section, *, rows: Sequence[SubjectRow], cap: int | None = None) -> None:
        """One read's rows, with the report saying so if they stopped at `cap`.

        `cap` must be the cap the query applied: if the two drift, a
        section either reports a truncation that did not happen or stays
        silent about one that did — and the second hands somebody an
        incomplete answer to a legal request while calling it their data.
        """
        setattr(self._sections, name.value, rows)
        if cap is not None and len(rows) >= cap:
            self._truncated[name.value] = _TRUNCATED.format(cap=cap)

    def things_they_created(self, things: ThingsTheyCreated) -> None:
        self._sections.things_they_created = things

    def services_answered(self, answers: ExportAnswers) -> None:
        """The registered services' own sections, and which of them did not answer."""
        if answers.sections:
            self._sections.services = answers.sections
        for name in answers.missing:
            self._truncated[name] = _SERVICE_MISSING.format(name=name)

    def scoped_to(self, org_id: str | None) -> None:
        self._scope = _SCOPE_DEPLOYMENT if org_id is None else _SCOPE_ORGANISATION.format(org_id=org_id)

    def build(self) -> SubjectAccessReport:
        return SubjectAccessReport(
            subject=self._subject,
            generated_at=self._generated_at.isoformat(),
            processing=self._processing,
            withheld=self._withheld,
            sections=self._sections.model_copy(),
            truncated=self._truncated or None,
            scope=self._scope,
        )
