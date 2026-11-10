"""The YAML frontmatter that opens an agent or a skill file.

One reading, for every place igni looks at one: the portal's warnings on
an agent entry (its `name:` and `tools:`), and the Services page's
summary of an extension's agents and skills (`name:` and
`description:`). Anything that is not a frontmatter block — no block,
YAML that does not parse, a block that is not a mapping — reads as an
empty one: a malformed file has a bigger problem than these fields, and
it is not the reader's to report.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

_BLOCK = re.compile(r'^\s*---\s*\n(.*?)\n---', re.S)


class Frontmatter(BaseModel):
    """The fields igni reads from a frontmatter block. The rest is ignored."""

    model_config = ConfigDict(extra='ignore', frozen=True)

    #: `None` when the block has no name, or only whitespace.
    name: str | None = None
    description: str = ''
    #: Either spelling the loader takes: a comma-joined string or a YAML list.
    tools: list[str] = Field(default_factory=list)

    @field_validator('name', mode='before')
    @classmethod
    def _named(cls, value: Any) -> str | None:
        return str(value) if value is not None and str(value).strip() else None

    @field_validator('description', mode='before')
    @classmethod
    def _described(cls, value: Any) -> str:
        return str(value) if value is not None else ''

    @field_validator('tools', mode='before')
    @classmethod
    def _tool_names(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [name.strip() for name in value.split(',') if name.strip()]
        if isinstance(value, list):
            return [str(name).strip() for name in value if str(name).strip()]
        return []

    @classmethod
    def of(cls, content: str | None) -> Frontmatter:
        """The block that opens *content*; an empty one when there is none to read."""
        match = _BLOCK.match(content or '')
        if not match:
            return cls()
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return cls()
        if not isinstance(block, dict):
            return cls()
        return cls.model_validate({str(key): value for key, value in block.items()})
