"""Pydantic schemas for extensions: a service uploading its own, and machines receiving them.

See `app.api.base_v1.endpoints.identity.extensions` and `docs/service-identity.md` §8.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, SerializerFunctionWrapHandler

from app.services.identity.extension_grants import AddedGrant
from app.services.identity.extensions import Publication
from app.services.identity.jwk import JwkSet


class HeldAsset(BaseModel):
    sha256: str
    size: int


class Published(BaseModel):
    extension_id: str
    version: str
    sha256: str
    #: Held for a super admin: it asks for more than the approved version,
    #: which machines keep receiving until it is approved.
    held: bool = False
    #: Why it is held, in a line a service can log.
    reason: str | None = None
    #: What it asks for that the approved version did not.
    added: list[AddedGrant] = Field(default_factory=list)

    @classmethod
    def of(cls, publication: Publication) -> Published:
        stored = publication.extension
        return cls(
            extension_id=stored.extension_id,
            version=stored.version,
            sha256=stored.sha256,
            held=publication.held,
            reason=publication.reason,
            added=publication.added,
        )


class DistributedExtension(BaseModel):
    extension_id: str
    version: str
    name: str
    description: str
    #: The service it belongs to — and the audience of the tokens the
    #: host fetches for it.
    service_url: str
    sha256: str
    #: Verify with `publisher_jwks` before installing.
    signature: str
    publisher_jwks: JwkSet
    published_at: datetime
    #: Every group in the producer's organisation this extension is on
    #: for. A person gets it because theirs is one of them.
    groups: list[str]
    #: Every app in the organisation this extension is on for. An app
    #: gets it because it is one of them.
    apps: list[str]

    @field_serializer('publisher_jwks', mode='wrap')
    def _as_registered(self, keys: JwkSet, handler: SerializerFunctionWrapHandler):
        """The keys exactly as the service registered them, with no member it left out added as null.

        Unannotated on purpose: the published schema stays `JwkSet`'s.
        """
        return keys.model_dump(mode='json', exclude_unset=True)


class ListedProducer(BaseModel):
    """Who the listing is for: a person, or an app (`docs/service-identity.md` §11)."""

    kind: str
    id: str


class Distributed(BaseModel):
    producer: ListedProducer
    #: The person's one group in the organisation they are signed in to,
    #: read now — fresher than the session token's `group` claim. Null
    #: for an app, which is in no group, and for somebody in no
    #: organisation, who gets nothing.
    group: str | None
    #: That group's name, for a client to show; null when `group` is.
    group_name: str | None = None
    extensions: list[DistributedExtension]
    #: The super admin's priority order of extensions, highest first. A
    #: machine gives every collision to the higher one, stacks prompt
    #: edits and asks hooks in it.
    priority: list[str]
