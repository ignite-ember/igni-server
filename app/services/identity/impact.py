"""What an extension changes in igni, and where two extensions collide.

An extension can take over a great deal of igni — the whole of the
agent's prompt, if that is what a deployment wants. That is allowed on
purpose, and it makes one question unavoidable: what happens when two
extensions want the same thing? A super admin answers it once, for every
case, by putting the extensions in one **priority** order
(`ExtensionPriority`): the higher one wins, automatically, here on the
Services page and on every machine. Nothing is ever held back from
distribution for a collision.

This is igni server's half of a rule igni states too
(`src/ember_code/extensions/impact.py` in igni). The two are held to one
answer by `tests/fixtures/extension_impact_cases.json`, a file both
repositories carry and both run.

## Prompts: overrides and edits

Per prompt — `main`, `plan_mode`, `agent:<name>` — an extension overrides
it whole, or edits it: appends a section, inserts one before or after a
named section, replaces a named section. Edits from several extensions
stack, in priority order, and never collide unless they replace the same
section. An override is the new starting point: the others' edits land
on it where it has the section they name, and are *dropped* where it does
not — listed, not a collision.

## What collides, and who wins

A **claim** is something only one extension can have, spelled
`<kind>:<name>`. In every collision the extension highest in the priority
order keeps it, and each other loses that claim only:

* `prompt:<target>` — overriding a whole prompt. Of two overrides the
  higher wins. An `exclusive` override also takes on everyone who edits
  that prompt, and holds only above all of them: then their edits of it
  are off; if any is higher, the exclusive override is off and the edits
  stand (the next override down, if any, has the prompt);
* `prompt:<target>#<section>` — replacing one section;
* `command:/<name>` — a slash command;
* `anchor:<NAME>` — the text at a `{{NAME}}` in a prompt.

An insert or an append is spelled `prompt:<target>+<name>` where it has
to be named, and is never a claim. Pages, settings, tools and a plugin's
own agents are named in the extension's namespace and cannot collide;
grants only add. Hooks (`permissions.hooks`) never collide: igni asks
the extensions hooking an event in priority order, rejecting or changing. A `when`
condition does not avoid a collision. Every loss says why, beginning
`lower priority than <extension>`.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError


class ServiceGrant:
    """What a service may do towards producers' agents beyond a plain event: `permissions.service`.

    `docs/service-identity.md` §15. Shown to the super admin who approves
    the service, and checked by igni server on every item the service sends.
    """

    #: Post text it wrote, as the extension's message.
    MESSAGES = 'messages'
    #: Upload files and name them on items.
    ATTACHMENTS = 'attachments'
    #: Name a thread (each opens a chat), open chats, act on them.
    CHATS = 'chats'

    ALL: frozenset[str] = frozenset({MESSAGES, ATTACHMENTS, CHATS})


#: igni's prompt sections and slots, as igni's `docs/prompt-targets.json`
#: lists them. `tests/test_extensions_collide_and_the_higher_priority_wins.py` holds this copy to
#: igni's when a checkout of igni sits beside this one.
PROMPT_TARGETS_FILE = Path(__file__).with_name('prompt_targets.json')
#: igni's own slash commands, as igni's `docs/igni-commands.json` lists
#: them; held to igni's the same way.
IGNI_COMMANDS_FILE = Path(__file__).with_name('igni_commands.json')

_SLUG = r'^[a-z][a-z0-9-]*$'
_DOCUMENT = r'^(main|plan_mode|agent:[a-z0-9][a-z0-9_.:-]*)$'
_SECTION = r'^[a-z][a-z0-9_]*$'
#: An agent's YAML header, a section of every agent's prompt.
FRONTMATTER = 'frontmatter'


class ImpactRefused(ValueError):
    """A manifest whose contributions cannot be read; the message says why."""


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


class _Read(BaseModel):
    """The parts of a manifest impact reads. Anything else is igni's to check."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)


