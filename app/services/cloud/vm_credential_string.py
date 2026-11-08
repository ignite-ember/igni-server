"""A VM credential as the VM holds it, and the successor each exchange derives from it.

    igvm1.<org_id>.<family_id>.<generation>.<secret>

For audience `igni-vm`; igni server stores only the secret's hash
(`app.services.cloud.vm_credentials`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from pydantic import BaseModel

from app.config import settings
from app.db.sql.models.vm_credential import IGNI_VM
from app.services.identity.errors import OAuthError
from app.services.secrets.cipher import previous_keys, SecretCipherUnavailable

_PREFIX = 'igvm1'


class VmCredentialString(BaseModel):
    """The parts of a VM credential."""

    org_id: str
    family_id: str
    generation: int
    secret: str

    def render(self) -> str:
        return f'{_PREFIX}.{self.org_id}.{self.family_id}.{self.generation}.{self.secret}'

    @property
    def secret_hash(self) -> str:
        return hashlib.sha256(self.secret.encode()).hexdigest()

    @classmethod
    def parse(cls, value: str) -> VmCredentialString:
        parts = value.split('.')
        if len(parts) != 5 or parts[0] != _PREFIX or not parts[3].isdigit():
            raise OAuthError.invalid_grant(f'not a VM credential for {IGNI_VM}')
        return cls(org_id=parts[1], family_id=parts[2], generation=int(parts[3]), secret=parts[4])

    @classmethod
    def fresh(cls, org_id: str, family_id: str) -> VmCredentialString:
        """A new family's first credential."""
        return cls(org_id=org_id, family_id=family_id, generation=0, secret=secrets.token_urlsafe(32))

    def successors(self) -> list[VmCredentialString]:
        """The credential that replaces this one, under each key this deployment holds, current first.

        Derived, not drawn: HMAC-SHA256 of the family, the next generation
        and this credential's secret, keyed from `SECRET_ENCRYPTION_KEY`.
        So a retry of a lost reply — which presents this credential again
        — recomputes exactly the successor it was issued, and igni server
        never stores a usable credential to answer it with: only its hash.
        Unpredictable without both the presented secret and the key. The
        previous keys are tried as well, so a key rotation inside the
        retry window does not turn a retry into a reuse.
        """
        return [self._successor(key) for key in [settings.secret_encryption_key, *previous_keys()]]

    def _successor(self, key: str | None) -> VmCredentialString:
        if not key:
            raise SecretCipherUnavailable('secret_encryption_key is not configured; VM credentials need it')
        message = f'{self.family_id}.{self.generation + 1}.{self.secret}'.encode()
        mac = hmac.new(hashlib.sha256(b'igni-vm-credential:' + key.encode()).digest(), message, hashlib.sha256)
        return VmCredentialString(
            org_id=self.org_id,
            family_id=self.family_id,
            generation=self.generation + 1,
            secret=base64.urlsafe_b64encode(mac.digest()).rstrip(b'=').decode(),
        )
