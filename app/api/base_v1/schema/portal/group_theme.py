"""A group's brand overrides, and how they are kept in the ``theme`` column."""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

logger = logging.getLogger(__name__)


#: A CSS hex colour: ``#rgb``, ``#rrggbb`` or ``#rrggbbaa``.
#:
#: Narrow on purpose. These values end up as CSS custom properties in
#: every client the group's people run, and the widest useful grammar is
#: also the one with room to hide in — ``rgb()`` takes nested
#: expressions, and named colours would have to be enumerated to be
#: checked at all. Hex covers what a colour picker emits.
#:
#: The client re-validates against the same grammar rather than trusting
#: what it is handed, for the reason ``ENTRY_NAME_PATTERN`` gives: it can
#: be pointed at a server somebody else runs.
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
    def _mark_within_size_budget(self) -> GroupTheme:
        if self.brand_mark and len(self.brand_mark.encode()) > MAX_BRAND_MARK_BYTES:
            raise ValueError(
                f'brand_mark is {len(self.brand_mark.encode())} bytes, over the {MAX_BRAND_MARK_BYTES} byte limit'
            )
        return self

    def to_column(self) -> str | None:
        """This theme as the ``theme`` column stores it, or None if it says nothing.

        A theme with every field unset overrides nothing, which *is*
        igni's palette — so it stores as NULL rather than as ``{}``. That
        keeps one representation of "no branding" in the database instead
        of two that behave identically.
        """
        payload = self.model_dump(exclude_none=True)
        if not payload:
            return None
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_column(cls, raw: str | None) -> GroupTheme | None:
        """The stored theme, or None — including when it cannot be read.

        A cosmetic column never takes a group down. Everything that reads
        a group (the portal's group views, the pack every client polls)
        reads this column, so a row that fails validation — hand-edited,
        say — serves igni's own palette instead of failing every request
        for everyone in the group. The failure is logged so it is seen.
        """
        if not raw:
            return None
        try:
            return cls.model_validate_json(raw)
        except ValidationError:
            logger.warning('group theme is unreadable; serving the default palette')
            return None
