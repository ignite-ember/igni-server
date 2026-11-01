"""A registered service, as the tests play one.

It does what a real service does: generates a key pair, registers the
public half, and signs client assertions with the private half. Nothing
here reaches into igni's internals except `approve_in`, which stands in
for the super admin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import tarfile
import uuid

import jwt

from app.db.sql.models.registered_client import APPROVED, RegisteredClient
from app.db.sql.registered_clients import RegisteredClientDB
from app.services.auth.jwt_service import JWTService
from app.services.identity.assertions import ASSERTION_TYPE
from app.services.identity.jwk import RsaKeyPair

REDIRECT_URI = 'https://codeindex.example.com/auth/callback'


class RegisteredService:
    def __init__(
        self,
        *,
        kind: str = 'service',
        name: str = 'CodeIndex',
        base_url: str = 'https://codeindex.example.com',
        org_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.org_id = org_id
        self.name = name
        self.base_url = base_url
        self.pair = RsaKeyPair.generate()
        self.public = self.pair.public_jwk(kid='service-key-1')
        self.client_id = ''

    def registration(self, **overrides: object) -> dict[str, object]:
        body: dict[str, object] = {
            'client_name': self.name,
            'kind': self.kind,
            'client_uri': self.base_url,
            'redirect_uris': [REDIRECT_URI],
            'jwks': {'keys': [self.public.as_dict()]},
        }
        body.update(overrides)
        return body

    async def approve(self, *, state: str = APPROVED) -> str:
        """Registered and decided, without the portal round trip.

        Through the app's own store rather than the `db` fixture, so it
        can be called from a test body: the two run on different loops.
        """
        self.client_id = str(uuid.uuid4())
        await RegisteredClientDB().add(
            RegisteredClient(
                client_id=self.client_id,
                kind=self.kind,
                org_id=self.org_id,
                name=self.name,
                base_url=self.base_url,
                redirect_uris=[REDIRECT_URI],
                jwks={'keys': [self.public.as_dict()]},
                thumbprint=self.public.thumbprint(),
                state=state,
            )
        )
        return self.client_id

    def assertion(
        self,
        *,
        audience: str | None = None,
        lifetime: timedelta = timedelta(seconds=60),
        jti: str | None = None,
        key: RsaKeyPair | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        return str(
            jwt.encode(
                {
                    'iss': self.client_id,
                    'sub': self.client_id,
                    'aud': audience or JWTService().issuer,
                    'iat': now,
                    'exp': now + lifetime,
                    'jti': jti or str(uuid.uuid4()),
                },
                (key or self.pair).private_pem(),
                algorithm='RS256',
                headers={'kid': self.public.kid},
            )
        )

    def authenticated(self, grant_type: str, **fields: str) -> dict[str, str]:
        """A token-endpoint form with this client's assertion."""
        return {
            'grant_type': grant_type,
            'client_assertion_type': ASSERTION_TYPE,
            'client_assertion': self.assertion(),
            **fields,
        }


class ExtensionBundle:
    """A bundle as a service builds it: a gzipped tar, manifest at the root."""

    def __init__(self, files: dict[str, str]) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            for path, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.archive = buffer.getvalue()

    @classmethod
    def of(cls, manifest: dict[str, object], **files: str) -> ExtensionBundle:
        return cls({'manifest.json': json.dumps(manifest), **files})

    def signed_by(self, service: RegisteredService, *, extension: str, version: str, sha256: str = '') -> str:
        claim = {
            'extension': extension,
            'version': version,
            'sha256': sha256 or hashlib.sha256(self.archive).hexdigest(),
        }
        return str(
            jwt.PyJWS().encode(
                json.dumps(claim).encode(),
                service.pair.private_pem(),
                algorithm='RS256',
                headers={'kid': service.public.kid},
            )
        )


class ExtensionAssetFile:
    """One asset as a service uploads it: its bytes, its declaration, its signature."""

    def __init__(self, name: str, platform: str, content: bytes, *, unpack: bool = False) -> None:
        self.content = content
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.declaration: dict[str, object] = {
            'name': name,
            'platform': platform,
            'file': f'{name}-{platform}.tar.gz' if unpack else f'{name}.bin',
            'size': len(content),
            'sha256': self.sha256,
            'source': f'https://downloads.example.com/{name}/{platform}',
            'unpack': unpack,
        }

    def signed_by(self, service: RegisteredService, *, size: int | None = None, sha256: str = '') -> str:
        claim = {'asset': sha256 or self.sha256, 'size': len(self.content) if size is None else size}
        return str(
            jwt.PyJWS().encode(
                json.dumps(claim).encode(),
                service.pair.private_pem(),
                algorithm='RS256',
                headers={'kid': service.public.kid},
            )
        )
