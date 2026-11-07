"""Pydantic schemas for a super admin's review of registered clients.

See `app.api.base_v1.endpoints.portal.clients`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.db.sql.models.service_extension import ServiceExtension
from app.services.identity.client_review import ClientDossier, ReviewedClient
from app.services.identity.extension_review import ExtensionReview
from app.services.identity.extensions import HeldUpdate, Manifest, PluginSummary


class ExtensionSummary(BaseModel):
    extension_id: str
    version: str
    name: str
    description: str
    published_at: datetime

    @classmethod
    def of(cls, extension: ServiceExtension) -> ExtensionSummary:
        manifest = Manifest.model_validate(extension.manifest)
        return cls(
            extension_id=extension.extension_id,
            version=extension.version,
            name=manifest.name or extension.extension_id,
            description=manifest.description,
            published_at=extension.published_at,
        )


class ExtensionDetail(ExtensionSummary):
    """Everything the extension would put on people's machines.

    `manifest` is the whole manifest as the service sent it: what it
    adds (pages, menus, commands, indicators, settings, its prompt
    contributions), what it may touch (`permissions`) and what it needs
    (`requires`). `plugin` lists its own agents, the agents of igni's it
    stands in for, and its skills. `review` is its impact on igni and
    every collision it is in, with who wins it by priority.
    """

    sha256: str
    manifest: Manifest
    plugin: PluginSummary
    review: ExtensionReview | None = None

    @classmethod
    def of_reviewed(cls, extension: ServiceExtension, review: ExtensionReview | None) -> ExtensionDetail:
        return cls(
            **ExtensionSummary.of(extension).model_dump(),
            sha256=extension.sha256,
            manifest=Manifest.model_validate(extension.manifest),
            plugin=PluginSummary.model_validate(extension.plugin),
            review=review,
        )


class ClientSummary(BaseModel):
    client_id: str
    kind: str
    name: str
    base_url: str
    redirect_uris: list[str]
    #: Compare with the one the client printed before approving.
    thumbprint: str
    state: str
    created_at: datetime
    decided_at: datetime | None = None
    #: What it ships to people's machines, if it has uploaded anything.
    extension: ExtensionSummary | None = None
    #: A newer version that asks for more, waiting for a super admin.
    held_version: str | None = None

    @classmethod
    def of(cls, reviewed: ReviewedClient) -> ClientSummary:
        client = reviewed.client
        return cls(
            client_id=client.client_id,
            kind=client.kind,
            name=client.name,
            base_url=client.base_url,
            redirect_uris=list(client.redirect_uris or []),
            thumbprint=client.thumbprint,
            state=client.state,
            created_at=client.created_at,
            decided_at=client.decided_at,
            extension=ExtensionSummary.of(reviewed.extension) if reviewed.extension is not None else None,
            held_version=reviewed.held.version if reviewed.held is not None else None,
        )


class ClientDetail(ClientSummary):
    extension_detail: ExtensionDetail | None = None
    #: The held version and what it adds, if one is waiting.
    held_update: HeldUpdate | None = None

    @classmethod
    def of_dossier(cls, dossier: ClientDossier) -> ClientDetail:
        extension = dossier.reviewed.extension
        return cls(
            **ClientSummary.of(dossier.reviewed).model_dump(),
            extension_detail=ExtensionDetail.of_reviewed(extension, dossier.review) if extension is not None else None,
            held_update=dossier.held_update,
        )
