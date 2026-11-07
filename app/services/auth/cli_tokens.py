"""The org-scoped session the CLI takes in exchange for a portal session."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.api.base_v1.schema.auth import CLITokenResponse
from app.db.redis.auth_handoff import AuthHandoffDB
from app.db.sql.organizations import OrganizationDB
from app.db.sql.team import TeamDB
from app.security.api_key_sessions import is_api_key_session, REFUSED as API_KEY_SESSION_REFUSED
from app.security.subject import resolve_existing_user_id
from app.services.auth.jwt_service import JWTService
from app.services.portal.group_extensions import GroupExtensions


class CLITokens:
    """Mints an org-scoped JWT for a current member, with a handoff code beside it."""

    def __init__(
        self,
        *,
        team_db: TeamDB | None = None,
        org_db: OrganizationDB | None = None,
        groups: GroupExtensions | None = None,
        jwt: JWTService | None = None,
        handoff: AuthHandoffDB | None = None,
    ) -> None:
        self._team_db = team_db or TeamDB()
        self._org_db = org_db or OrganizationDB()
        self._groups = groups or GroupExtensions()
        self._jwt = jwt or JWTService()
        self._handoff = handoff or AuthHandoffDB()

    async def issue(self, *, token: str, org_id: str) -> CLITokenResponse:
        """A new token with `org_id` baked in, for the holder of portal session `token`."""
        # Minting a fresh token for somebody who no longer exists would
        # hand out 30 more days on the strength of an old one. F122.
        user_id = await resolve_existing_user_id(token)
        # Nor 30 days on the strength of a 15-minute session an API key bought (§22).
        if is_api_key_session(token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=API_KEY_SESSION_REFUSED)

        membership = await self._team_db.get_active_member(org_id, user_id)
        if not membership:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail='Not a member of this organization')

        org = await self._org_db.get_by_id(org_id)
        org_name = str(org.display_name) if org else None

        group = await self._groups.group_of(user_id, org_id)
        cli_token = self._jwt.create_token(user_id, org_id=org_id, org_name=org_name, group=group)
        # Issued unconditionally rather than on request. A caller that does
        # not need it ignores one short string; a caller that needs it and
        # cannot get it puts the token in a URL instead, which is the
        # failure being removed.
        return CLITokenResponse(access_token=cli_token, handoff_code=await self._handoff.issue(cli_token))


def cli_tokens() -> CLITokens:
    """The FastAPI dependency; tests replace it through `app.dependency_overrides`."""
    return CLITokens()