class _Text(_Read):
    from_: str = Field(alias='from', min_length=1)
    when: str | None = Field(default=None, pattern=_SLUG)


class _Override(_Text):
    target: str = Field(pattern=_DOCUMENT)
    sections: list[Annotated[str, Field(pattern=_SECTION)]] = Field(min_length=1)
    exclusive: bool = False


class _Edit(_Text):
    target: str = Field(pattern=_DOCUMENT)
    replace: str | None = Field(default=None, pattern=_SECTION)
    insert: str | None = Field(default=None, pattern=_SECTION)
    before: str | None = Field(default=None, pattern=_SECTION)
    after: str | None = Field(default=None, pattern=_SECTION)
    append: str | None = Field(default=None, pattern=_SECTION)

    @model_validator(mode='after')
    def _one_change(self) -> _Edit:
        chosen = [k for k in ('replace', 'insert', 'append') if getattr(self, k) is not None]
        if len(chosen) != 1:
            raise ValueError(f"an edit of {self.target} is exactly one of 'replace', 'insert' and 'append'")
        placed = [k for k in ('before', 'after') if getattr(self, k) is not None]
        if (self.insert is not None) != (len(placed) == 1) or len(placed) > 1:
            raise ValueError("an insert says exactly one of 'before' and 'after', and nothing else does")
        return self


class _Prompt(_Read):
    #: Forbidding what it does not know: a prompt key igni would refuse
    #: is refused here too, at upload.
    model_config = ConfigDict(extra='forbid')

    overrides: list[_Override] = Field(default_factory=list)
    edits: list[_Edit] = Field(default_factory=list)


class _Named(_Read):
    kind: str = ''
    id: str = ''
    key: str = ''
    command: str = ''
    anchor: str = ''


class _Contributes(_Read):
    prompt: _Prompt = Field(default_factory=_Prompt)
    promptFragments: list[_Named] = Field(default_factory=list)
    commands: list[_Named] = Field(default_factory=list)
    pages: list[_Named] = Field(default_factory=list)
    menu: list[_Named] = Field(default_factory=list)
    indicators: list[_Named] = Field(default_factory=list)
    settings: list[_Named] = Field(default_factory=list)
    plugin: str | None = None


class _ToolHints(BaseModel):
    """MCP's four hints, every one stated: igni's permission modes decide on them."""

    model_config = ConfigDict(extra='forbid')

    readOnlyHint: bool  # noqa: N815 — MCP's name for it
    destructiveHint: bool  # noqa: N815
    idempotentHint: bool  # noqa: N815
    openWorldHint: bool  # noqa: N815

    @model_validator(mode='after')
    def _a_read_destroys_nothing(self) -> _ToolHints:
        if self.readOnlyHint and self.destructiveHint:
            raise ValueError('a read-only tool cannot also be destructive')
        return self


class _Tool(_Read):
    name: str = Field(min_length=1)
    annotations: _ToolHints


#: igni's hook events (igni's `docs/HOOKS.md`) an extension may answer.
HOOK_EVENTS: frozenset[str] = frozenset(
    {
        'PreToolUse',
        'PostToolUse',
        'PostToolUseFailure',
        'UserPromptSubmit',
        'SessionStart',
        'SessionEnd',
        'Stop',
        'SubagentStart',
        'SubagentStop',
        'Notification',
        'PreCompact',
        'PostCompact',
        'InstructionsLoaded',
        'TaskCreated',
        'TaskCompleted',
        'StopFailure',
        'PermissionRequest',
        'PermissionDenied',
        'PlanSubmit',
    }
)

#: The hook events whose answer decides something: the extension may stop
#: the event or change it. It is only told the rest.
DECIDING_HOOKS: frozenset[str] = frozenset({'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop', 'PlanSubmit'})


class _Permissions(_Read):
    workspace: str = 'none'
    egress: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    tools: list[_Tool] = Field(default_factory=list)
    rpc: list[str] = Field(default_factory=list)
    host: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    #: What its service may do towards producers' agents beyond a plain
    #: event (`docs/service-identity.md` §15): `messages`, `attachments`, `chats`.
    service: list[str] = Field(default_factory=list)


