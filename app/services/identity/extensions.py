"""A service's extension: checked on upload, described, distributed.

The bundle is a gzipped tar with `manifest.json` at its root, exactly the
directory `igni ext install` takes. The service signs it with the key it
registered — a compact JWS over `{"extension", "version", "sha256"}`, the
SHA-256 being of the archive's bytes — and igni refuses an upload whose
signature does not verify against that key or does not match the bytes.
The same check runs again on every machine before it installs.

Uploading is allowed while the client is still pending, because a super
admin deciding whether to approve a service needs to see what it would
put on people's machines. Distribution waits for approval.

Approval covers what the super admin saw. Once the service is approved,
a new version that asks for the same or less
(`app.services.identity.extension_grants`) goes live at once; one that
asks for **more** is held: machines keep receiving the approved version,
and a super admin approves or rejects the held one on the Services page
(`HeldUpdates`). A newer upload replaces a held one. The upload still
succeeds, and says it is held and why, so the service can log it.

The archive is read here only for what the portal shows — the manifest,
the plugin's agents, the agents it stands in for, and its skills — and
never unpacked to disk. Every member is size-checked, and anything that
is not a regular file is ignored rather than followed.

What the extension changes in igni is read off the manifest too
(`app.services.identity.impact`), for the Services page. A collision
never holds an upload back: it is settled by the super admin's priority
order, here and on every machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import io
import json
import re
import tarfile
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml

from app.db.sql.models.registered_client import APPROVED, RegisteredClient, SERVICE
from app.db.sql.models.service_extension import HELD, LIVE, ServiceExtension
from app.db.sql.registered_clients import RegisteredClientDB
from app.db.sql.service_extensions import ServiceExtensionDB
from app.security import jose
from app.services.identity.extension_assets import AssetRefused, BundleAssets, ExtensionAssets
from app.services.identity.extension_grants import compare_grants, ExtensionGrants, GrantsComparison
from app.services.identity.impact import ExtensionImpact, IgniCommands, ImpactRefused
from app.services.identity.jwk import JwkSet, PublicJwk

#: The largest bundle a service may upload. A bundle carries the
#: extension and every dependency as wheels for each supported platform,
#: so a machine with no network can install it.
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024

#: The upload route, for the body-size middleware's larger limit.
EXTENSION_UPLOAD_PATH = r'/v1/clients/[^/]+/extension$'

#: The most an archive may expand to, and the largest single file read.
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024

_ALGORITHM_FOR = {'RSA': 'RS256', 'EC': 'ES256'}
_FRONTMATTER = re.compile(r'^\s*---\s*\n(.*?)\n---', re.S)


class ExtensionRefused(ValueError):
    """The upload cannot be accepted; the message says why."""


class ExtensionConflict(RuntimeError):
    """Another approved service already ships an extension with this id."""


class BundleClaim(BaseModel):
    """What the signature says the archive is."""

    extension: str
    version: str
    sha256: str


class Manifest(BaseModel):
    """The parts of an extension manifest igni reads. The rest is kept as sent."""

    model_config = ConfigDict(extra='allow')

    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    name: str = ''
    description: str = ''
    vendor: str = ''
    contributes: dict[str, Any] = Field(default_factory=dict)


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


class ExtensionBundle:
    """One uploaded archive, read but not unpacked."""

    def __init__(self, archive: bytes) -> None:
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ExtensionRefused(f'a bundle may be at most {MAX_ARCHIVE_BYTES} bytes')
        self.archive = archive
        self.sha256 = hashlib.sha256(archive).hexdigest()
        self._files = self._read(archive)

    @staticmethod
    def _read(archive: bytes) -> dict[str, bytes]:
        """Regular files under the size limits, by normalised path."""
        files: dict[str, bytes] = {}
        unpacked = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    unpacked += member.size
                    # Wheels are already compressed; this bounds a bomb.
                    if unpacked > MAX_UNPACKED_BYTES:
                        raise ExtensionRefused('the bundle unpacks to more than the limit')
                    path = member.name.lstrip('./')
                    if not path.endswith(('.json', '.md')) or member.size > MAX_MEMBER_BYTES:
                        continue
                    if '..' in path.split('/'):
                        raise ExtensionRefused(f'the bundle has a path outside itself: {member.name}')
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        files[path] = extracted.read()
        except (tarfile.TarError, gzip.BadGzipFile, EOFError, OSError) as exc:
            raise ExtensionRefused(f'the bundle is not a gzipped tar: {exc}') from exc
        return files

    def manifest(self) -> Manifest:
        raw = self._files.get('manifest.json')
        if raw is None:
            raise ExtensionRefused('the bundle has no manifest.json at its root')
        try:
            return Manifest.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExtensionRefused(f'manifest.json is not a valid manifest: {exc}') from exc

    def plugin(self, manifest: Manifest) -> PluginSummary:
        summary = PluginSummary()
        prompt = manifest.contributes.get('prompt')
        overrides = prompt.get('overrides') if isinstance(prompt, dict) else None
        for override in overrides if isinstance(overrides, list) else []:
            if not isinstance(override, dict) or not str(override.get('target') or '').startswith('agent:'):
                continue
            path = str(override.get('from') or '').strip('/') if override.get('source') == 'file' else ''
            front = self._frontmatter(path) if path in self._files else {}
            summary.agent_overrides.append(
                AgentOverride(
                    agent=str(override['target']).removeprefix('agent:'),
                    file=path,
                    description=str(front.get('description') or ''),
                    exclusive=bool(override.get('exclusive')),
                    when=override.get('when'),
                )
            )
        root = str(manifest.contributes.get('plugin') or '').strip('/')
        if not root:
            return summary
        for path in sorted(self._files):
            if path.startswith(f'{root}/agents/') and path.endswith('.md'):
                front = self._frontmatter(path)
                summary.agents.append(
                    PluginAgent(
                        file=path,
                        name=str(front.get('name') or path.rsplit('/', 1)[-1].removesuffix('.md')),
                        description=str(front.get('description') or ''),
                    )
                )
            elif path.startswith(f'{root}/skills/') and path.endswith('/SKILL.md'):
                front = self._frontmatter(path)
                summary.skills.append(
                    PluginSkill(
                        name=str(front.get('name') or path.split('/')[-2]),
                        description=str(front.get('description') or ''),
                    )
                )
        return summary

    def _frontmatter(self, path: str) -> dict[str, Any]:
        match = _FRONTMATTER.match(self._files[path].decode('utf-8', errors='replace'))
        if not match:
            return {}
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return {}
        return block if isinstance(block, dict) else {}


class BundleSignature:
    @staticmethod
    def verify(signature: str, client: RegisteredClient) -> BundleClaim:
        """The claim, if one of the client's registered keys signed it."""
        keys = JwkSet.model_validate(client.jwks)
        try:
            kid = str(jose.unverified_header(signature).get('kid') or '')
        except jose.TokenError as exc:
            raise ExtensionRefused('the signature is not a compact JWS') from exc
        candidates: list[PublicJwk] = [k for k in keys.keys if k.kid == kid] if kid else list(keys.keys)
        for key in candidates:
            try:
                payload = jose.verify_jws(signature, key.as_dict(), algorithms=[_ALGORITHM_FOR[key.kty]])
            except jose.TokenError:
                continue
            try:
                return BundleClaim.model_validate_json(payload)
            except ValidationError as exc:
                raise ExtensionRefused(f'the signed claim is malformed: {exc}') from exc
        raise ExtensionRefused("the bundle is not signed by this client's registered key")


