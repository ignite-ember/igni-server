"""Somebody who gets extensions: in an organisation, in a group that has them on.

A newly approved extension reaches nobody until an org admin switches it
on for a group (`docs/service-identity.md` §8.3), so a test that wants a
machine to receive one says which. Idempotent: a test may ask for the same
person's headers more than once.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql.group_extensions import GroupExtensionsDB
from app.db.sql.groups import GroupsDB
from app.db.sql.models.org_group import OrgGroup
from app.db.sql.models.org_group_member import OrgGroupMember
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, put_in_org

ORG_SLUG = 'entitled'
GROUP_NAME = 'Entitled'


class EntitledPerson:
    """A member of one organisation's group, with chosen extensions on for that group."""

    def __init__(self, db: AsyncSession, email: str = 'someone@example.com') -> None:
        self._db = db
        self._email = email

    async def _user_id(self, email: str) -> str:
        found = (await self._db.execute(select(User).where(User.email == email))).scalars().first()
        if found is not None:
            return str(found.user_id)
        return str((await create_user(self._db, email=email)).user_id)

    async def _org_id(self) -> str:
        found = (await self._db.execute(select(Organization).where(Organization.slug == ORG_SLUG))).scalars().first()
        if found is not None:
            return str(found.id)
        owner = await self._user_id('entitled-owner@example.com')
        return str((await create_org(self._db, owner_user_id=owner, slug=ORG_SLUG, display_name='Entitled')).id)

    async def group_id(self) -> str:
        org_id = await self._org_id()
        found = await GroupsDB().get_group_by_name(org_id, GROUP_NAME)
        if found is not None:
            return str(found.id)
        group = OrgGroup(org_id=org_id, name=GROUP_NAME)
        await put_in_org(org_id, group)
        return str(group.id)

    async def headers(self, *extension_ids: str) -> dict[str, str]:
        """This person's session headers, with *extension_ids* on for their group."""
        org_id = await self._org_id()
        group_id = await self.group_id()
        user_id = await self._user_id(self._email)
        await add_member(self._db, org_id, user_id)
        if await GroupsDB().get_user_group_summary(user_id) is None:
            await put_in_org(org_id, OrgGroupMember(group_id=group_id, user_id=user_id))
        for extension_id in extension_ids:
            await GroupExtensionsDB().enable(org_id, group_id, extension_id, by_user_id=None)
        return auth_header(make_token(user_id, org_id=org_id))
