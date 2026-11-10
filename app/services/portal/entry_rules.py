"""The rules an entry request has to meet, checked without touching storage."""

from __future__ import annotations

from typing import Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from app.api.base_v1.schema.portal.group_entries import CreateEntryRequest
from app.db.sql.schemas.group import EntryCreate
from app.services.portal.entry_kinds import (
    ALLOWED_CONTENT_TYPE,
    KINDS_WITH_MODEL,
    PLUGIN_SOURCE_SCHEMES,
    VALID_CONTENT_TYPES,
    VALID_KINDS,
)
from app.services.portal.group_errors import (
    KindNeedsContentType,
    KindTakesNoModel,
    UnknownContentType,
    UnknownEntryKind,
    UnsupportedPluginSource,
)


class PluginSource(BaseModel):
    """Where a plugin is installed from, with one spelling of "absent" for each part."""

    model_config = ConfigDict(frozen=True)

    url: str | None = None
    ref: str | None = None
    subdir: str | None = None

    @classmethod
    def from_request(cls, *, url: str | None, ref: str | None, subdir: str | None) -> Self:
        """The source a request names, or a refusal if igni could not install from it.

        Checked here as well as in the portal, so an admin past the
        frontend (curl, say) gets a clean 422 rather than a failure at
        install time. Empty strings become None, so the row stores a
        single "absent" whatever shape the frontend sent.
        """
        if url is None:
            return cls(ref=ref, subdir=subdir)
        try:
            scheme = urlparse(url).scheme
        except ValueError as exc:
            # urlparse refuses some malformed netlocs outright: 'http://['.
            raise UnsupportedPluginSource() from exc
        if scheme not in PLUGIN_SOURCE_SCHEMES:
            raise UnsupportedPluginSource()
        return cls(
            url=url.strip() or None,
            ref=(ref or '').strip() or None,
            subdir=(subdir or '').strip() or None,
        )


class EntryRules:
    """What a group can hold, and in which format.

    Typed refusals rather than Pydantic validators: the portal reads a
    refusal's ``detail`` as one string, and these run after the group has
    been found writable, so a missing or standard group still answers
    404 or 409 before any of them.
    """

    def refuse_bad_kind(self, body: CreateEntryRequest) -> None:
        """Refuse a kind a group cannot hold, or a model on a kind that takes none."""
        if body.kind not in VALID_KINDS:
            raise UnknownEntryKind(VALID_KINDS)
        if body.model:
            self.refuse_model_on(body.kind)

    def refuse_model_on(self, kind: str) -> None:
        """Refuse a model on a kind that does not run against one."""
        if kind not in KINDS_WITH_MODEL:
            raise KindTakesNoModel(kind)

    def entry_from(self, body: CreateEntryRequest) -> EntryCreate:
        """The entry to store, once its content type and plugin source pass."""
        if body.content_type not in VALID_CONTENT_TYPES:
            raise UnknownContentType(VALID_CONTENT_TYPES)
        expected = ALLOWED_CONTENT_TYPE.get(body.kind)
        if expected and body.content_type != expected:
            raise KindNeedsContentType(body.kind, expected)

        source = PluginSource.from_request(url=body.source_url, ref=body.source_ref, subdir=body.source_subdir)
        return EntryCreate(
            kind=body.kind,
            entry_name=body.entry_name,
            content=body.content,
            content_type=body.content_type,
            enabled=body.enabled,
            source_url=source.url,
            source_ref=source.ref,
            source_subdir=source.subdir,
            model=body.model or None,
        )
