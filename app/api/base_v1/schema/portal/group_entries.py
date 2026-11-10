"""Pydantic schemas for a group's entries: what the portal writes, and what it and the CLI read."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.sql.models.org_group_entry import OrgGroupEntry
from app.db.sql.schemas.group import EntryPatch

#: Ceiling on an entry's content, in bytes.
#:
#: ``content`` was the one field on these requests with no bound, while
#: ``entry_name``, ``source_url`` and the rest all had one — and it is the
#: field that matters most, because an entry does not just sit in
#: Postgres: it is served in the group pack to every client and written
#: to disk on every developer's machine. One request, N machines.
#:
#: 32 MB was accepted before this. The largest entry the default seed
#: ships is an agent prompt at just under 20 KB, and all 29 of them
#: together come to 262 KB — so this is roughly thirteen times the
#: biggest real entry, and generous for anything anybody writes by hand.
MAX_ENTRY_CONTENT_BYTES = 256 * 1024


#: An entry name has to be a plain filename.
#:
#: The client turns it into a path — ``group-policy/<kind>/<name>.md`` —
#: and ``Path`` accepts a separator without complaint, so a name like
#: ``../../../../../../tmp/x`` wrote outside the cache directory on every
#: machine that synced the pack. For ``scripts`` the file is written mode
#: 0700, which makes it an executable dropped wherever the user can write.
#:
#: The client refuses these itself as well — it must, since it can be
#: pointed at a server somebody else runs — but a name that cannot be
#: materialised has no business being stored either, and refusing at the
#: portal is where an admin finds out while they are still looking at the
#: form.
#:
#: Letters, digits, dot, underscore and hyphen. A leading dot is allowed
#: (``.mcp.json``-style names exist) but a name that is only dots is not —
#: expressed as "must contain at least one non-dot character" rather than
#: with a negative lookahead, because pydantic's regex engine has none.
ENTRY_NAME_PATTERN = r'^[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*$'


class OrgGroupEntryResponse(BaseModel):
    """A single entry for a group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    kind: str  # agents | mcps | plugins | settings
    entry_name: str
    content: str
    content_type: str  # markdown | yaml | json
    enabled: bool = True
    # Plugin install source — mirrors the trio the GroupPolicy
    # cache consumes. For ``kind == "plugins"`` entries,
    # ``source_url`` set means "git-clone this on the runner"
    # instead of writing the YAML content. All three are
    # optional; non-plugin rows usually leave them null.
    source_url: str | None = None
    source_ref: str | None = None
    source_subdir: str | None = None
    # Agents only: which model this one runs against. Null falls back to
    # the group's default, then the deployment's.
    model: str | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, entry: OrgGroupEntry) -> OrgGroupEntryResponse:
        """A stored entry as the portal sees it, source trio included."""
        return cls(
            id=entry.id,
            group_id=entry.group_id,
            kind=entry.kind,
            entry_name=entry.entry_name,
            content=entry.content,
            content_type=entry.content_type,
            enabled=entry.enabled,
            model=entry.model,
            source_url=entry.source_url,
            source_ref=entry.source_ref,
            source_subdir=entry.source_subdir,
            created_by_user_id=entry.created_by_user_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )


class AgentToolsResponse(BaseModel):
    """What an agent may name in its ``tools:`` frontmatter.

    The portal warns against this list as somebody types. Fetched
    rather than bundled: a copy in the frontend would be a third place
    to keep in step, and the first to fall behind.
    """

    tools: list[str]


class OrgGroupEntryList(BaseModel):
    """List of entries for a group.

    ``warnings`` carries anything worth saying about what was just
    written — a mistyped tool name, say. Advisory: the write succeeded,
    and these are things the admin would want to know before somebody
    hits them at runtime.
    """

    entries: list[OrgGroupEntryResponse]
    total: int
    warnings: list[str] = []


