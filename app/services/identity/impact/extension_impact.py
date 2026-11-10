"""What one extension changes in igni, as a super admin reviews it."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.services.identity.extension_manifest import Manifest
from app.services.identity.impact.claims import Claim, EditOp
from app.services.identity.impact.contributions import ImpactEdit, ImpactHook, ImpactOverride, ImpactTool
from app.services.identity.impact.grants import DECIDING_HOOKS, ServiceGrant
from app.services.identity.impact.manifest import ImpactManifest


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
    service: list[ServiceGrant] = Field(default_factory=list)

    @classmethod
    def of(cls, manifest: Manifest) -> ExtensionImpact:
        """Read off a manifest. Refused (`ImpactRefused`) when its contributions are malformed."""
        read = ImpactManifest.read(manifest)
        contributes = read.contributes
        permissions = read.permissions
        return cls(
            extension_id=read.id,
            name=read.name or read.id,
            version=read.version,
            prompt_overrides=[o.impact() for o in sorted(contributes.prompt.overrides, key=lambda o: o.target)],
            prompt_edits=read.edits(),
            anchors=sorted(f.anchor for f in contributes.promptFragments),
            commands=sorted(c.command for c in contributes.commands),
            tools=sorted((tool.impact() for tool in permissions.tools), key=lambda tool: tool.name),
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
            service=permissions.grants(),
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


__all__ = ['ExtensionImpact']