@dataclass(frozen=True)
class Publication:
    """What became of an upload."""

    extension: ServiceExtension
    #: Held for a super admin: it asks for more than the approved version.
    held: bool = False
    #: Against the approved version; `None` when there was nothing to compare with.
    comparison: GrantsComparison | None = None


def grants_of(extension: ServiceExtension | None) -> ExtensionGrants:
    """What a stored version asks for.

    Nothing, for no version or one whose manifest can no longer be read:
    then everything a new version asks for is more, and it is held.
    """
    if extension is None:
        return ExtensionGrants()
    try:
        return ExtensionGrants.of(
            dict(extension.manifest), impact=dict(extension.impact or {}), plugin=dict(extension.plugin)
        )
    except ImpactRefused:
        return ExtensionGrants()


class ServiceExtensions:
    def __init__(
        self,
        store: ServiceExtensionDB | None = None,
        clients: RegisteredClientDB | None = None,
        assets: ExtensionAssets | None = None,
    ) -> None:
        self._store = store or ServiceExtensionDB()
        self._clients = clients or RegisteredClientDB()
        self._assets = assets or ExtensionAssets(clients=self._clients)

    async def publish(self, client_id: str, archive: bytes, signature: str) -> Publication:
        client = await self._clients.get(client_id)
        if client is None or client.kind != SERVICE or client.state not in (APPROVED, 'pending'):
            raise ExtensionRefused('no such client, or it has been revoked')
        claim = BundleSignature.verify(signature, client)
        bundle = ExtensionBundle(archive)
        if claim.sha256 != bundle.sha256:
            raise ExtensionRefused('the signature is for different bytes than were uploaded')
        manifest = bundle.manifest()
        if (manifest.id, manifest.version) != (claim.extension, claim.version):
            raise ExtensionRefused('the signature names a different extension or version than the manifest')
        owner = await self._store.approved_owner_of(manifest.id)
        if owner is not None and owner != client_id:
            raise ExtensionConflict(f'another approved service already ships {manifest.id}')
        raw = manifest.model_dump(mode='json')
        try:
            await self._assets.check_declared(client_id, BundleAssets.of(raw))
        except AssetRefused as exc:
            raise ExtensionRefused(str(exc)) from exc
        try:
            impact = ExtensionImpact.of(raw)
        except ImpactRefused as exc:
            raise ExtensionRefused(str(exc)) from exc
        igni_own = IgniCommands.igni().clashes(impact)
        if igni_own:
            raise ExtensionRefused(
                f"{', '.join(igni_own)} {'is' if len(igni_own) == 1 else 'are'} igni's own: "
                'igni answers its own commands before any extension, so these would never run'
            )
        upload = ServiceExtension(
            client_id=client_id,
            stage=LIVE,
            extension_id=manifest.id,
            version=manifest.version,
            manifest=raw,
            plugin=bundle.plugin(manifest).model_dump(mode='json'),
            impact=impact.model_dump(mode='json'),
            archive=archive,
            sha256=bundle.sha256,
            signature=signature,
        )
        # Approval covers the version the super admin saw. A pending
        # service's upload is what they will see; a first upload has
        # nothing to be compared with, as before.
        live = await self._store.of(client_id) if client.state == APPROVED else None
        comparison = compare_grants(grants_of(live), grants_of(upload)) if live is not None else None
        if comparison is not None and comparison.wider:
            held = await self._store.held_of(client_id)
            if held is not None and held.sha256 == upload.sha256:
                # The same bytes again — a restart. It keeps its place.
                return Publication(extension=held, held=True, comparison=comparison)
            upload.stage = HELD
        stored = await self._store.put(upload)
        await self.keep_only_declared(client_id)
        return Publication(extension=stored, held=stored.stage == HELD, comparison=comparison)

    async def keep_only_declared(self, client_id: str) -> None:
        """An asset neither the live nor the held version declares is not distributed again; the bytes go."""
        digests: set[str] = set()
        for row in (await self._store.of(client_id), await self._store.held_of(client_id)):
            if row is not None:
                digests |= BundleAssets.of(row.manifest).digests()
        await self._assets.keep_only(client_id, digests)


