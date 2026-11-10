"""Spelling and reading claim names, as igni does: `<kind>:<name>`."""

from __future__ import annotations

from enum import StrEnum

#: An agent's YAML header, a section of every agent's prompt.
FRONTMATTER = 'frontmatter'


class ClaimKind(StrEnum):
    PROMPT = 'prompt'
    COMMAND = 'command'
    ANCHOR = 'anchor'


class EditOp(StrEnum):
    APPEND = 'append'
    INSERT = 'insert'
    REPLACE = 'replace'


class Position(StrEnum):
    BEFORE = 'before'
    AFTER = 'after'


class Claim:
    """Spelling and reading claim names, as igni does."""

    @staticmethod
    def prompt(target: str) -> str:
        return f'{ClaimKind.PROMPT.value}:{target}'

    @staticmethod
    def addition(target: str, name: str) -> str:
        return f'{ClaimKind.PROMPT.value}:{target}+{name}'

    @staticmethod
    def command(command: str) -> str:
        return f'{ClaimKind.COMMAND.value}:{command}'

    @staticmethod
    def anchor(name: str) -> str:
        return f'{ClaimKind.ANCHOR.value}:{name}'

    @staticmethod
    def whole_prompt(claim: str) -> str | None:
        kind, _, name = claim.partition(':')
        if kind != ClaimKind.PROMPT.value or '#' in name or '+' in name:
            return None
        return name

    @staticmethod
    def prompt_document(item: str) -> str | None:
        kind, _, name = item.partition(':')
        if kind != ClaimKind.PROMPT.value:
            return None
        return name.partition('#')[0].partition('+')[0]

    @staticmethod
    def is_edit(item: str) -> bool:
        kind, _, name = item.partition(':')
        return kind == ClaimKind.PROMPT.value and ('#' in name or '+' in name)

    @staticmethod
    def is_addition(item: str) -> bool:
        """An insert or an append: switched off on its own, but never a claim."""
        return '+' in item.partition(':')[2]


__all__ = ['FRONTMATTER', 'Claim', 'ClaimKind', 'EditOp', 'Position']
