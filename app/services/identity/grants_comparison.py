"""Whether a new version of an extension asks for more than the approved one — and what more.

`GrantsComparison.between` is the one place the rule is written; what
counts as a grant is in `app.services.identity.extension_grants`. The rule
is deliberately conservative: anything not literally approved before is
wider, without reasoning about globs or hosts covering one another.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, computed_field, ConfigDict, Field

from app.services.identity.extension_grants import ExtensionGrants, ToolAudience, WorkspaceLevel
from app.services.identity.impact import ImpactEdit, ImpactOverride, ImpactTool


class GrantKind(StrEnum):
    """What an added grant is, with the words the portal shows for it."""

    label: str

    def __new__(cls, value: str, label: str) -> GrantKind:
        kind = str.__new__(cls, value)
        kind._value_ = value
        kind.label = label
        return kind

    WORKSPACE = 'workspace', "Access to people's projects"
    WORKSPACE_PATHS = 'workspace_paths', 'Project paths'
    EGRESS = 'egress', 'Network egress'
    SECRET = 'secret', 'Secret'
    HOST = 'host', 'Host capability'
    RPC = 'rpc', 'RPC method'
    SERVICE = 'service', 'What its service may send to agents'
    TOOL = 'tool', 'Tool for the agent'
    TOOL_HINTS = 'tool_hints', 'Riskier tool'
    TOOL_AUDIENCE = 'tool_audience', 'Which agents may call its tools'
    HOOK = 'hook', 'Hook'
    PROMPT_OVERRIDE = 'prompt_override', 'Prompt override'
    PROMPT_EDIT = 'prompt_edit', 'Prompt section'
    ANCHOR = 'anchor', 'Prompt anchor'
    COMMAND = 'command', 'Slash command'
    PAGE = 'page', 'Page'
    AGENT = 'agent', 'Agent'
    SKILL = 'skill', 'Skill'


#: A tool's hints, each with its riskier value and how that reads.
_RISKIER_HINTS: tuple[tuple[str, bool, str], ...] = (
    ('readOnlyHint', False, 'no longer read-only'),
    ('destructiveHint', True, 'destructive'),
    ('idempotentHint', False, 'no longer idempotent'),
    ('openWorldHint', True, 'reaches the open world'),
)


class AddedGrant(BaseModel):
    """One thing a new version asks for that the approved one did not."""

    model_config = ConfigDict(frozen=True)

    kind: GrantKind
    #: What is asked for: a host, a path, a tool's name, `main#rules`.
    item: str
    #: What the approved version had, where this widens something it had.
    was: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """The kind in words, for the portal and the log."""
        return self.kind.label

    @property
    def text(self) -> str:
        return f'{self.label}: {self.item}' + (f' (was {self.was})' if self.was is not None else '')


class GrantsComparison(BaseModel):
    """A new version against the approved one."""

    #: It asks for something the approved version did not.
    wider: bool
    added: list[AddedGrant] = Field(default_factory=list)

    @classmethod
    def between(cls, approved: ExtensionGrants, candidate: ExtensionGrants) -> GrantsComparison:
        """Whether `candidate` asks for the same as `approved` or less, or for more — and what more."""
        added = _Widening(approved, candidate).added()
        return cls(wider=bool(added), added=added)

    @property
    def reason(self) -> str | None:
        """One line a service can log, or `None` when it is not wider."""
        if not self.wider:
            return None
        return 'it asks for more than the approved version: ' + '; '.join(grant.text for grant in self.added)


class _Widening:
    """What `candidate` adds to `approved`, grant by grant, in the order the portal lists them."""

    def __init__(self, approved: ExtensionGrants, candidate: ExtensionGrants) -> None:
        self._approved = approved
        self._candidate = candidate
        self._added: list[AddedGrant] = []

    def added(self) -> list[AddedGrant]:
        approved, candidate = self._approved, self._candidate
        self._workspace()
        self._new_names(GrantKind.EGRESS, approved.egress, candidate.egress)
        self._new_names(GrantKind.SECRET, approved.secrets, candidate.secrets)
        self._new_names(GrantKind.HOST, approved.host, candidate.host)
        self._new_names(GrantKind.RPC, approved.rpc, candidate.rpc)
        self._new_names(GrantKind.SERVICE, approved.service, candidate.service)
        self._tools()
        self._new_names(GrantKind.HOOK, approved.hooks, candidate.hooks)
        self._overrides()
        self._edits()
        self._new_names(GrantKind.ANCHOR, approved.anchors, candidate.anchors)
        self._new_names(GrantKind.COMMAND, approved.commands, candidate.commands)
        self._new_names(GrantKind.PAGE, approved.pages, candidate.pages)
        self._new_names(GrantKind.AGENT, approved.agents, candidate.agents)
        self._new_names(GrantKind.SKILL, approved.skills, candidate.skills)
        return self._added

    def _add(self, kind: GrantKind, item: str, was: str | None = None) -> None:
        self._added.append(AddedGrant(kind=kind, item=item, was=was))

    def _new_names(self, kind: GrantKind, before: list[str], after: list[str]) -> None:
        for name in sorted(set(after) - set(before)):
            self._add(kind, name)

    def _workspace(self) -> None:
        """Its level, then its scope: empty paths are the whole project."""
        approved, candidate = self._approved, self._candidate
        if WorkspaceLevel.rank(candidate.workspace) > WorkspaceLevel.rank(approved.workspace):
            self._add(GrantKind.WORKSPACE, candidate.workspace, was=approved.workspace)
        none = WorkspaceLevel.NONE
        if candidate.workspace != none and approved.workspace != none and approved.workspace_paths:
            if not candidate.workspace_paths:
                self._add(GrantKind.WORKSPACE_PATHS, 'the whole project', was=', '.join(approved.workspace_paths))
            else:
                self._new_names(GrantKind.WORKSPACE_PATHS, approved.workspace_paths, candidate.workspace_paths)

    def _tools(self) -> None:
        """New ones, approved ones made riskier, and tools offered to every agent."""
        approved_tools = {tool.name: tool for tool in self._approved.tools}
        for tool in self._candidate.tools:
            before = approved_tools.get(tool.name)
            if before is None:
                self._add(GrantKind.TOOL, tool.name)
                continue
            riskier = _riskier(before, tool)
            if riskier:
                self._add(GrantKind.TOOL_HINTS, f'{tool.name}: {", ".join(riskier)}')
        if (
            self._candidate.tools
            and self._candidate.tool_audience is ToolAudience.ALL
            and self._approved.tool_audience is ToolAudience.OWN
        ):
            self._add(GrantKind.TOOL_AUDIENCE, ToolAudience.ALL, was=ToolAudience.OWN)

    def _overrides(self) -> None:
        approved: dict[str, ImpactOverride] = {o.target: o for o in self._approved.prompt_overrides}
        for override in self._candidate.prompt_overrides:
            before = approved.get(override.target)
            if before is None:
                self._add(GrantKind.PROMPT_OVERRIDE, override.target + (' (exclusive)' if override.exclusive else ''))
                continue
            if override.exclusive and not before.exclusive:
                self._add(GrantKind.PROMPT_OVERRIDE, f'{override.target} becomes exclusive')
            for section in sorted(set(override.sections) - set(before.sections)):
                self._add(GrantKind.PROMPT_EDIT, f'{override.target}#{section}')
            if before.when is not None and override.when != before.when:
                self._add(
                    GrantKind.PROMPT_OVERRIDE, _condition(override.target, override.when), was=f'while {before.when}'
                )

    def _edits(self) -> None:
        approved: dict[str, ImpactEdit] = {_edit_name(e): e for e in self._approved.prompt_edits}
        for edit in self._candidate.prompt_edits:
            name = _edit_name(edit)
            before = approved.get(name)
            if before is None:
                self._add(GrantKind.PROMPT_EDIT, f'{name} ({edit.op.value})')
            elif before.when is not None and edit.when != before.when:
                self._add(GrantKind.PROMPT_EDIT, _condition(name, edit.when), was=f'while {before.when}')


def _riskier(before: ImpactTool, after: ImpactTool) -> list[str]:
    """How `after`'s hints read where they are riskier than `before`'s."""
    return [
        words
        for hint, risky, words in _RISKIER_HINTS
        if getattr(after, hint) is risky and getattr(before, hint) is not risky
    ]


def _edit_name(edit: ImpactEdit) -> str:
    """`main#rules` for a replacement, `main+tips` for an insert or append."""
    return edit.key.partition(':')[2]


def _condition(what: str, when: str | None) -> str:
    return f'{what}, always' if when is None else f'{what}, while {when}'


__all__ = ['AddedGrant', 'GrantKind', 'GrantsComparison']