class HeldUpdate(BaseModel):
    """An approved service's upload that asks for more, waiting for a super admin."""

    client_id: str
    #: The service waiting.
    service: str
    extension_id: str
    name: str
    #: The version machines receive now.
    approved_version: str | None
    #: The version waiting.
    version: str
    sha256: str
    uploaded_at: datetime
    #: What it asks for that the approved version did not.
    comparison: GrantsComparison


class HeldUpdates:
    """A super admin's review of held versions: list, approve, reject."""

    def __init__(
        self,
        store: ServiceExtensionDB | None = None,
        clients: RegisteredClientDB | None = None,
        extensions: ServiceExtensions | None = None,
    ) -> None:
        self._store = store or ServiceExtensionDB()
        self._clients = clients or RegisteredClientDB()
        self._extensions = extensions or ServiceExtensions(store=self._store, clients=self._clients)

    async def all(self) -> list[HeldUpdate]:
        held = await self._store.all_held()
        updates = [await self._describe(row) for row in held.values()]
        return sorted((u for u in updates if u is not None), key=lambda u: u.uploaded_at)

    async def of(self, client_id: str) -> HeldUpdate | None:
        row = await self._store.held_of(client_id)
        return await self._describe(row) if row is not None else None

    async def _describe(self, row: ServiceExtension) -> HeldUpdate | None:
        client = await self._clients.get(row.client_id)
        if client is None:
            return None
        live = await self._store.of(row.client_id)
        comparison = compare_grants(grants_of(live), grants_of(row))
        return HeldUpdate(
            client_id=row.client_id,
            service=client.name,
            extension_id=row.extension_id,
            name=str(row.manifest.get('name') or row.extension_id),
            approved_version=live.version if live is not None else None,
            version=row.version,
            sha256=row.sha256,
            uploaded_at=row.published_at,
            comparison=comparison,
        )

    async def approve(self, client_id: str) -> ServiceExtension | None:
        """The held version becomes the approved one, and is distributed. `None` when nothing is held."""
        held = await self._store.held_of(client_id)
        if held is None:
            return None
        owner = await self._store.approved_owner_of(held.extension_id)
        if owner is not None and owner != client_id:
            raise ExtensionConflict(f'another approved service already ships {held.extension_id}')
        promoted = await self._store.promote(client_id)
        await self._extensions.keep_only_declared(client_id)
        return promoted

    async def reject(self, client_id: str) -> bool:
        """Discard the held version; the service stays on the approved one. False when nothing is held."""
        discarded = await self._store.discard_held(client_id)
        if discarded:
            await self._extensions.keep_only_declared(client_id)
        return discarded
