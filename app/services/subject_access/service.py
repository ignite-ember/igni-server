"""Assembling one person's data into an Article 15 report."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.sql.subject_access import ACTIVITY_LIMIT, SubjectAccessDB
from app.services.identity.service_subjects import ServiceSubjects
from app.services.subject_access.classification import ColumnRegistry
from app.services.subject_access.processing import ProcessingDescription
from app.services.subject_access.report_builder import SubjectAccessReportBuilder
from app.services.subject_access.schemas import InvalidSubjectAddress, SubjectAccessReport


class SubjectAccessService:
    """Orchestrates one export: reads from `SubjectAccessDB` and the
    registered services, and hands the rows to a report builder.

    No SQL here (`SubjectAccessDB` reads), no wording (the builder writes
    it) and no policy (`ColumnRegistry` holds it).
    """

    def __init__(
        self,
        *,
        db: SubjectAccessDB | None = None,
        services: ServiceSubjects | None = None,
        processing: ProcessingDescription | None = None,
        columns: ColumnRegistry | None = None,
    ) -> None:
        self._db = db or SubjectAccessDB()
        self._services = services or ServiceSubjects()
        self._processing = processing or ProcessingDescription()
        self._columns = columns or ColumnRegistry()

    async def export(self, *, email: str, org_id: str | None = None) -> SubjectAccessReport:
        """Everything this deployment holds about the address, plus context.

        Keyed on an email address rather than a user id because the subject
        may have no account at all and still appear in sign-in attempts.
        """
        if not email or '@' not in email:
            raise InvalidSubjectAddress('a subject access request needs an email address')

        report = SubjectAccessReportBuilder(
            subject=email,
            generated_at=datetime.now(timezone.utc),
            processing=self._processing.describe(),
            withheld=self._columns.withheld_reasons(),
        )
        sections = report.sections
        db = self._db

        account = await db.account(email)
        sections.account = account

        if not account:
            report.no_account()
        else:
            user_id = str(account[0]['user_id'])
            sections.deployment_role = await db.deployment_role(user_id)
            sections.organisation_memberships = await db.organisation_memberships(user_id)
            sections.organisations_owned = await db.organisations_owned(user_id)

            # The organisation's own records: in its own database, and
            # read only for the organisation whose administrator is asking.
            if org_id is not None:
                sections.group_memberships = await db.group_memberships(org_id, user_id=user_id)
                sections.model_usage = report.capped(
                    'model_usage', await db.model_usage(org_id, user_id=user_id), cap=ACTIVITY_LIMIT
                )
                sections.own_activity = report.capped(
                    'own_activity', await db.own_activity(org_id, user_id=user_id), cap=ACTIVITY_LIMIT
                )
                sections.things_they_created = await db.things_they_created(org_id=org_id, user_id=user_id, email=email)
                # The registered services' own records about this person
                # in this organisation, each under the service's name.
                report.services_answered(await self._services.export(user_id, org_id=org_id))

        # Keyed on the address, which catches the authentication
        # attempts made before any account existed.
        sections.authentication_attempts = report.capped(
            'authentication_attempts', await db.authentication_attempts(org_id, email=email), cap=ACTIVITY_LIMIT
        )

        report.scoped_to(org_id)
        return report.build()
