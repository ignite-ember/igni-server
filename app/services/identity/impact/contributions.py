"""The parts of what one extension changes: its tools, hooks, prompt overrides and prompt edits."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.services.identity.impact.claims import Claim, EditOp, FRONTMATTER, Position


class ImpactTool(BaseModel):
    """A tool it gives the agent, and the four hints igni's permission modes decide on."""

    model_config = ConfigDict(frozen=True)

    name: str
    readOnlyHint: bool  # noqa: N815 — MCP's name for it, as the manifest has it
    destructiveHint: bool  # noqa: N815
    idempotentHint: bool  # noqa: N815
    openWorldHint: bool  # noqa: N815


class ImpactHook(BaseModel):
    """A hook event it answers, and whether its answer decides it or it is only told."""

    model_config = ConfigDict(frozen=True)

    event: str
    decides: bool = False


class ImpactOverride(BaseModel):
    """A whole prompt an extension brings."""

    model_config = ConfigDict(frozen=True)

    target: str
    #: Its sections; an agent's `frontmatter` is implied.
    sections: list[str]
    exclusive: bool = False
    when: str | None = None

    @property
    def key(self) -> str:
        return Claim.prompt(self.target)

    def has(self, section: str) -> bool:
        return section in self.sections or (section == FRONTMATTER and self.target.startswith('agent:'))


class ImpactEdit(BaseModel):
    """One change an extension makes to a prompt."""

    model_config = ConfigDict(frozen=True)

    target: str
    op: EditOp
    #: The section replaced, or inserted against.
    section: str | None = None
    position: Position | None = None
    #: The new section's name, for an insert or an append.
    name: str | None = None
    when: str | None = None

    @property
    def key(self) -> str:
        if self.op is EditOp.REPLACE:
            return Claim.prompt(f'{self.target}#{self.section}')
        return Claim.addition(self.target, self.name or '')


__all__ = ['ImpactEdit', 'ImpactHook', 'ImpactOverride', 'ImpactTool']
