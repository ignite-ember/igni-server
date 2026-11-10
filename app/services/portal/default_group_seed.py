"""What igni ships, as the entries a new Default group is seeded with.

The agents, skills and hook scripts are vendored copies of igni's
``bundled_agents`` and ``bundled_skills``, kept in this repo rather than
read from an installed igni: the server has no reason to depend on the
CLI package, and a deployment may not have it at all.
``make refresh-default-seed`` re-copies them.

The hook declarations are written here. Each reaches its script through
``{group_scripts}``, which the CLI expands to wherever it materialised
the group's ``scripts`` entries, so a hook and the script it runs always
arrive together. The client stops registering these hooks itself once a
group ships them, so they fire once rather than twice.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.db.sql.schemas.group import EntryCreate
from app.services.portal.entry_kinds import ALLOWED_CONTENT_TYPE

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent


@dataclass(frozen=True)
class SeedEntry:
    """One entry of the seed. The entry name is the file stem (or the hook's name)."""

    kind: str
    entry_name: str
    content: str

    def to_entry_create(self) -> EntryCreate:
        """The entry as the group store creates it: enabled, in its kind's content type."""
        return EntryCreate(
            kind=self.kind,
            entry_name=self.entry_name,
            content=self.content,
            content_type=ALLOWED_CONTENT_TYPE[self.kind],
            enabled=True,
        )


class SeedHook(BaseModel):
    """A hook declaration igni provisions into every project.

    Serialised in field order, with an absent `background` left out, so
    the stored JSON matches what earlier seeds wrote byte for byte.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    event: str
    command: str
    matcher: str
    timeout: int
    background: bool | None = None

    def to_seed_entry(self) -> SeedEntry:
        declaration = {'event': self.event, 'type': 'command', 'command': self.command}
        declaration |= self.model_dump(include={'matcher', 'timeout', 'background'}, exclude_none=True)
        return SeedEntry(kind='hooks', entry_name=self.name, content=json.dumps(declaration, indent=2))


@dataclass(frozen=True)
class SeedDirectory:
    """A directory of vendored files of one kind. The glob is per kind: scripts are ``.sh``."""

    kind: str
    directory: Path
    pattern: str

    def entries(self) -> list[SeedEntry]:
        """Every matching file, sorted by path. A missing or empty directory is logged, not raised."""
        if not self.directory.is_dir():
            logger.warning('No seed %s at %s', self.kind, self.directory)
            return []
        found = sorted(self.directory.glob(self.pattern))
        if not found:
            logger.warning('Seed directory %s holds no %s', self.directory, self.pattern)
        return [SeedEntry(self.kind, path.stem, path.read_text(encoding='utf-8')) for path in found]


@dataclass(frozen=True)
class DefaultGroupSeed:
    """Everything a new Default group is seeded with: vendored files, then hooks.

    The plain agents only; an extension's ``<agent>.<variant>.md``
    versions ship with the extension.
    """

    directories: tuple[SeedDirectory, ...]
    hooks: tuple[SeedHook, ...]

    def entries(self) -> list[SeedEntry]:
        out = [entry for directory in self.directories for entry in directory.entries()]
        out.extend(hook.to_seed_entry() for hook in self.hooks)
        return out


BUNDLED_SEED = DefaultGroupSeed(
    directories=(
        SeedDirectory('agents', _HERE / 'default_group_agents', '*.md'),
        SeedDirectory('skills', _HERE / 'default_group_skills', '*.md'),
        SeedDirectory('scripts', _HERE / 'default_group_scripts', '*.sh'),
    ),
    hooks=(
        SeedHook(
            name='pre-pr-review',
            event='PreToolUse',
            command='"{group_scripts}/pre-pr-review.sh"',
            matcher='Bash',
            timeout=15000,
        ),
        SeedHook(
            name='post-commit-todo',
            event='PostToolUse',
            command='"{group_scripts}/post-commit-todo.sh"',
            matcher='Bash',
            timeout=15000,
            background=True,
        ),
    ),
)