class _Manifest(_Read):
    id: str
    version: str
    name: str | None = None
    #: Read only to know it runs no process of its own.
    endpoint: dict[str, Any] | None = None
    permissions: _Permissions = Field(default_factory=_Permissions)
    contributes: _Contributes = Field(default_factory=_Contributes)


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

    @classmethod
    def of(cls, edit: _Edit) -> ImpactEdit:
        if edit.replace is not None:
            return cls(target=edit.target, op=EditOp.REPLACE, section=edit.replace, when=edit.when)
        if edit.insert is not None:
            return cls(
                target=edit.target,
                op=EditOp.INSERT,
                section=edit.before or edit.after,
                position=Position.BEFORE if edit.before else Position.AFTER,
                name=edit.insert,
                when=edit.when,
            )
        return cls(target=edit.target, op=EditOp.APPEND, name=edit.append, when=edit.when)


class ExtensionImpact(BaseModel):
    """What one extension changes, as a super admin reviews it."""

    model_config = ConfigDict(frozen=True)

    extension_id: str
    name: str
    version: str

    #: Prompts it overrides whole.
    prompt_overrides: list[ImpactOverride] = Field(default_factory=list)
    #: Edits it makes to prompts, in the order it declares them.
    prompt_edits: list[ImpactEdit] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)

    #: Every tool it gives the agent, with its hints.
    tools: list[ImpactTool] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    menu: list[str] = Field(default_factory=list)
    indicators: list[str] = Field(default_factory=list)
    settings: list[str] = Field(default_factory=list)
    host: list[str] = Field(default_factory=list)
    #: igni's hook events it answers. Never a claim: igni asks the
    #: extensions hooking an event in priority order.
    hooks: list[ImpactHook] = Field(default_factory=list)
    rpc: list[str] = Field(default_factory=list)
    egress: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    workspace: str = 'none'
    plugin: str | None = None
    #: What its service may do towards producers' agents (§15). Approving
    #: the service grants them; igni server checks them on every item.
    service: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, manifest: dict[str, Any]) -> ExtensionImpact:
        """Read off a manifest. Refused when its contributions are malformed."""
        try:
            read = _Manifest.model_validate(manifest)
        except ValidationError as exc:
            raise ImpactRefused(f'the manifest says something igni cannot read: {exc}') from exc
        prompt = read.contributes.prompt
        tool_names = [tool.name for tool in read.permissions.tools]
        repeated_tools = sorted({n for n in tool_names if tool_names.count(n) > 1})
        if repeated_tools:
            raise ImpactRefused(f'permissions.tools names {", ".join(repeated_tools)} twice')
        events = list(read.permissions.hooks)
        unknown = sorted(set(events) - HOOK_EVENTS)
        if unknown:
            raise ImpactRefused(f'permissions.hooks names {", ".join(unknown)}, which is not a hook event of igni')
        if events and read.endpoint is not None:
            raise ImpactRefused(
                f'permissions.hooks ({", ".join(events)}) needs a process igni starts, and an endpoint '
                'is not one: it is never asked, and its silence would block every fail-closed event'
            )
        repeated_events = sorted({e for e in events if events.count(e) > 1})
        if repeated_events:
            raise ImpactRefused(f'permissions.hooks names {", ".join(repeated_events)} twice')
        unknown_grants = sorted(set(read.permissions.service) - ServiceGrant.ALL)
        if unknown_grants:
            raise ImpactRefused(
                f'permissions.service names {", ".join(unknown_grants)}; '
                f'a service may be granted {", ".join(sorted(ServiceGrant.ALL))}'
            )
        overridden = [o.target for o in prompt.overrides]
        edits = [ImpactEdit.of(e) for e in prompt.edits]
        keys = [e.key for e in edits]
        for what, names in (('overrides', overridden), ('replaces or adds', keys)):
            repeated = sorted({n for n in names if names.count(n) > 1})
            if repeated:
                raise ImpactRefused(f'the prompt contribution {what} {", ".join(repeated)} twice')
        both = sorted({e.target for e in edits} & set(overridden))
        if both:
            raise ImpactRefused(f'the prompt contribution both overrides and edits {", ".join(both)}')
        contributes = read.contributes
        permissions = read.permissions
        return cls(
            extension_id=read.id,
            name=read.name or read.id,
            version=read.version,
            prompt_overrides=[
                ImpactOverride(target=o.target, sections=list(o.sections), exclusive=o.exclusive, when=o.when)
                for o in sorted(prompt.overrides, key=lambda o: o.target)
            ],
            prompt_edits=edits,
            anchors=sorted(f.anchor for f in contributes.promptFragments),
            commands=sorted(c.command for c in contributes.commands),
            tools=sorted(
                (ImpactTool(name=tool.name, **tool.annotations.model_dump()) for tool in permissions.tools),
                key=lambda tool: tool.name,
            ),
            pages=sorted(p.kind for p in contributes.pages),
            menu=sorted(m.command for m in contributes.menu),
            indicators=sorted(i.id for i in contributes.indicators),
            settings=sorted(s.key for s in contributes.settings),
            host=sorted(permissions.host),
            hooks=sorted(
                (ImpactHook(event=event, decides=event in DECIDING_HOOKS) for event in permissions.hooks),
                key=lambda hook: hook.event,
            ),
            rpc=sorted(permissions.rpc),
            egress=sorted(permissions.egress),
            secrets=sorted(permissions.secrets),
            workspace=permissions.workspace,
            plugin=contributes.plugin,
            service=sorted(set(permissions.service)),
        )

    def claims(self) -> list[str]:
        """Everything this extension wants that only one extension can have."""
        return sorted(
            [o.key for o in self.prompt_overrides]
            + [e.key for e in self.prompt_edits if e.op is EditOp.REPLACE]
            + [Claim.command(c) for c in self.commands]
            + [Claim.anchor(a) for a in self.anchors]
        )

    def items(self) -> list[str]:
        """Everything that can be switched off on its own: the claims, and every insert and append."""
        return sorted(set(self.claims()) | {e.key for e in self.prompt_edits})

    def override(self, target: str) -> ImpactOverride | None:
        return next((o for o in self.prompt_overrides if o.target == target), None)

    def edits_of(self, target: str) -> list[ImpactEdit]:
        return [e for e in self.prompt_edits if e.target == target]

    def unknown_targets(self, targets: PromptTargets) -> list[str]:
        """What this names in igni's own prompts that this igni does not have — text that would land nowhere.

        An agent igni does not ship — a plugin's — is not igni's to know,
        so only igni's own prompts are checked.
        """
        unknown = [
            o.target
            for o in self.prompt_overrides
            if o.target not in targets.documents and not o.target.startswith('agent:')
        ]
        for edit in self.prompt_edits:
            if edit.target not in targets.documents:
                if not edit.target.startswith('agent:'):
                    unknown.append(edit.target)
                continue
            if edit.section is not None and f'{edit.target}#{edit.section}' not in targets.sections:
                unknown.append(f'{edit.target}#{edit.section}')
        return sorted(set(unknown))


