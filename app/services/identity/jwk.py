"""Public keys as JSON Web Keys, and the thumbprint that names one.

Two kinds of key pass through here: igni's own signing keys, which it
publishes, and the keys a registered client generated for itself, which
it hands over at registration. Both are public halves only — a JWK with
a private member (`d`, `p`, `q`, …) is refused rather than stored,
because a service that sent its private key has already leaked it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel, ConfigDict, Field

#: Members that only a private JWK has. Any of them present means the
#: caller sent a private key.
PRIVATE_MEMBERS = frozenset({'d', 'p', 'q', 'dp', 'dq', 'qi', 'oth', 'k'})

#: The algorithms a key may be used with. Asymmetric only: an HMAC key
#: would have to be shared, and sharing a secret is what this replaces.
Algorithm = Literal['RS256', 'ES256']


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64url_uint(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8 or 1, 'big'))


class PublicJwk(BaseModel):
    """One public key. RSA (`n`, `e`) or P-256 (`crv`, `x`, `y`)."""

    model_config = ConfigDict(extra='forbid')

    kty: Literal['RSA', 'EC']
    kid: str = Field(default='', max_length=128)
    use: Literal['sig'] = 'sig'
    alg: Algorithm | None = None
    n: str | None = None
    e: str | None = None
    crv: Literal['P-256'] | None = None
    x: str | None = None
    y: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> PublicJwk:
        """Parse a JWK somebody sent, refusing a private one by name.

        `extra='forbid'` would refuse it too, but as "extra field `d`",
        which does not tell the sender what they did wrong.
        """
        leaked = sorted(PRIVATE_MEMBERS & set(raw))
        if leaked:
            raise ValueError(f'this is a private key (it has {", ".join(leaked)}); send only the public half')
        key = cls.model_validate(raw)
        key.check_complete()
        return key

    def check_complete(self) -> None:
        if self.kty == 'RSA' and not (self.n and self.e):
            raise ValueError('an RSA key needs n and e')
        if self.kty == 'EC' and not (self.crv and self.x and self.y):
            raise ValueError('an EC key needs crv, x and y')

    def thumbprint(self) -> str:
        """RFC 7638: SHA-256 over the required members, in order.

        What a super admin compares before approving a client: the
        service logs it at start-up, the portal shows it, and the two
        must match. Independent of `kid`, `use` and `alg`, which the
        sender chooses freely.
        """
        if self.kty == 'RSA':
            members = {'e': self.e, 'kty': 'RSA', 'n': self.n}
        else:
            members = {'crv': self.crv, 'kty': 'EC', 'x': self.x, 'y': self.y}
        canonical = json.dumps(members, separators=(',', ':'), sort_keys=True).encode()
        return _b64url(hashlib.sha256(canonical).digest())

    def as_dict(self) -> dict[str, str]:
        return self.model_dump(exclude_none=True)


class JwkSet(BaseModel):
    """What `/.well-known/jwks.json` serves, and what a client registers."""

    keys: list[PublicJwk] = Field(default_factory=list)

    def find(self, kid: str) -> PublicJwk | None:
        return next((key for key in self.keys if key.kid == kid), None)


class RsaKeyPair:
    """A freshly generated RSA key, for igni's own signing.

    2048 bits: what every JWT library verifies by default, and the size
    the OIDC providers igni already trusts use for theirs.
    """

    KEY_SIZE = 2048

    def __init__(self, private_key: rsa.RSAPrivateKey) -> None:
        self._private = private_key

    @classmethod
    def generate(cls) -> RsaKeyPair:
        return cls(rsa.generate_private_key(public_exponent=65537, key_size=cls.KEY_SIZE))

    @classmethod
    def from_pem(cls, pem: str) -> RsaKeyPair:
        loaded = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(loaded, rsa.RSAPrivateKey):
            raise ValueError('not an RSA private key')
        return cls(loaded)

    def private_pem(self) -> str:
        return self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    def public_jwk(self, *, kid: str = '') -> PublicJwk:
        numbers = self._private.public_key().public_numbers()
        key = PublicJwk(kty='RSA', alg='RS256', n=_b64url_uint(numbers.n), e=_b64url_uint(numbers.e))
        key.kid = kid or key.thumbprint()
        return key
