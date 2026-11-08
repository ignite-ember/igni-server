"""What an extension asks for, and whether a new version asks for more.

A super admin approves a service having read what its extension would
put on people's machines. That approval covers what they read — not
whatever a later version asks for. So an approved service's new upload
is compared with the version it would replace, and one that asks for
**more** is held until a super admin accepts it
(`app.services.identity.extensions.ServiceExtensions`); one that asks for the same or
less goes live at once. The comparison is
`app.services.identity.grants_comparison`.

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

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

from app.db.sql.models.service_extension import ServiceExtension
from app.services.identity.extension_manifest import Manifest, PluginSummary
from app.services.identity.impact.contributions import ImpactEdit, ImpactOverride, ImpactTool
from app.services.identity.impact.errors import ImpactRefused
from app.services.identity.impact.extension_impact import ExtensionImpact


class WorkspaceLevel(StrEnum):
    """`permissions.workspace`, least to most."""

    NONE = 'none'
    READ = 'read'
    WRITE = 'write'

    @classmethod
    def rank(cls, level: str) -> int:
        """Its place in the order; a level igni does not know ranks above `write`."""
        return next((at for at, known in enumerate(cls) if known == level), len(cls))


class ToolAudience(StrEnum):
    """Whose agents may call its tools; `own-agents` is narrower."""

    OWN = 'own-agents'
    ALL = 'all-agents'


class _Scope(BaseModel):
    """The two permissions `ExtensionImpact` does not read: where in a project, and whose agents."""

    model_config = ConfigDict(extra='ignore')

    #: Empty is the whole project.
    workspacePaths: list[str] = Field(default_factory=list)  # noqa: N815 — the manifest's name for it
    toolAudience: ToolAudience = ToolAudience.ALL  # noqa: N815

    @classmethod
    def of(cls, manifest: Manifest) -> _Scope:
        permissions = (manifest.model_extra or {}).get('permissions')
        return cls.model_validate(permissions if isinstance(permissions, dict) else {})

    @field_validator('workspacePaths', mode='before')
    @classmethod
    def _paths(cls, value: Any) -> list[str]:
        return sorted({str(path) for path in value}) if isinstance(value, list) else []

    @field_validator('toolAudience', mode='before')
    @classmethod
    def _audience(cls, value: Any) -> ToolAudience:
        """Anything but `own-agents` is every agent, as the host reads it."""
        return ToolAudience.OWN if value == ToolAudience.OWN else ToolAudience.ALL


class ExtensionGrants(BaseModel):
    """Everything one version of an extension asks for."""

    model_config = ConfigDict(frozen=True)

    workspace: str = WorkspaceLevel.NONE
    #: Empty is the whole project.
    workspace_paths: list[str] = Field(default_factory=list)
    egress: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    host: list[str] = Field(default_factory=list)
    rpc: list[str] = Field(default_factory=list)
    service: list[str] = Field(default_factory=list)
    tools: list[ImpactTool] = Field(default_factory=list)
    tool_audience: ToolAudience = ToolAudience.ALL
    hooks: list[str] = Field(default_factory=list)
    prompt_overrides: list[ImpactOverride] = Field(default_factory=list)
    prompt_edits: list[ImpactEdit] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, manifest: Manifest, plugin: PluginSummary) -> ExtensionGrants:
        """Read off a stored or uploaded version: its manifest and its plugin summary.

        Refused (`ImpactRefused`) when the manifest does not read, rather
        than read as asking for nothing.
        """
        impact = ExtensionImpact.of(manifest)
        scope = _Scope.of(manifest)
        return cls(
            workspace=impact.workspace,
            workspace_paths=scope.workspacePaths,
            egress=list(impact.egress),
            secrets=list(impact.secrets),
            host=list(impact.host),
            rpc=list(impact.rpc),
            service=list(impact.service),
            tools=list(impact.tools),
            tool_audience=scope.toolAudience,
            hooks=[hook.event for hook in impact.hooks],
            prompt_overrides=list(impact.prompt_overrides),
            prompt_edits=list(impact.prompt_edits),
            anchors=list(impact.anchors),
            commands=list(impact.commands),
            pages=list(impact.pages),
            agents=sorted({agent.name for agent in plugin.agents if agent.name}),
            skills=sorted({skill.name for skill in plugin.skills if skill.name}),
        )

    @classmethod
    def of_version(cls, extension: ServiceExtension | None) -> ExtensionGrants:
        """What a stored version asks for.

        Nothing, for no version or one whose manifest no longer reads
        under today's rules: then everything a new version asks for is
        more, and it is held for a super admin rather than let through.
        """
        if extension is None:
            return cls()
        try:
            return cls.of(Manifest.model_validate(extension.manifest), PluginSummary.model_validate(extension.plugin))
        except (ImpactRefused, ValidationError):
            return cls()


__all__ = ['ExtensionGrants', 'ToolAudience', 'WorkspaceLevel']
