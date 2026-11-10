"""A VM profile request as its requester and an org admin read it, and its version.

`docs/service-identity.md` §12.3.
"""

from __future__ import annotations

from app.api.base_v1.schema.vm_commands import Requester
from app.api.base_v1.schema.vm_profile_requests import (
    RequestedBy,
    RequestSubject,
    VmProfileRequestRecord,
    VmProfileRequestSettings,
)
from app.api.base_v1.schema.vm_profiles import PlatformVmCapsBody
from app.db.sql.models.vm_profile_request import VmProfileRequest
from app.services.cloud.vm_caps import VmCaps
from app.services.cloud.vm_profile_versions import ProfileVersion

#: A subject kind: an extension, asking through its manifest (an app's is `registered_client.APP`).
EXTENSION = 'extension'
#: A requester kind: the service whose extension's manifest asks.
SERVICE = 'service'
#: What a deciding admin acted as.
ORG_ADMIN = 'org_admin'
SUPER_ADMIN = 'super_admin'


class VmProfileRequestRecords:
    """Stored requests turned into what is read, and what a request's version is."""

    @staticmethod
    def version_of(settings: VmProfileRequestSettings) -> str:
        """A digest of everything asked for: equal exactly when the requests are."""
        return ProfileVersion.digest({'requested': settings.model_dump(mode='json')})

    @staticmethod
    def record(org_id: str, *, row: VmProfileRequest, name: str, caps: PlatformVmCapsBody) -> VmProfileRequestRecord:
        """The row, its subject called `name`, and whether it fits within `caps`."""
        requested = VmProfileRequestSettings.model_validate(row.settings)
        return VmProfileRequestRecord(
            id=row.id,
            org_id=org_id,
            subject=RequestSubject.stored(row.subject_kind, subject_id=row.subject_id, name=name),
            state=VmProfileRequestRecord.stored_state(row.state),
            requested=requested,
            version=row.version,
            packages_version=ProfileVersion.packages(requested),
            within_caps=VmCaps.refusal(requested, caps) is None,
            requested_by=RequestedBy.stored(row.requested_by_kind, row.requested_by_id),
            requested_at=row.requested_at,
            decided_by=(
                Requester(kind='person', id=row.decided_by_user_id, acting_as=row.decided_as or ORG_ADMIN)
                if row.decided_by_user_id
                else None
            ),
            decided_at=row.decided_at,
            reason=row.reason,
            profile_id=row.profile_id,
        )
