"""A person is a member of one organisation at a time, and the database says so.

Different organisations own different domains, and an account's address
decides its organisation; `uq_org_memberships_one_org_per_person` makes
that a constraint, so no path — however it was written — can put a
person in two (`docs/service-identity.md` §20).
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.organizations import OrganizationDB
from tests.cloud_world import shared_plane
from tests.conftest import create_org, create_user

pytestmark = pytest.mark.anyio


def _membership(org_id: str, user_id: str, status: str = 'active') -> OrgMembership:
    now = datetime.now(timezone.utc)
    return OrgMembership(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
        role='member',
        status=status,
        created_at=now,
        updated_at=now,
    )


async def test_a_second_membership_is_refused_by_the_database(db):
    await create_user(db, user_id='ada', email='ada@acme.com')
    acme = await create_org(db, owner_user_id='ada', slug='acme')
    await create_user(db, user_id='owner', email='owner@globex.com')
    globex = await create_org(db, owner_user_id='owner', slug='globex')

    db.add(_membership(str(globex.id), 'ada'))
    with pytest.raises(IntegrityError) as refused:
        await db.commit()

    assert 'uq_org_memberships_one_org_per_person' in str(refused.value)
    assert acme is not None


async def test_a_deactivated_membership_still_counts_and_an_invitation_does_not(db):
    await create_user(db, user_id='ada', email='ada@acme.com')
    await create_user(db, user_id='owner', email='owner@globex.com')
    globex = str((await create_org(db, owner_user_id='owner', slug='globex')).id)
    acme = str((await create_org(db, owner_user_id='other', slug='acme')).id)
    db.add(_membership(acme, 'ada', status='deactivated'))
    await db.commit()

    db.add(_membership(globex, 'ada'))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    db.add(_membership(globex, 'invited-x1', status='invited'))
    db.add(_membership(acme, 'invited-x2', status='invited'))
    await db.commit()


async def test_the_dev_path_does_not_add_somebody_already_in_one(client):
    async with shared_plane() as db:
        await create_user(db, user_id='dev', email='dev@acme.com')
        await create_org(db, owner_user_id='dev', slug='acme')

    await OrganizationDB().ensure_exists(
        org_id=str(uuid.uuid4()), slug='dev-org', display_name='Dev', owner_user_id='dev'
    )

    async with shared_plane() as db:
        rows = await db.execute(select(OrgMembership.org_id).where(OrgMembership.user_id == 'dev'))
        assert len(rows.all()) == 1
