"""What an extension bundle declares: its manifest, and the plugin read from it.

Both are stored with every uploaded version (`ServiceExtension.manifest`
and `.plugin`) and read back for the Services page, for distribution, and
for comparing what a new version asks for (`app.services.identity.extension_grants`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.base_v1.schema.vm_profile_requests import ManifestVm


class PromptOverride(BaseModel):
    """One `contributes.prompt.overrides` entry, as far as the plugin summary reads it."""

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    target: str
    source: str | None = None
    from_: str = Field(default='', alias='from')
    exclusive: bool = False
    when: str | None = None

    @field_validator('source', mode='before')
    @classmethod
    def _source_is_text(cls, value: Any) -> Any:
        """Impact does not read `source`, so anything but text means "not a file" rather than a refusal."""
        return value if isinstance(value, str) else None

    @property
    def agent(self) -> str | None:
        """The agent it overrides, or `None` when it overrides another prompt."""
        return self.target.removeprefix('agent:') if self.target.startswith('agent:') else None

    @property
    def file(self) -> str:
        """The bundle file that defines it, when its source is a file; else empty."""
        return self.from_.strip('/') if self.source == 'file' else ''


class PromptContribution(BaseModel):
    model_config = ConfigDict(extra='ignore')

    overrides: list[PromptOverride] = Field(default_factory=list)


class Contributions(BaseModel):
    """The parts of `contributes` the plugin summary reads.

    Read once `ExtensionImpact` has accepted the manifest, so the shapes
    here have already been checked.
    """

    model_config = ConfigDict(extra='ignore')

    prompt: PromptContribution = Field(default_factory=PromptContribution)
    #: The plugin's root directory in the bundle.
    plugin: str | None = None

    @property
    def plugin_root(self) -> str:
        return (self.plugin or '').strip('/')


class Manifest(BaseModel):
    """The parts of an extension manifest igni reads. The rest is kept as sent."""

    model_config = ConfigDict(extra='allow')

    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    name: str = ''
    description: str = ''
    vendor: str = ''
    contributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def _vm_is_readable(cls, data: Any) -> Any:
        """`vm`, what its VMs in igni's cloud would get, kept as sent but refused when malformed (§12.3)."""
        if isinstance(data, dict) and data.get('vm') is not None:
            ManifestVm.model_validate(data['vm'])
        return data

    def contributions(self) -> Contributions:
        return Contributions.model_validate(self.contributes)


class PluginAgent(BaseModel):
    """One of the plugin's own agents, under the extension's namespace."""

    file: str
    name: str
    description: str = ''


class AgentOverride(BaseModel):
    """An agent this extension overrides, as its own file defines it while it runs."""

    #: The agent's name, as igni knows it: `explorer`.
    agent: str
    file: str
    description: str = ''
    #: Takes no other extension's edits.
    exclusive: bool = False
    #: The condition it waits on, if any.
    when: str | None = None


class PluginSkill(BaseModel):
    name: str
    description: str = ''


class PluginSummary(BaseModel):
    agents: list[PluginAgent] = Field(default_factory=list)
    agent_overrides: list[AgentOverride] = Field(default_factory=list)
    skills: list[PluginSkill] = Field(default_factory=list)
