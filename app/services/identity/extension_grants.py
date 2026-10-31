"""What an extension asks for, and whether a new version asks for more.

A super admin approves a service having read what its extension would
put on people's machines. That approval covers what they read — not
whatever a later version asks for. So an approved service's new upload
is compared with the version it would replace, and one that asks for
**more** is held until a super admin accepts it
(`app.services.identity.extensions`); one that asks for the same or
less goes live at once.

## What counts as a grant

From the manifest's `permissions`:

* `workspace` — `none` < `read` < `write`; and, with access, its scope:
  `workspacePaths` empty is the whole project, so going from some
  paths to none is wider, and any path not approved before is wider;
* `egress` — each host, or `host:port`, compared as written;
* `secrets`, `host` (host-API capabilities such as `sessions.post`),
  `rpc`, `service` (`messages`, `attachments`, `chats`) — each name;
* `tools` — each tool by name, and an approved tool whose hints become
  riskier: no longer read-only, now destructive, no longer idempotent,
  now open-world;
* `toolAudience` — `own-agents` becoming `all-agents`, while it has tools;
* `hooks` — each hook event.

From the impact surface (`ExtensionImpact`, and the plugin as read from
the bundle):

* a prompt override of a prompt not overridden before; one that becomes
  `exclusive`; one that gains a section; one whose `when` condition is
  dropped or changed;
* a prompt edit not made before (a section replaced, inserted or
  appended), and one whose `when` is dropped or changed;
* a `{{ANCHOR}}` filled, a slash command, a page;
* a plugin agent, a skill.

Not grants: name, description, version, menu entries (they run commands,
which count), status indicators, settings, assets, and the text of a
prompt section. Each is shown on the Services page, and none widens what
the extension may reach. The rule is deliberately conservative: anything
not literally approved before is wider, without reasoning about globs or
hosts covering one another.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.identity.impact import ExtensionImpact, ImpactEdit, ImpactOverride, ImpactRefused, ImpactTool

#: `workspace`, least to most. Anything else ranks above `write`.
WORKSPACE_ORDER = {'none': 0, 'read': 1, 'write': 2}
#: Whose agents may call its tools; the first is narrower.
OWN_AGENTS = 'own-agents'
ALL_AGENTS = 'all-agents'


class GrantKind:
    """What an added grant is, with the words the portal shows for it."""

    WORKSPACE = 'workspace'
    WORKSPACE_PATHS = 'workspace_paths'
    EGRESS = 'egress'
    SECRET = 'secret'
    HOST = 'host'
    RPC = 'rpc'
    SERVICE = 'service'
    TOOL = 'tool'
    TOOL_HINTS = 'tool_hints'
    TOOL_AUDIENCE = 'tool_audience'
    HOOK = 'hook'
    PROMPT_OVERRIDE = 'prompt_override'
    PROMPT_EDIT = 'prompt_edit'
    ANCHOR = 'anchor'
    COMMAND = 'command'
    PAGE = 'page'
    AGENT = 'agent'
    SKILL = 'skill'

    LABEL: dict[str, str] = {
        WORKSPACE: "Access to people's projects",
        WORKSPACE_PATHS: 'Project paths',
        EGRESS: 'Network egress',
        SECRET: 'Secret',
        HOST: 'Host capability',
        RPC: 'RPC method',
        SERVICE: 'What its service may send to agents',
        TOOL: 'Tool for the agent',
        TOOL_HINTS: 'Riskier tool',
        TOOL_AUDIENCE: 'Which agents may call its tools',
        HOOK: 'Hook',
        PROMPT_OVERRIDE: 'Prompt override',
        PROMPT_EDIT: 'Prompt section',
        ANCHOR: 'Prompt anchor',
        COMMAND: 'Slash command',
        PAGE: 'Page',
        AGENT: 'Agent',
        SKILL: 'Skill',
    }


class AddedGrant(BaseModel):
    """One thing a new version asks for that the approved one did not."""

    model_config = ConfigDict(frozen=True)

    kind: str
    #: The kind in words, for the portal and the log.
    label: str
    #: What is asked for: a host, a path, a tool's name, `main#rules`.
    item: str
    #: What the approved version had, where this widens something it had.
    was: str | None = None

    @property
    def text(self) -> str:
        return f'{self.label}: {self.item}' + (f' (was {self.was})' if self.was is not None else '')


class GrantsComparison(BaseModel):
    """A new version against the approved one."""

    #: It asks for something the approved version did not.
    wider: bool
    added: list[AddedGrant] = Field(default_factory=list)

    @property
    def reason(self) -> str | None:
        """One line a service can log, or `None` when it is not wider."""
        if not self.wider:
            return None
        return 'it asks for more than the approved version: ' + '; '.join(grant.text for grant in self.added)


class ExtensionGrants(BaseModel):
    """Everything one version of an extension asks for."""

    model_config = ConfigDict(frozen=True)

    workspace: str = 'none'
    #: Empty is the whole project.
    workspace_paths: list[str] = Field(default_factory=list)
    egress: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    host: list[str] = Field(default_factory=list)
    rpc: list[str] = Field(default_factory=list)
    service: list[str] = Field(default_factory=list)
    tools: list[ImpactTool] = Field(default_factory=list)
    tool_audience: str = ALL_AGENTS
    hooks: list[str] = Field(default_factory=list)
    prompt_overrides: list[ImpactOverride] = Field(default_factory=list)
    prompt_edits: list[ImpactEdit] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @classmethod
    def of(
        cls, manifest: dict[str, Any], *, impact: dict[str, Any] | None = None, plugin: dict[str, Any] | None = None
    ) -> ExtensionGrants:
        """Read off a stored or uploaded version: its manifest, its impact and its plugin summary.

        The impact is read again from the manifest, so a row stored before
        igni server recorded one is compared the same way; a manifest that
        no longer reads falls back to the impact stored with it, and with
        none stored is refused (`ImpactRefused`) rather than read as asking
        for nothing.
        """
        try:
            read = ExtensionImpact.of(manifest)
        except ImpactRefused:
            if not impact:
                raise
            read = ExtensionImpact.model_validate(impact)
        permissions = manifest.get('permissions')
        permissions = permissions if isinstance(permissions, dict) else {}
        paths = permissions.get('workspacePaths')
        summary = plugin or {}
        grants = cls(
            workspace_paths=sorted({str(p) for p in paths}) if isinstance(paths, list) else [],
            tool_audience=OWN_AGENTS if permissions.get('toolAudience') == OWN_AGENTS else ALL_AGENTS,
            agents=sorted({str(a['name']) for a in _dicts(summary.get('agents')) if a.get('name')}),
            skills=sorted({str(s['name']) for s in _dicts(summary.get('skills')) if s.get('name')}),
        )
        return grants.model_copy(
            update={
                'workspace': read.workspace,
                'egress': list(read.egress),
                'secrets': list(read.secrets),
                'host': list(read.host),
                'rpc': list(read.rpc),
                'service': list(read.service),
                'tools': list(read.tools),
                'hooks': [hook.event for hook in read.hooks],
                'prompt_overrides': list(read.prompt_overrides),
                'prompt_edits': list(read.prompt_edits),
                'anchors': list(read.anchors),
                'commands': list(read.commands),
                'pages': list(read.pages),
            }
        )


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


#: A tool's hints, each with its riskier value and how that reads.
_RISKIER_HINT = {
    'readOnlyHint': (False, 'no longer read-only'),
    'destructiveHint': (True, 'destructive'),
    'idempotentHint': (False, 'no longer idempotent'),
    'openWorldHint': (True, 'reaches the open world'),
}


def compare_grants(approved: ExtensionGrants, candidate: ExtensionGrants) -> GrantsComparison:
    """Whether `candidate` asks for the same as `approved` or less, or for more — and what more.

    The one place the rule is written; see the module docstring.
    """
    added: list[AddedGrant] = []

    def add(kind: str, item: str, was: str | None = None) -> None:
        added.append(AddedGrant(kind=kind, label=GrantKind.LABEL[kind], item=item, was=was))

    def new_names(kind: str, before: list[str], after: list[str]) -> None:
        for name in sorted(set(after) - set(before)):
            add(kind, name)

    # The workspace: its level, then its scope.
    unknown = len(WORKSPACE_ORDER)
    if WORKSPACE_ORDER.get(candidate.workspace, unknown) > WORKSPACE_ORDER.get(approved.workspace, unknown):
        add(GrantKind.WORKSPACE, candidate.workspace, was=approved.workspace)
    if candidate.workspace != 'none' and approved.workspace != 'none' and approved.workspace_paths:
        if not candidate.workspace_paths:
            add(GrantKind.WORKSPACE_PATHS, 'the whole project', was=', '.join(approved.workspace_paths))
        else:
            new_names(GrantKind.WORKSPACE_PATHS, approved.workspace_paths, candidate.workspace_paths)

    new_names(GrantKind.EGRESS, approved.egress, candidate.egress)
    new_names(GrantKind.SECRET, approved.secrets, candidate.secrets)
    new_names(GrantKind.HOST, approved.host, candidate.host)
    new_names(GrantKind.RPC, approved.rpc, candidate.rpc)
    new_names(GrantKind.SERVICE, approved.service, candidate.service)

    # Tools: new ones, and approved ones made riskier.
    approved_tools = {tool.name: tool for tool in approved.tools}
    for tool in candidate.tools:
        before = approved_tools.get(tool.name)
        if before is None:
            add(GrantKind.TOOL, tool.name)
            continue
        riskier = [
            words
            for hint, (risky, words) in _RISKIER_HINT.items()
            if getattr(tool, hint) is risky and getattr(before, hint) is not risky
        ]
        if riskier:
            add(GrantKind.TOOL_HINTS, f'{tool.name}: {", ".join(riskier)}')
    if candidate.tools and candidate.tool_audience == ALL_AGENTS and approved.tool_audience == OWN_AGENTS:
        add(GrantKind.TOOL_AUDIENCE, ALL_AGENTS, was=OWN_AGENTS)

    new_names(GrantKind.HOOK, approved.hooks, candidate.hooks)

    # Prompts: whole overrides, then edits.
    approved_overrides = {o.target: o for o in approved.prompt_overrides}
    for override in candidate.prompt_overrides:
        before_override = approved_overrides.get(override.target)
        if before_override is None:
            add(GrantKind.PROMPT_OVERRIDE, override.target + (' (exclusive)' if override.exclusive else ''))
            continue
        if override.exclusive and not before_override.exclusive:
            add(GrantKind.PROMPT_OVERRIDE, f'{override.target} becomes exclusive')
        for section in sorted(set(override.sections) - set(before_override.sections)):
            add(GrantKind.PROMPT_EDIT, f'{override.target}#{section}')
        if before_override.when is not None and override.when != before_override.when:
            add(
                GrantKind.PROMPT_OVERRIDE,
                _condition(override.target, override.when),
                was=f'while {before_override.when}',
            )
    approved_edits = {_edit_name(e): e for e in approved.prompt_edits}
    for edit in candidate.prompt_edits:
        before_edit = approved_edits.get(_edit_name(edit))
        if before_edit is None:
            add(GrantKind.PROMPT_EDIT, f'{_edit_name(edit)} ({edit.op.value})')
        elif before_edit.when is not None and edit.when != before_edit.when:
            add(GrantKind.PROMPT_EDIT, _condition(_edit_name(edit), edit.when), was=f'while {before_edit.when}')

    new_names(GrantKind.ANCHOR, approved.anchors, candidate.anchors)
    new_names(GrantKind.COMMAND, approved.commands, candidate.commands)
    new_names(GrantKind.PAGE, approved.pages, candidate.pages)
    new_names(GrantKind.AGENT, approved.agents, candidate.agents)
    new_names(GrantKind.SKILL, approved.skills, candidate.skills)

    return GrantsComparison(wider=bool(added), added=added)


def _edit_name(edit: ImpactEdit) -> str:
    """`main#rules` for a replacement, `main+tips` for an insert or append."""
    return edit.key.partition(':')[2]


def _condition(what: str, when: str | None) -> str:
    return f'{what}, always' if when is None else f'{what}, while {when}'


__all__ = [
    'ALL_AGENTS',
    'AddedGrant',
    'ExtensionGrants',
    'GrantKind',
    'GrantsComparison',
    'OWN_AGENTS',
    'WORKSPACE_ORDER',
    'compare_grants',
]
