"""A group pack, signed so igni can tell it came from this server.

igni caches the pack on the developer's machine and applies it at the
next start, often with no network. A cache is a file the user — or
anything running as the user, or a repository that could once move
`storage.data_dir` — can write. Unsigned, it could be edited, replaced
with another person's, or deleted, and igni had no way to tell.

So the pack also leaves here as a compact JWS, signed with the key igni
signs its tokens with and published at `/.well-known/jwks.json`. The
client stores that string exactly as received and verifies it before any
of it is merged (igni `core/config/group_policy_signature.py`).

## The payload

* `policy` — the pack exactly as the unsigned fields of the same response
  carry it (`OrgGroupPack.as_payload()`).
* `iss` — this server's public URL. igni compares it with its `api_url`,
  so a pack from another igni server is not this one's.
* `sub` — the person it was served to. One person's pack is not
  replayable as another's.
* `org` — the organisation the group belongs to.
* `iat` — when it was signed, in seconds; `version` — the same moment in
  milliseconds. Monotonic per server, so a client can refuse a pack
  older than one it has already applied.
* `token_use: group_policy`, and `typ` in the header.
* For a person with no pack, `policy: null` and `no_pack: true` instead
  (`sign_absence`): an answer igni can trust enough to clear its cache. No `aud` and no
  `exp`: nothing that checks an igni token accepts this as one, and the
  pack keeps working offline for as long as it is current.

## Rotation

A superseded key stays in the JWKS for about a month
(`KEEP_SUPERSEDED_FOR`), and a client offline longer than that still
verifies against the JWKS it cached. The `kid` is part of the ETag, so
the first poll after a rotation receives a pack signed by the new key
rather than a 304 that would leave the old signature in place.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.api.base_v1.schema.portal.group_pack import OrgGroupPack
from app.security import jose
from app.services.auth.jwt_service import JWTService
from app.services.identity.key_ring import KeyRing, SigningKeys

#: The JWS `typ` header. Distinct from a token's, so neither is mistaken
#: for the other by anything that looks at the header first.
PACK_TYPE = 'igni-group-policy+jwt'

#: The `token_use` claim. `JWTService.verify` demands a specific one, so
#: a pack is never accepted where a token is expected.
PACK_USE = 'group_policy'


class GroupPackSigner:
    """Signs packs with the process's key ring; holds no state of its own."""

    def __init__(self, ring: Callable[[], KeyRing] = SigningKeys.ring) -> None:
        self._ring = ring

    @property
    def issuer(self) -> str:
        return JWTService(self._ring).issuer

    @property
    def kid(self) -> str:
        return self._ring().kid

    def binding(self, *, subject: str, org_id: str | None) -> str:
        """What, besides the content, a signed pack depends on.

        Folded into the ETag: two people in one group hold the same
        content but not the same signed pack, and a key rotation changes
        the signature without changing the content.
        """
        return '|'.join((self.issuer, subject, org_id or '', self.kid))

    def sign(self, pack: OrgGroupPack, *, subject: str, org_id: str | None) -> str:
        """The pack as a compact JWS bound to `subject`."""
        return self._sign({'policy': pack.as_payload()}, subject=subject, org_id=org_id)

    def sign_absence(self, *, subject: str, org_id: str | None) -> str:
        """ "`subject` has no pack", signed the same way.

        Without it, "no pack" was an unsigned `null` igni could only ignore,
        so a person removed from every group kept the last policy forever,
        and a forged "no pack" would have been as good as a real one. Signed,
        igni clears what it cached; and since it carries a `version`, an
        older pack put back afterwards is still refused as a rollback.
        """
        return self._sign({'policy': None, 'no_pack': True}, subject=subject, org_id=org_id)

    def _sign(self, body: dict[str, object], *, subject: str, org_id: str | None) -> str:
        ring = self._ring()
        now = datetime.now(timezone.utc)
        claims: dict[str, object] = {
            'iss': self.issuer,
            'sub': subject,
            'org': org_id,
            'iat': int(now.timestamp()),
            'version': int(now.timestamp() * 1000),
            'token_use': PACK_USE,
            **body,
        }
        return str(
            jose.encode(
                claims,
                ring.private_pem(),
                algorithm='RS256',
                headers={'kid': ring.kid, 'typ': PACK_TYPE},
            )
        )
