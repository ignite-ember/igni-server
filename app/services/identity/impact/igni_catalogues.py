"""igni's own prompts and slash commands, as igni lists them, and what an extension names of them.

`tests/test_extensions_collide_and_the_higher_priority_wins.py` holds the
copies beside this module to igni's when a checkout of igni sits beside
this one.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.services.identity.impact.claims import Claim
from app.services.identity.impact.extension_impact import ExtensionImpact

#: igni's prompt sections and slots, as igni's `docs/prompt-targets.json` lists them.
PROMPT_TARGETS_FILE = Path(__file__).with_name('prompt_targets.json')
#: igni's own slash commands, as igni's `docs/igni-commands.json` lists them.
IGNI_COMMANDS_FILE = Path(__file__).with_name('igni_commands.json')


class PromptTargets(BaseModel):
    """igni's prompts and their sections, as igni's `docs/prompt-targets.json` lists them."""

    documents: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)

    @classmethod
    def igni(cls) -> PromptTargets:
        return cls.model_validate(json.loads(PROMPT_TARGETS_FILE.read_text(encoding='utf-8')))

    def unknown_in(self, impact: ExtensionImpact) -> list[str]:
        """What the extension names in igni's own prompts that this igni does not have — text that would land nowhere.

        An agent igni does not ship — a plugin's — is not igni's to know,
        so only igni's own prompts are checked.
        """
        unknown = [
            o.target
            for o in impact.prompt_overrides
            if o.target not in self.documents and not o.target.startswith('agent:')
        ]
        for edit in impact.prompt_edits:
            if edit.target not in self.documents:
                if not edit.target.startswith('agent:'):
                    unknown.append(edit.target)
                continue
            if edit.section is not None and f'{edit.target}#{edit.section}' not in self.sections:
                unknown.append(f'{edit.target}#{edit.section}')
        return sorted(set(unknown))


class IgniCommands(BaseModel):
    """igni's own slash commands: claims no extension can have.

    igni runs its own command before any extension's, so an extension
    declaring one would never be run — not a collision between
    extensions, and priority does not enter into it. A bundle declaring
    one is refused at upload.
    """

    model_config = ConfigDict(frozen=True)

    commands: list[str] = Field(default_factory=list)

    @classmethod
    def igni(cls) -> IgniCommands:
        return cls.model_validate(json.loads(IGNI_COMMANDS_FILE.read_text(encoding='utf-8')))

    def claims(self) -> list[str]:
        return [Claim.command(f'/{name}') for name in self.commands]

    def clashes(self, impact: ExtensionImpact) -> list[str]:
        """The commands this extension declares that are igni's."""
        own = set(self.commands)
        return [c for c in impact.commands if c.lstrip('/') in own]


__all__ = ['IGNI_COMMANDS_FILE', 'PROMPT_TARGETS_FILE', 'IgniCommands', 'PromptTargets']