class CreateEntryRequest(BaseModel):
    """Request to create a new entry."""

    kind: str = Field(..., description='Entry kind: agents | mcps | plugins | settings')
    entry_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        pattern=ENTRY_NAME_PATTERN,
        description='Unique name within the group. A plain filename: letters, digits, dot, underscore, hyphen.',
    )
    content: str = Field(
        ...,
        max_length=MAX_ENTRY_CONTENT_BYTES,
        description='Raw file content',
    )
    content_type: str = Field(..., description='Content type hint: markdown | yaml | json')
    enabled: bool = Field(True, description='Whether the entry is active')
    # Plugin install source — same trio as the wire response. See
    # OrgGroupEntryResponse.source_url for the contract.
    source_url: str | None = Field(None, max_length=2048)
    source_ref: str | None = Field(None, max_length=512)
    source_subdir: str | None = Field(None, max_length=1024)
    # Agents only: which model this one runs against. Absent falls back
    # to the group's default, then the deployment's.
    model: str | None = Field(None, description='Model or LoRA adapter this agent uses')

    @model_validator(mode='after')
    def _source_fields_are_complete(self) -> CreateEntryRequest:
        """Reject half-filled plugin source specs (ref/subdir without URL).

        Mirrors the Pydantic rule in
        ``igni_core.core.config.group_policy.entry.GroupPolicyEntry``
        so the FE catches the same mistake before hitting the wire.
        Empty strings are treated as absent — wire formats commonly
        serialize "no value" as ``""``, so ``is None`` alone would
        let ``""`` + ``"main"`` slip past.
        """
        url_present = bool(self.source_url and self.source_url.strip())
        ref_present = bool(self.source_ref and self.source_ref.strip())
        subdir_present = bool(self.source_subdir and self.source_subdir.strip())

        if (ref_present or subdir_present) and not url_present:
            raise ValueError('source_ref / source_subdir require source_url to be set')
        return self


class UpdateEntryRequest(BaseModel):
    """Request to update an entry.

    ``model`` follows the usual partial-update rule — absent leaves it
    alone — with an empty string as the explicit clear, since ``None``
    already means "not supplied".
    """

    content: str | None = Field(None, max_length=MAX_ENTRY_CONTENT_BYTES)
    enabled: bool | None = None
    model: str | None = None

    def to_patch(self) -> EntryPatch:
        """The edit this request asks for: only the fields it supplies. An empty `model` is the explicit clear."""
        patch = EntryPatch()
        if self.content is not None:
            patch.content = self.content
        if self.enabled is not None:
            patch.enabled = self.enabled
        if self.model is not None:
            patch.model = self.model or None
        return patch


class OrgGroupPackEntry(BaseModel):
    """One entry in the group pack."""

    kind: str
    entry_name: str
    content: str
    content_type: str
    enabled: bool
    # Agents only: which model this one runs against. Null means the
    # group's default, then the deployment's.
    model: str | None = None
    # Plugin install source trio — populated only for ``kind == "plugins"``
    # entries the admin attached a git source to. The CLI's
    # :class:`GroupPolicyCache` uses ``source_url`` to decide between
    # writing YAML vs invoking :class:`PluginInstaller`. Mirrors the trio
    # on :class:`OrgGroupEntryResponse`; nullable on the wire so
    # non-plugin kinds and old rows round-trip unchanged.
    source_url: str | None = None
    source_ref: str | None = None
    source_subdir: str | None = None

    @classmethod
    def of(cls, entry: OrgGroupEntry) -> OrgGroupPackEntry:
        """A stored entry as the CLI receives it."""
        return cls(
            kind=entry.kind,
            entry_name=entry.entry_name,
            content=entry.content,
            content_type=entry.content_type,
            enabled=entry.enabled,
            model=entry.model,
            source_url=entry.source_url,
            source_ref=entry.source_ref,
            source_subdir=entry.source_subdir,
        )
