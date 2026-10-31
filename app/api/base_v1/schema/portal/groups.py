"""Pydantic schemas for portal group management."""

from datetime import datetime
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ------------------------------------------------------------------
# Shared / base
# ------------------------------------------------------------------


class EntryKind(str):
    AGENTS = 'agents'
    MCPS = 'mcps'
    PLUGINS = 'plugins'
    SETTINGS = 'settings'


# ------------------------------------------------------------------
# Groups
# ------------------------------------------------------------------


#: A CSS hex colour: ``#rgb``, ``#rrggbb`` or ``#rrggbbaa``.
#:
#: Narrow on purpose. These values end up as CSS custom properties in
#: every client the group's people run, and the widest useful grammar is
#: also the one with room to hide in — ``rgb()`` takes nested
#: expressions, and named colours would have to be enumerated to be
#: checked at all. Hex covers what a colour picker emits.
#:
#: The client re-validates against the same grammar rather than trusting
#: what it is handed, for the reason :data:`ENTRY_NAME_PATTERN` gives
#: below: it can be pointed at a server somebody else runs.
HEX_COLOUR_PATTERN = r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$'

#: The image types a brand mark may be.
#:
#: SVG is here, and it is the one worth a sentence: an SVG document can
#: carry script, so the client renders a brand mark as ``<img src=...>``
#: and never inlines it as markup. Script does not run in an ``<img>``.
BRAND_MARK_MIME_PATTERN = r'^data:image/(?:png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$'

#: 64 KB of base64, so ~48 KB of image.
#:
#: The pack this rides on is polled by every running session, and a
#: brand mark is a logo — anything near this cap is a photograph that
#: should have been an SVG or a smaller PNG.
MAX_BRAND_MARK_BYTES = 64 * 1024


class GroupTheme(BaseModel):
    """Brand overrides for a group's people.

    Every field is optional: a theme that sets only ``accent`` leaves
    the rest of igni's palette alone. The client maps these onto the
    CSS custom properties its stylesheet already defines for both light
    and dark, so the user's palette choice keeps working — the group
    brands the product, it does not pick light or dark for anybody.
    """

    model_config = ConfigDict(extra='forbid')

    accent: str | None = Field(None, pattern=HEX_COLOUR_PATTERN, description='Primary brand colour (hex)')
    danger: str | None = Field(None, pattern=HEX_COLOUR_PATTERN, description='Error/danger colour (hex)')
    success: str | None = Field(None, pattern=HEX_COLOUR_PATTERN, description='Success colour (hex)')
    brand_name: str | None = Field(
        None,
        max_length=40,
        description='Replaces the "igni" wordmark in the client header',
    )
    brand_mark: str | None = Field(
        None,
        pattern=BRAND_MARK_MIME_PATTERN,
        description=(
            'Logo as a base64 data: URI (png, jpeg, webp or svg+xml). Not a URL — the '
            "desktop client's CSP allows img-src from data: only, so a remote URL would "
            'never load.'
        ),
    )

    @model_validator(mode='after')
    def _mark_within_size_budget(self) -> 'GroupTheme':
        if self.brand_mark and len(self.brand_mark.encode()) > MAX_BRAND_MARK_BYTES:
            raise ValueError(
                f'brand_mark is {len(self.brand_mark.encode())} bytes, over the {MAX_BRAND_MARK_BYTES} byte limit'
            )
        return self


class OrgGroupResponse(BaseModel):
    """A single org group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    description: str | None = None
    # Which model this group's people get when they do not name one.
    # Usually a LoRA adapter — see ``OrgGroup.default_model``.
    default_model: str | None = None
    # Brand overrides. Null is igni's palette. Parsed from the column's
    # JSON by the service, so the portal edits a typed object rather
    # than a string it has to parse itself.
    theme: GroupTheme | None = None
    # The group seeded with what igni ships. Shown as such when picking
    # a template, since "the standard set" is the one people want.
    is_default: bool = False
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class OrgGroupDetail(OrgGroupResponse):
    """Group with member and entry counts."""

    member_count: int = 0
    entry_count: int = 0


class OrgGroupList(BaseModel):
    """List of org groups."""

    groups: list[OrgGroupDetail]
    total: int
    offset: int = 0
    limit: int | None = None


class CreateGroupRequest(BaseModel):
    """Request to create a new group."""

    name: str = Field(..., min_length=1, max_length=120, description='Group name (unique within org)')
    description: str | None = Field(None, description='Optional description')
    default_model: str | None = Field(
        None, description='Model or LoRA adapter this group routes to when the caller names none'
    )
    template_group_id: str | None = Field(
        None,
        description=(
            'Copy every entry and extension from this group. Any group can be a template, including the '
            'Default one that holds what igni ships. Omit to start empty. Nobody is copied.'
        ),
    )
    theme: GroupTheme | None = Field(None, description="Brand overrides. Omit for igni's palette.")


class CopyGroupRequest(BaseModel):
    """Copy a group: its entries, extensions and settings, under a new name, with nobody in it."""

    name: str = Field(..., min_length=1, max_length=120, description='Name of the copy (unique within org)')
    description: str | None = Field(None, description="The copy's description. Omit to keep the original's.")


class UpdateGroupRequest(BaseModel):
    """Request to update a group.

    ``default_model`` follows the usual partial-update rule — absent
    leaves it alone — with an empty string as the explicit way to clear
    it, since ``None`` already means "not supplied".

    ``theme`` needs the same distinction and cannot borrow the same
    trick, being an object rather than a string: an empty
    :class:`GroupTheme` (every field null) is the explicit "clear it",
    and absent still means "leave it alone". Both end up storing NULL —
    a theme that overrides nothing *is* igni's palette — so the two
    spellings agree on the result.
    """

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    default_model: str | None = None
    theme: GroupTheme | None = None


# ------------------------------------------------------------------
# Members
# ------------------------------------------------------------------


class OrgGroupMemberResponse(BaseModel):
    """A user assigned to a group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    user_id: str
    added_by_user_id: str
    added_at: datetime
    # Resolved from users table
    email: str | None = None
    full_name: str | None = None


