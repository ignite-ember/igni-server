"""Erasing one person from every service that holds their work (GDPR Art. 17).

The workflow behind `POST /v1/portal/erasure`: find the subject, refuse
while a legal hold covers them, tell every approved service, then remove
them from the gateway's feed. Each refusal is a typed error; the router
decides what HTTP makes of it and what the audit trail records.

Raises rather than swallowing, unlike most best-effort calls in this
codebase. Erasure is a legal obligation, and an operator told it
succeeded must not have been told that because a request timed out. A
deployment with no approved services is a no-op, which is correct: there
is nothing anywhere else to redact.
"""

from __future__ import annotations

from app.db.sql.user import UserDB
from app.services.cloud.legal_holds import LegalHolds
from app.services.cloud.producer_feed import ProducerFeed
from app.services.identity.service_subjects import ServiceSubjects


class PersonalErasure:
    def __init__(
        self,
        *,
        users: UserDB | None = None,
        legal_holds: LegalHolds | None = None,
        subjects: ServiceSubjects | None = None,
        feed: ProducerFeed | None = None,
    ) -> None:
        self._users = users or UserDB()
        self._legal_holds = legal_holds or LegalHolds()
        self._subjects = subjects or ServiceSubjects()
        self._feed = feed or ProducerFeed()

    async def erase(self, email: str, *, org_id: str) -> list[str]:
        """The services that confirmed, by name; empty for an address with no account.

        `ErasureRefused` under a legal hold; `ErasureIncomplete` naming the
        services that did not confirm — safe to run again, because every
        service must make erasure idempotent.
        """
        subject = await self._users.get_by_email(email)
        if subject is None:
            return []
        subject_id = str(subject.user_id)
        # Overrides the organisation's retention — but not a legal hold.
        await self._legal_holds.refuse_erasure_if_held(subject_id)
        services_told = await self._subjects.erase(subject_id, org_id=org_id)
        # The gateway's feed names them: removed now, so the gateway erases
        # their VMs, and the rows go once it has been told.
        await self._feed.erase_person(subject_id)
        return services_told
