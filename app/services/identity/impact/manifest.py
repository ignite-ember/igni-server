"""The parts of a manifest impact reads, checked as igni would check them at install.

Anything else in the manifest is igni's to check. A manifest that does
not read, or reads as something igni would refuse, is `ImpactRefused`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError

from app.services.identity.extension_manifest import Manifest
from app.services.identity.impact.claims import EditOp, Position
from app.services.identity.impact.contributions import ImpactEdit, ImpactOverride, ImpactTool
from app.services.identity.impact.errors import ImpactRefused
from app.services.identity.impact.grants import HOOK_EVENTS, ServiceGrant

_SLUG = r'^[a-z][a-z0-9-]*$'
_DOCUMENT = r'^(main|plan_mode|agent:[a-z0-9][a-z0-9_.:-]*)$'
_SECTION = r'^[a-z][a-z0-9_]*$'


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

    def impact(self) -> ImpactOverride:
        return ImpactOverride(
            target=self.target, sections=list(self.sections), exclusive=self.exclusive, when=self.when
        )


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

    def impact(self) -> ImpactEdit:
        if self.replace is not None:
            return ImpactEdit(target=self.target, op=EditOp.REPLACE, section=self.replace, when=self.when)
        if self.insert is not None:
            return ImpactEdit(
                target=self.target,
                op=EditOp.INSERT,
                section=self.before or self.after,
                position=Position.BEFORE if self.before else Position.AFTER,
                name=self.insert,
                when=self.when,
            )
        return ImpactEdit(target=self.target, op=EditOp.APPEND, name=self.append, when=self.when)


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
    promptFragments: list[_Named] = Field(default_factory=list)  # noqa: N815 — the manifest's name
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

    def impact(self) -> ImpactTool:
        return ImpactTool(name=self.name, **self.annotations.model_dump())


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

    def grants(self) -> list[ServiceGrant]:
        """Its service grants, once `check` has accepted them."""
        return sorted(ServiceGrant(grant) for grant in set(self.service))


class _Endpoint(_Read):
    """A remote MCP server. Impact reads only that there is one: no process of its own."""


class ImpactManifest(_Read):
    """A manifest as impact reads it, accepted only when igni would accept it."""

    id: str
    version: str
    name: str | None = None
    endpoint: _Endpoint | None = None
    permissions: _Permissions = Field(default_factory=_Permissions)
    contributes: _Contributes = Field(default_factory=_Contributes)

    @classmethod
    def read(cls, manifest: Manifest) -> ImpactManifest:
        """Read and check. Refused when its contributions are malformed."""
        try:
            read = cls.model_validate(manifest.model_dump(mode='json'))
        except ValidationError as exc:
            raise ImpactRefused(f'the manifest says something igni cannot read: {exc}') from exc
        read._check()
        return read

    def edits(self) -> list[ImpactEdit]:
        """Its prompt edits, in the order it declares them."""
        return [edit.impact() for edit in self.contributes.prompt.edits]

    def _check(self) -> None:
        self._check_tools()
        self._check_hooks()
        self._check_grants()
        self._check_prompt()

    def _check_tools(self) -> None:
        repeated = _repeated(tool.name for tool in self.permissions.tools)
        if repeated:
            raise ImpactRefused(f'permissions.tools names {", ".join(repeated)} twice')

    def _check_hooks(self) -> None:
        events = list(self.permissions.hooks)
        unknown = sorted(set(events) - HOOK_EVENTS)
        if unknown:
            raise ImpactRefused(f'permissions.hooks names {", ".join(unknown)}, which is not a hook event of igni')
        if events and self.endpoint is not None:
            raise ImpactRefused(
                f'permissions.hooks ({", ".join(events)}) needs a process igni starts, and an endpoint '
                'is not one: it is never asked, and its silence would block every fail-closed event'
            )
        repeated = _repeated(events)
        if repeated:
            raise ImpactRefused(f'permissions.hooks names {", ".join(repeated)} twice')

    def _check_grants(self) -> None:
        known = {grant.value for grant in ServiceGrant}
        unknown = sorted(set(self.permissions.service) - known)
        if unknown:
            raise ImpactRefused(
                f'permissions.service names {", ".join(unknown)}; a service may be granted {", ".join(sorted(known))}'
            )

    def _check_prompt(self) -> None:
        overridden = [o.target for o in self.contributes.prompt.overrides]
        edits = self.edits()
        for what, names in (('overrides', overridden), ('replaces or adds', [e.key for e in edits])):
            repeated = _repeated(names)
            if repeated:
                raise ImpactRefused(f'the prompt contribution {what} {", ".join(repeated)} twice')
        both = sorted({e.target for e in edits} & set(overridden))
        if both:
            raise ImpactRefused(f'the prompt contribution both overrides and edits {", ".join(both)}')


def _repeated(names: Iterable[str]) -> list[str]:
    """Sorted: every name that appears more than once."""
    seen = list(names)
    return sorted({n for n in seen if seen.count(n) > 1})


__all__ = ['ImpactManifest']
