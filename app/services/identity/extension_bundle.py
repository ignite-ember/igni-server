"""An uploaded extension bundle: read for what the portal shows, and its signature checked.

The bundle is a gzipped tar with `manifest.json` at its root, exactly the
directory `igni ext install` takes. The service signs it with the key it
registered — a compact JWS over `{"extension", "version", "sha256"}`, the
SHA-256 being of the archive's bytes — and igni refuses an upload whose
signature does not verify against that key or does not match the bytes.
The same check runs again on every machine before it installs.

The archive is read here only for what the portal shows — the manifest,
the plugin's agents, the agents it stands in for, and its skills — and
never unpacked to disk. Every member is size-checked, and anything that
is not a regular file is ignored rather than followed.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile

from pydantic import BaseModel, ValidationError

from app.db.sql.models.registered_client import RegisteredClient
from app.security import jose
from app.services.identity.extension_errors import ExtensionRefused
from app.services.identity.extension_manifest import AgentOverride, Manifest, PluginAgent, PluginSkill, PluginSummary
from app.services.identity.jwk import JwkSet
from app.utils.frontmatter import Frontmatter

#: The largest bundle a service may upload. A bundle carries the
#: extension and every dependency as wheels for each supported platform,
#: so a machine with no network can install it.
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024

#: The upload route, for the body-size middleware's larger limit.
EXTENSION_UPLOAD_PATH = r'/v1/clients/[^/]+/extension$'

#: The most an archive may expand to, and the largest single file read.
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024


class BundleClaim(BaseModel):
    """What the signature says the archive is."""

    extension: str
    version: str
    sha256: str

    @classmethod
    def signed(cls, signature: str, client: RegisteredClient) -> BundleClaim:
        """The claim, if one of the client's registered keys signed it."""
        try:
            payload = JwkSet.model_validate(client.jwks).verified_payload(signature)
        except jose.TokenError as exc:
            raise ExtensionRefused('the signature is not a compact JWS') from exc
        if payload is None:
            raise ExtensionRefused("the bundle is not signed by this client's registered key")
        try:
            return cls.model_validate_json(payload)
        except ValidationError as exc:
            raise ExtensionRefused(f'the signed claim is malformed: {exc}') from exc


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
        """The plugin's agents and skills, and the agents it overrides; call once the impact has read the manifest."""
        contributions = manifest.contributions()
        summary = PluginSummary()
        for override in contributions.prompt.overrides:
            if override.agent is None:
                continue
            summary.agent_overrides.append(
                AgentOverride(
                    agent=override.agent,
                    file=override.file,
                    description=self._frontmatter(override.file).description,
                    exclusive=override.exclusive,
                    when=override.when,
                )
            )
        root = contributions.plugin_root
        if not root:
            return summary
        for path in sorted(self._files):
            if path.startswith(f'{root}/agents/') and path.endswith('.md'):
                front = self._frontmatter(path)
                summary.agents.append(
                    PluginAgent(
                        file=path,
                        name=front.name or path.rsplit('/', 1)[-1].removesuffix('.md'),
                        description=front.description,
                    )
                )
            elif path.startswith(f'{root}/skills/') and path.endswith('/SKILL.md'):
                front = self._frontmatter(path)
                summary.skills.append(
                    PluginSkill(name=front.name or path.split('/')[-2], description=front.description)
                )
        return summary

    def _frontmatter(self, path: str) -> Frontmatter:
        """The file's frontmatter; an empty one when the bundle has no such file."""
        raw = self._files.get(path)
        return Frontmatter.of(raw.decode('utf-8', errors='replace')) if raw is not None else Frontmatter()