class OrgGroupMemberList(BaseModel):
    """List of group members."""

    members: list[OrgGroupMemberResponse]
    total: int


class AddMemberRequest(BaseModel):
    """Put a person in this group.

    A person is in exactly one group, so this moves them out of the one
    they are in — the Default group, for anybody nobody has placed yet.
    """

    user_id: str = Field(..., description='User ID to move into this group')


class OrgGroupMembership(BaseModel):
    """Which one group a person of the organisation is in."""

    user_id: str
    group_id: str
    group_name: str
    is_default: bool


class OrgGroupMembershipList(BaseModel):
    """Every person of the organisation with their group."""

    memberships: list[OrgGroupMembership]


# ------------------------------------------------------------------
# Entrys
# ------------------------------------------------------------------


class OrgGroupEntryResponse(BaseModel):
    """A single entry entry for a group."""

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
    def _source_fields_are_complete(self) -> 'CreateEntryRequest':
        """Reject half-filled plugin source specs (ref/subdir without URL).

        Mirrors the Pydantic rule in
        ``ember_code.core.config.group_policy.GroupPolicyEntryEntry``
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


# ------------------------------------------------------------------
# CLI pack (consumed by igni CLI)
# ------------------------------------------------------------------


class OrgGroupSummary(BaseModel):
    """Minimal group info for the CLI to know which group it's in."""

    id: str
    org_id: str
    name: str


class OrgGroupPackEntry(BaseModel):
    """One entry entry in the group pack."""

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


class OrgGroupPack(BaseModel):
    """Full pack of entries for a group — consumed by igni CLI."""

    group_id: str
    group_name: str
    fetched_at: datetime
    entries: list[OrgGroupPackEntry]
    # What the group's people get when an agent names no model.
    default_model: str | None = None
    # Brand overrides, applied by the client's UI. Group-level like
    # ``default_model`` rather than an entry, and delivered the same
    # way — this pack is already polled with an ETag, so branding costs
    # no extra request and an unchanged theme costs no bytes.
    theme: GroupTheme | None = None
    #: The organisation the group belongs to. Not part of the served
    #: payload (excluded from `as_payload`, so the response and its ETag
    #: are what they were); carried so the signed form can name it.
    org_id: str | None = Field(default=None, exclude=True)

    def as_payload(self) -> dict:
        """The pack as JSON-ready types.

        `mode='json'`, so `fetched_at` leaves as an ISO string. Python
        mode hands `JSONResponse`'s encoder a raw `datetime`, which it
        refuses — and that is a property of this model's fields, so it
        belongs here rather than in a comment beside one caller.
        """
        return self.model_dump(mode='json')

    def etag(self, *, bound_to: str = '') -> str:
        """A validator for "has this pack changed".

        **`fetched_at` is excluded, and that is the whole point.** It
        is `now()`, so a tag over the whole payload differs on every
        request: present, well-formed, and unable to match by
        construction. The tag covers what identifies the pack, not
        when it was read.

        `usedforsecurity=False` is not only documentation. This is a
        cache validator and not a signature, and on a FIPS-enabled
        host a bare `hashlib.md5()` raises — which would make this a
        500 on exactly the kind of regulated deployment a self-hosted
        product is installed into.

        `bound_to` folds in what the signed form depends on besides the
        content: who it was signed for and with which key (see
        `GroupPackSigner.binding`). Without it, somebody else signing in
        on the same machine, or the first poll after a key rotation,
        would get a 304 and keep a signature that no longer verifies.
        """
        identity: dict = {key: value for key, value in self.as_payload().items() if key != 'fetched_at'}
        if bound_to:
            identity['_bound_to'] = bound_to
        digest = hashlib.md5(json.dumps(identity, sort_keys=True).encode(), usedforsecurity=False)
        return f'"{digest.hexdigest()}"'


class SignedOrgGroupPack(OrgGroupPack):
    """What `GET /v1/portal/me/group/pack` serves: the pack, and the pack signed.

    Every field of `OrgGroupPack` is still here, unchanged, for clients
    that read them. `signed_pack` is the same content as a compact JWS
    (`app.services.portal.group_pack_signature`); igni applies only that.
    """

    signed_pack: str


class GroupExtension(BaseModel):
    """One approved extension, and whether this group's people get it."""

    extension_id: str
    name: str
    description: str
    #: The service it comes from — where its website, and its admin
    #: screens, are.
    service_url: str
    enabled: bool


class GroupExtensionList(BaseModel):
    """Every approved extension, each with whether this group has it on.

    Off by default: a newly approved extension reaches nobody until an
    org admin switches it on for a group (`docs/service-identity.md` §8.3).
    """

    extensions: list[GroupExtension]


class SetGroupExtension(BaseModel):
    enabled: bool
