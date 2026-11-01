"""Pydantic schemas for an organisation's apps (`docs/service-identity.md` §11)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.base_v1.schema.retention import ErasureRecord


class RegisterAppRequest(BaseModel):
    """An org admin registering an app.

    The app generated its own key pair; `jwks` is the public half, as a
    JWK set — the same as a service sends to `POST /v1/clients`. Nothing
    secret is handed to the app, and nothing secret is typed in here.
    """

    name: str = Field(min_length=1, max_length=80)
    #: A member of the organisation answerable for the app.
    owner_user_id: str = Field(min_length=1)
    jwks: dict[str, list[dict[str, object]]]


class UpdateAppRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    owner_user_id: str | None = Field(default=None, min_length=1)


class ReplaceAppKeysRequest(BaseModel):
    """A key rotation: the app's new public keys, replacing the old ones."""

    jwks: dict[str, list[dict[str, object]]]


class OrgAppResponse(BaseModel):
    """An app, as its organisation's admins see it."""

    app_id: str
    #: Its client id at the token endpoint — the same as `app_id`.
    client_id: str
    name: str
    owner_user_id: str | None
    owner_email: str | None = None
    #: Of the first key: what the app printed when it generated it.
    thumbprint: str
    #: Its VM profile in the cloud; null is the organisation's default.
    vm_profile_id: str | None = None
    created_at: datetime


class OrgAppList(BaseModel):
    apps: list[OrgAppResponse]


class DiscardAppRequest(BaseModel):
    #: Register a new app at once with the old one's name, key, owner,
    #: VM profile, extensions and cloud credential: a fresh VM, same app.
    start_fresh: bool = False


class DiscardedApp(BaseModel):
    """An app discarded: gone at once, its VM erased in the background (`docs/service-identity.md` §18.5)."""

    #: Where its erasure stands; null for an app whose VM never existed.
    discarded: ErasureRecord | None
    #: The new app, when `start_fresh` asked for one.
    fresh_app: OrgAppResponse | None = None
