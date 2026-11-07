"""The shapes a subject access export is made of, and its errors.

The column classification (`ColumnVerdict`, `ColumnClassification`),
the Article 15 context (`ProcessingContext`) and the report itself
(`SubjectAccessReport`), kept apart from the classes that build them,
as `app/services/ai/schemas.py` keeps the inference types.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.sql.schemas.subject_access import Rows, ThingsTheyCreated
from app.services.identity.service_subjects import ServiceSections


class InvalidSubjectAddress(ValueError):
    """A subject access request was made for something that is not an email address."""


class ColumnVerdict(StrEnum):
    """What the export does with one personal-data column."""

    #: The export returns it, or matches on it to find the row.
    INCLUDED = 'included'
    #: Personal, and deliberately not disclosed. The report says the
    #: column exists and why its value is missing.
    WITHHELD = 'withheld'
    #: In a table no code references. Recorded so the decision is
    #: visible, and so an export cannot silently ignore rows that do
    #: still exist.
    DEAD = 'dead'


class ColumnClassification(BaseModel):
    """One column's verdict, and — only when withheld — the reason the subject is given.

    The reason lives on the entry rather than in a second list so that
    "every withheld column is explained in the report" holds by
    construction: a withheld column with no reason cannot be built.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')

    verdict: ColumnVerdict
    reason: str | None = None

    @model_validator(mode='after')
    def _reason_if_and_only_if_withheld(self) -> Self:
        if self.verdict is ColumnVerdict.WITHHELD and not self.reason:
            raise ValueError('a withheld column needs the reason the subject is given')
        if self.verdict is not ColumnVerdict.WITHHELD and self.reason is not None:
            raise ValueError(f'only a withheld column carries a reason, not one that is {self.verdict}')
        return self

    @classmethod
    def withheld(cls, reason: str) -> ColumnClassification:
        return cls(verdict=ColumnVerdict.WITHHELD, reason=reason)


INCLUDED = ColumnClassification(verdict=ColumnVerdict.INCLUDED)
DEAD = ColumnClassification(verdict=ColumnVerdict.DEAD)


class Retention(BaseModel):
    """How long the deployment keeps what it holds."""

    model_config = ConfigDict(extra='forbid', serialize_by_alias=True)

    audit_events: str
    #: Spelled with a space on the wire, as it always has been.
    everything_else: str = Field(serialization_alias='everything else')


class ProcessingContext(BaseModel):
    """The half of Article 15 that is not rows.

    Purposes, categories, recipients, retention and source, built by
    `ProcessingDescription` from what the deployment is configured to do.
    """

    model_config = ConfigDict(extra='forbid')

    controller: str
    purposes: list[str]
    categories: list[str]
    recipients: list[str]
    retention: Retention
    source: str
    rights: str


class Section(StrEnum):
    """The sections that are one read's rows, written through `SubjectAccessReportBuilder.section`.

    Each value is a `SubjectAccessSections` field and, when the read is
    capped, the key the report's `truncated` carries for it — so these
    are wire names. `account`, `note`, `things_they_created` and
    `services` are not here: each has its own builder method.
    """

    DEPLOYMENT_ROLE = 'deployment_role'
    ORGANISATION_MEMBERSHIPS = 'organisation_memberships'
    ORGANISATIONS_OWNED = 'organisations_owned'
    GROUP_MEMBERSHIPS = 'group_memberships'
    MODEL_USAGE = 'model_usage'
    OWN_ACTIVITY = 'own_activity'
    AUTHENTICATION_ATTEMPTS = 'authentication_attempts'


def _absent(value: object) -> bool:
    return value is None


class SubjectAccessSections(BaseModel):
    """One field per section an export can produce, in the order it produces them.

    The section *names* are a fixed set, so they are a model; the rows
    inside each are `Rows` (see its note). A section the export did not
    produce is absent from the wire rather than null — the subject is
    told what was looked at, and `null` would read as "looked, found
    nothing", which is what an empty list says.

    `extra='forbid'` with `validate_assignment` so a misspelt section
    fails loudly instead of being dropped on the way out.
    """

    model_config = ConfigDict(extra='forbid', validate_assignment=True)

    account: Rows | None = Field(default=None, exclude_if=_absent)
    #: Present only when no account exists for the address.
    note: str | None = Field(default=None, exclude_if=_absent)
    deployment_role: Rows | None = Field(default=None, exclude_if=_absent)
    organisation_memberships: Rows | None = Field(default=None, exclude_if=_absent)
    organisations_owned: Rows | None = Field(default=None, exclude_if=_absent)
    group_memberships: Rows | None = Field(default=None, exclude_if=_absent)
    model_usage: Rows | None = Field(default=None, exclude_if=_absent)
    own_activity: Rows | None = Field(default=None, exclude_if=_absent)
    #: Grouped by kind of thing, as `SubjectAccessDB.things_they_created` returns it.
    things_they_created: ThingsTheyCreated | None = Field(default=None, exclude_if=_absent)
    #: Each registered service's own sections, under the service's name.
    services: ServiceSections | None = Field(default=None, exclude_if=_absent)
    authentication_attempts: Rows | None = Field(default=None, exclude_if=_absent)

    def present_count(self) -> int:
        """How many sections the export produced — what the audit trail records."""
        return sum(1 for name in type(self).model_fields if getattr(self, name) is not None)

    def has_account(self) -> bool:
        return bool(self.account)


class SubjectAccessReport(BaseModel):
    """What an Article 15 request is answered with.

    The one HTTP contract in this product a regulator might read, so
    every part of it is declared: the context is a `ProcessingContext`
    and the sections a `SubjectAccessSections`. Only the rows inside a
    section stay untyped — the envelope is a contract; the rows are an
    inventory.
    """

    model_config = ConfigDict(extra='forbid')

    subject: str
    generated_at: str
    #: Article 15 context — purposes, categories, recipients, retention, source.
    processing: ProcessingContext
    #: Column to the reason it is not in `sections`. Named as withheld
    #: rather than silently absent, which is the whole difference
    #: between a redaction and an incomplete answer.
    withheld: dict[str, str]
    sections: SubjectAccessSections

    #: Section (or service) name to the sentence saying it is incomplete.
    #:
    #: Absent when nothing was capped, which is why it is optional
    #: rather than an empty dict — and why it is here at all: a cap
    #: this did not carry would be an export that silently
    #: under-reported, which is the failure the whole mechanism
    #: exists to prevent.
    truncated: dict[str, str] | None = None

    #: Whose records these are: one organisation's, or none.
    scope: str | None = None
