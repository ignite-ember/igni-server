"""Deactivating a member: what it stops, and what it does not.

B1. `AC-11` in the compliance tracker read *"a signed 30-day token
cannot be withdrawn while the account exists, so an operator who
suspects a leaked token has no lever short of deleting the person."*

Two things are wrong with that, and both matter to whoever answers an
auditor.

**There is a lever.** `PATCH /portal/team/{id}/status` sets
`user_roles.status = 'deactivated'`, and every request that resolves an
organisation goes through `get_active_member`, which filters on
`status == 'active'`. It takes effect on the *next* request — no
waiting for a 30-day token to expire — and it is reversible.

**But it is scoped to the organisation, not to the token.** A portal
login mints `create_token(user_id)` with no `org` claim (only the CLI's
token carries one), so the organisation comes from the `X-Org-Id`
header the portal sends. Leave the header off and
`get_or_create_personal_org` answers instead: the deactivated person
still has a working session in a personal organisation of their own.
Nothing of the customer's is reachable that way, which is why this is a
scope statement and not a finding — but "revocation works" and
"revocation does not work" are both wrong, and the shape is what
belongs in the register.

Revocation of the credential itself is now built — DEC-10, goal B5,
`POST /portal/me/revoke-sessions` and its admin twin, proven in
`test_withdrawn_sessions_are_refused.py`. It did **not** change the third
test here, and that is the finding worth keeping: deactivation and
revocation remain two different acts. Deactivation ends organisation
access and leaves the credential alive; revocation ends the credential
and leaves the membership alone. An operator facing a leaked token needs
the second, and until B5 they only had the first.

## The boundary moved, deliberately

This file asked to be told, so: deactivation is now wired into the
watermark, and the credential is withdrawn with the membership.

The scope statement above — *"nothing of the customer's is reachable
that way"* — stopped being true. It was written when everything the
customer owned resolved through an organisation. Documents do not:
`shared_artifacts` is owned per user, and its routes never ask which
organisation you are acting as. Measured against a running server
before the change, a deactivated member holding their old token could
read `/v1/artifacts/shared`, read `/v1/notifications`, **publish a new
document and append a version to it**. Only `/v1/portal/*` refused
them.

Membership could not have carried the fix either. Every account is
given a personal organisation at sign-up, so deactivating the company
membership leaves an active one beside it, and a per-user resource is
not the organisation's to withdraw.

So the fact moved to the account: `users.deactivated_at`, checked
beside `tokens_valid_after` at the one point every authenticated
request already passes, and refused at sign-in too because the door
only sees tokens. See
`test_somebody_who_left_cannot_get_back_in.py`, which sweeps the whole
authenticated surface.

**What is left of the distinction**, because it is still real: the two
acts have different *reach*, not different strength. Revoking sessions
ends the credential and leaves the person able to sign in again.
Deactivating ends the credential **and** refuses the next sign-in.
An operator facing a leaked token still wants the first; one facing a
departure wants the second.
"""

from __future__ import annotations

import uuid

import anyio
import pytest

from tests.conftest import add_member, auth_header, create_org, create_user, make_token

ORG_SCOPED = '/portal/groups'
"""Readable by an ordinary member, and it resolves org membership.

`/portal/team` was the first choice and it is admin-only, so the
control failed for a reason that had nothing to do with deactivation.
"""


@pytest.fixture
async def member_pair(client, db):
    """An org with an admin and an ordinary member, both active."""
    suffix = uuid.uuid4().hex[:8]
    admin_id, member_id = f'admin-{suffix}', f'member-{suffix}'
    await create_user(db, user_id=admin_id, email=f'{admin_id}@example.invalid', full_name='Admin')
    await create_user(db, user_id=member_id, email=f'{member_id}@example.invalid', full_name='Member')
    org = await create_org(db, owner_user_id=admin_id, slug=f'acme-{suffix}', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id=member_id, role='member')

    org_id = str(org.id)
    return {
        'org_id': org_id,
        'member_id': member_id,
        'admin_token': make_token(admin_id, org_id=org_id),
        # No `org` claim, which is what a portal login actually mints.
        # The organisation comes from the header, as it does in the app.
        'member_token': make_token(member_id),
        'member_headers': {
            **auth_header(make_token(member_id)),
            'X-Org-Id': org_id,
        },
    }


async def _deactivate(client, member, status_value: str):
    resp = await client.patch(
        f'/portal/team/{member["member_id"]}/status',
        json={'status': status_value},
        headers=auth_header(member['admin_token']),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_an_active_member_can_read_the_org(client, member_pair):
    """The control, so the next assertion is about deactivation rather
    than about the fixture being wrong."""
    r = await client.get(ORG_SCOPED, headers=member_pair['member_headers'])
    assert r.status_code == 200, r.text


async def test_deactivation_cuts_org_access_off_on_the_next_request(client, member_pair):
    """The half AC-11 understated: there *is* an immediate lever.

    401 rather than the 403 this once asserted. The membership check
    that produced the 403 is still there and still correct; it is no
    longer what refuses first, because the credential is withdrawn
    with the membership and the door comes before the route.
    """
    assert (await _deactivate(client, member_pair, 'deactivated'))['new_status'] == 'deactivated'

    after = await client.get(ORG_SCOPED, headers=member_pair['member_headers'])
    assert after.status_code in (
        401,
        403,
    ), f'a deactivated member still read the organisation. Got {after.status_code}: {after.text}'


async def test_the_credential_no_longer_works_outside_the_org_either(client, member_pair):
    """What this used to assert, inverted on purpose.

    It read: drop the `X-Org-Id` header and the same token still
    authenticates the person into a personal organisation — "nothing
    of the customer's, but proof that the token was never withdrawn".

    The second half was true and the first stopped being true when
    documents arrived. They are owned per user and resolve no
    organisation at all, so the header made no difference to them: a
    deactivated member read and wrote them with exactly this token.

    The credential is withdrawn with the membership now. This is where
    that gets noticed if it is ever undone.
    """
    await _deactivate(client, member_pair, 'deactivated')

    me = await client.get('/portal/me', headers=auth_header(member_pair['member_token']))
    assert me.status_code == 401, (
        'a deactivated person still held a working credential. Deactivation is '
        f'recorded on the account and checked at the door. Got {me.status_code}: {me.text}'
    )


async def test_reactivation_restores_access(client, member_pair):
    """Reversible, which is what makes it usable as an incident-response
    step rather than a one-way door.

    With a **fresh** token: deactivation withdraws the sessions the
    person held, and coming back is not a reason to re-admit the one on
    the laptop that was the reason they went. The membership is
    restored; signing in again is the cost.
    """
    await _deactivate(client, member_pair, 'deactivated')
    await _deactivate(client, member_pair, 'active')

    # Past the withdrawal watermark, which rounds up to the next whole
    # second and treats equal as revoked.
    await anyio.sleep(2.2)
    restored = {
        **auth_header(make_token(member_pair['member_id'])),
        'X-Org-Id': member_pair['org_id'],
    }

    back = await client.get(ORG_SCOPED, headers=restored)
    assert back.status_code == 200, back.text


async def test_the_old_token_is_not_restored_with_them(client, member_pair):
    """The other half of reversible, and the one worth stating.

    An operator who deactivates somebody because a laptop went missing
    and then reactivates them has not handed the laptop back its
    session.
    """
    await _deactivate(client, member_pair, 'deactivated')
    await _deactivate(client, member_pair, 'active')

    old = await client.get(ORG_SCOPED, headers=member_pair['member_headers'])
    assert old.status_code == 401, old.text
