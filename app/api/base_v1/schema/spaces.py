"""Pydantic schemas for an app's spaces and the limits on them (`docs/service-identity.md` §17)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.security.producer import ProducerRef

#: An app's own name for its user: anything but a slash, which the path could not carry.
EXTERNAL_ID_PATTERN = r'^[^/]{1,200}$'


class SpaceRecord(BaseModel):
    space_id: str
    app_id: str
    org_id: str
    #: The app's own name for its user.
    external_id: str
    #: The space as a producer: `{"kind": "space", "id": space_id}`.
    producer: ProducerRef
    created_at: datetime


class SpacePage(BaseModel):
    spaces: list[SpaceRecord]
    #: Pass back as `after` for the next page; null on the last.
    next: str | None


class SpaceCapsBody(BaseModel):
    """The super admin's caps on spaces; null is none."""

    max_spaces_per_app: int | None = Field(default=None, ge=0)
    max_spaces_per_org: int | None = Field(default=None, ge=0)


class SpaceCapsResponse(SpaceCapsBody):
    updated_at: datetime | None = None


class SpaceQuotaBody(BaseModel):
    """An organisation's own limits on spaces, each at most the platform's cap; null leaves the cap."""

    max_spaces_per_app: int | None = Field(default=None, ge=0)
    max_spaces: int | None = Field(default=None, ge=0)


class SpaceQuotaResponse(SpaceQuotaBody):
    org_id: str
    caps: SpaceCapsResponse
    #: How many spaces the organisation has now.
    spaces: int
    updated_at: datetime | None = None


class SpaceLimit(BaseModel):
    """The smaller of the platform's cap and the organisation's own, per app and per organisation."""

    per_app: int | None
    per_org: int | None
