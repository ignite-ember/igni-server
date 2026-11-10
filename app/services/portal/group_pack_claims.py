"""The claims of a signed group pack: the contract igni verifies.

igni checks these in `core/config/group_policy_signature.py`. The
meaning of each claim is described in `group_pack_signature`; this
module is the one place their names and types are defined.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict

from app.api.base_v1.schema.portal.group_pack import OrgGroupPack

#: The `token_use` claim. `JWTService.verify` demands a specific one, so
#: a pack is never accepted where a token is expected.
PACK_USE: Final = 'group_policy'


class GroupPackClaims(BaseModel):
    """A pack signed for one person: who signed it, for whom, and when."""

    model_config = ConfigDict(frozen=True)

    iss: str
    sub: str
    #: Always present, `null` when the group names no organisation.
    org: str | None
    #: Signing time in epoch seconds.
    iat: int
    #: The same moment in milliseconds; monotonic per server.
    version: int
    token_use: Literal['group_policy'] = PACK_USE
    policy: OrgGroupPack | None

    @classmethod
    def stamped(
        cls,
        now: datetime,
        *,
        issuer: str,
        subject: str,
        org_id: str | None,
        policy: OrgGroupPack | None = None,
    ) -> Self:
        """The claims for `subject`, signed at `now`."""
        return cls(
            iss=issuer,
            sub=subject,
            org=org_id,
            iat=int(now.timestamp()),
            version=int(now.timestamp() * 1000),
            policy=policy,
        )

    def payload(self) -> dict[str, object]:
        """The claims as they go into the JWS, JSON-ready (`policy` exactly as `OrgGroupPack.as_payload`)."""
        return self.model_dump(mode='json')


class AbsentPackClaims(GroupPackClaims):
    """ "`sub` has no pack", signed: `policy: null` and `no_pack: true`."""

    policy: None = None
    no_pack: Literal[True] = True