class PromptTargets(BaseModel):
    """igni's prompts and their sections, as igni's `docs/prompt-targets.json` lists them."""

    documents: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)

    @classmethod
    def igni(cls) -> PromptTargets:
        return cls.model_validate(json.loads(PROMPT_TARGETS_FILE.read_text(encoding='utf-8')))


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


class Collision(BaseModel):
    """Two or more extensions wanting one thing, and which of them has it."""

    model_config = ConfigDict(frozen=True)

    claim: str
    extensions: list[str]
    #: The highest in the priority order. `None` only for an exclusive
    #: override a higher extension's edits switched off, with no other
    #: override left — then nobody holds the whole prompt.
    winner: str | None = None
    #: Sorted: the extensions that lost something in it, each by being
    #: lower in priority than another in it.
    losers: list[str] = Field(default_factory=list)


class SwitchedOff(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim: str
    reason: str


class Dropped(SwitchedOff):
    """An edit that lands nowhere, the override that leaves it out, and why."""

    #: The extension whose override has no section the edit names.
    by: str


class ResolvedImpact(BaseModel):
    collisions: list[Collision] = Field(default_factory=list)
    off: dict[str, list[SwitchedOff]] = Field(default_factory=dict)
    #: Inserts and replacements aimed at a section another extension's
    #: override does not have: listed, not a collision.
    dropped: dict[str, list[Dropped]] = Field(default_factory=dict)

    def allows(self, extension_id: str, claim: str) -> bool:
        return all(entry.claim != claim for entry in self.off.get(extension_id, []))

    def involving(self, extension_id: str) -> list[Collision]:
        """Every collision this extension is in."""
        return [c for c in self.collisions if extension_id in c.extensions]


class Priority:
    """The super admin's one order of extensions: first is highest.

    An extension the order does not name ranks below every one it does, by
    id. igni server always hands out the whole order, so that is only a
    machine with a development install.
    """

    def __init__(self, order: Iterable[str] = ()) -> None:
        self._rank = {extension_id: index for index, extension_id in enumerate(order)}

    def key(self, extension_id: str) -> tuple[int, str]:
        return (self._rank.get(extension_id, len(self._rank)), extension_id)

    def ordered(self, extension_ids: Iterable[str]) -> list[str]:
        return sorted(extension_ids, key=self.key)

    def above(self, first: str, second: str) -> bool:
        return self.key(first) < self.key(second)


def lower_than(winner: str | None) -> str:
    """How every loss by priority begins."""
    return f'lower priority than {winner}'


class ImpactResolver:
    """Finds the collisions among a set of extensions and settles each by priority."""

    def __init__(
        self,
        impacts: Iterable[ExtensionImpact],
        priority: Iterable[str] = (),
        *,
        reserved: Iterable[str] = (),
    ) -> None:
        """`priority` is the super admin's order, highest first; `reserved` the
        claims igni keeps for itself: an extension claiming one loses it, always."""
        self._impacts = {impact.extension_id: impact for impact in impacts}
        self._priority = Priority(priority)
        self._reserved = set(reserved)

    def resolve(self) -> ResolvedImpact:
        active = {ext: set(impact.items()) for ext, impact in self._impacts.items()}
        outcome = ResolvedImpact()
        for ext in sorted(active):
            for key in sorted(active[ext] & self._reserved):
                self._switch_off(
                    outcome, active, ext, {key}, f"{key} is igni's own; igni answers it before any extension could"
                )
        for key in self._keys(active):
            document = Claim.whole_prompt(key)
            if document is not None:
                self._settle_prompt(key, document, active, outcome)
                continue
            holders = self._priority.ordered(ext for ext, items in active.items() if key in items)
            if len(holders) < 2:
                continue
            winner = holders[0]
            outcome.collisions.append(
                Collision(claim=key, extensions=sorted(holders), winner=winner, losers=sorted(holders[1:]))
            )
            for loser in holders[1:]:
                self._switch_off(outcome, active, loser, {key}, f'{lower_than(winner)}, which also claims {key}')
        self._drop(outcome, active)
        return outcome

    def _settle_prompt(self, key: str, document: str, active: dict[str, set[str]], outcome: ResolvedImpact) -> None:
        """Overrides of one prompt, and an exclusive one against the edits.

        An exclusive override holds only above every extension editing its
        prompt; outranked, it is off and the edits stand. Of the overrides
        left the highest has the prompt, and if it is exclusive everybody
        else's edits of it are off.
        """
        overriders = self._priority.ordered(ext for ext, items in active.items() if key in items)
        if not overriders:
            return
        editors = self._priority.ordered(
            ext for ext in active if ext not in overriders and self._edits_of(ext, document, active)
        )
        exclusive = [ext for ext in overriders if self._exclusive(ext, document)]
        participants = set(overriders) | (set(editors) if exclusive else set())
        if len(participants) < 2:
            return
        standing: list[str] = []
        losers: set[str] = set()
        for ext in overriders:
            higher = [e for e in editors if self._priority.above(e, ext)]
            if self._exclusive(ext, document) and higher:
                self._switch_off(
                    outcome,
                    active,
                    ext,
                    {key},
                    f'{lower_than(higher[0])}, which edits {document}; an exclusive override '
                    'holds only above every extension editing its prompt',
                )
                losers.add(ext)
                continue
            standing.append(ext)
        winner = standing[0] if standing else None
        for ext in standing[1:]:
            losers.add(ext)
            self._switch_off(outcome, active, ext, {key}, f'{lower_than(winner)}, which also overrides {document}')
        if winner is not None and self._exclusive(winner, document):
            for ext in editors:
                losers.add(ext)
                self._switch_off(
                    outcome,
                    active,
                    ext,
                    self._edits_of(ext, document, active),
                    f'{lower_than(winner)}, whose override of {document} is exclusive',
                )
        outcome.collisions.append(
            Collision(claim=key, extensions=sorted(participants), winner=winner, losers=sorted(losers))
        )

    @staticmethod
    def _keys(active: dict[str, set[str]]) -> list[str]:
        everything: set[str] = set().union(*active.values()) if active else set()
        claims = {item for item in everything if '+' not in item.partition(':')[2]}
        wholes = sorted(c for c in claims if Claim.whole_prompt(c) is not None)
        rest = sorted(c for c in claims if Claim.whole_prompt(c) is None)
        return wholes + rest

    def _exclusive(self, ext: str, document: str) -> bool:
        override = self._impacts[ext].override(document)
        return override is not None and override.exclusive

    @staticmethod
    def _edits_of(ext: str, document: str, active: dict[str, set[str]]) -> set[str]:
        return {i for i in active[ext] if Claim.is_edit(i) and Claim.prompt_document(i) == document}

    @staticmethod
    def _switch_off(
        outcome: ResolvedImpact, active: dict[str, set[str]], extension_id: str, lost: set[str], reason: str
    ) -> None:
        for item in sorted(lost & active[extension_id]):
            active[extension_id].discard(item)
            outcome.off.setdefault(extension_id, []).append(SwitchedOff(claim=item, reason=reason))

    def _drop(self, outcome: ResolvedImpact, active: dict[str, set[str]]) -> None:
        """Inserts and replacements aimed at sections an override in force does not have."""
        for owner in sorted(active):
            for override in self._impacts[owner].prompt_overrides:
                if override.key not in active[owner]:
                    continue
                while_ = f' while {owner} holds {override.when}' if override.when else ''
                for ext in sorted(active):
                    if ext == owner:
                        continue
                    for edit in self._impacts[ext].edits_of(override.target):
                        if edit.op is EditOp.APPEND or edit.key not in active[ext]:
                            continue
                        if edit.section is not None and not override.has(edit.section):
                            outcome.dropped.setdefault(ext, []).append(
                                Dropped(
                                    claim=edit.key,
                                    by=owner,
                                    reason=f'{owner} overrides {override.target}{while_}, '
                                    f'and its prompt has no section {edit.section!r}',
                                )
                            )


__all__ = [
    'Claim',
    'ClaimKind',
    'Collision',
    'Dropped',
    'EditOp',
    'ExtensionImpact',
    'FRONTMATTER',
    'IgniCommands',
    'ImpactEdit',
    'ImpactHook',
    'ImpactOverride',
    'ImpactRefused',
    'ImpactResolver',
    'ImpactTool',
    'Position',
    'Priority',
    'PromptTargets',
    'ResolvedImpact',
    'SwitchedOff',
    'lower_than',
]
