"""A deactivated member kept receiving their organisation's group pack.

B8. `GET /v1/portal/me/group/pack` is what a starting igni session asks
for. The pack is the organisation's own configuration: agent prompts,
hook declarations and the scripts those hooks run — a hook's `command`
goes through `bash -c` on the developer's machine, which is why the
content of a pack is not a small thing to hand out.

It resolved through `DefaultGroup.ensure_for_user`, which has two
branches:

* the user is already in a group — `get_user_group_summary(user_id)`,
  which reads `org_group_members` by user id alone, with no join to
  `org_memberships` and so no notion of an active membership;
* the user is in no group — `list_user_orgs(user_id)`, which **does**
  filter `status == 'active'`.

So the strict branch was the one almost nobody takes. Every ordinary
member is in a group, so deactivating them left the pack flowing: the
customer's internal prompts and hook scripts, delivered to somebody they
had offboarded, for as long as their token lived.

The third exception to AC-11 found in this review, after B1's scoping
statement and B7's access check — and the same shape every time: a read
path that resolves a person's *relationship* without asking whether the
relationship still holds.
"""

from __future__ import annotations

import uuid

import anyio
import pytest

from tests.conftest import add_member, auth_header, create_org, create_user, make_token

pytestmark = pytest.mark.anyio


@pytest.fixture
async def member_in_a_group(client, db):
    """An org, an admin, a member, and the member placed in a group.

    The group is created through the API rather than seeded, so the row
    layout is whatever the product actually writes.
    """
    suffix = uuid.uuid4().hex[:8]
    admin_id, member_id = f'admin-{suffix}', f'member-{suffix}'
    await create_user(db, user_id=admin_id, email=f'{admin_id}@example.invalid', full_name='Admin')
    await create_user(db, user_id=member_id, email=f'{member_id}@example.invalid', full_name='Member')
    org = await create_org(db, owner_user_id=admin_id, slug=f'acme-{suffix}', display_name='Acme')
    await add_member(db, org_id=str(org.id), user_id=member_id, role='member')

    org_id = str(org.id)
    admin_headers = {**auth_header(make_token(admin_id, org_id=org_id)), 'X-Org-Id': org_id}

    created = await client.post(
        '/portal/groups',
        json={'name': f'Reviewers {suffix}', 'description': 'has a hook'},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    group_id = created.json()['id']

    added = await client.post(
        f'/portal/groups/{group_id}/members',
        json={'user_id': member_id},
        headers=admin_headers,
    )
    assert added.status_code in (200, 201), added.text

    # A hook, because the pack's contents are the point: this is the
    # thing that runs through `bash -c` on a developer's machine.
    entry = await client.post(
        f'/portal/groups/{group_id}/entries',
        json={
            'kind': 'hooks',
            'entry_name': 'pre-tool',
            'content': '{"event": "PreToolUse", "command": "echo internal-only"}',
            'content_type': 'json',
        },
        headers=admin_headers,
    )
    assert entry.status_code in (200, 201), entry.text

    return {
        'org_id': org_id,
        'admin_id': admin_id,
        'member_id': member_id,
        'group_id': group_id,
        'admin_headers': admin_headers,
        'member_token': make_token(member_id),
    }


async def _deactivate(client, ctx):
    response = await client.patch(
        f'/portal/team/{ctx["member_id"]}/status',
        json={'status': 'deactivated'},
        headers=ctx['admin_headers'],
    )
    assert response.status_code == 200, response.text


class TestTheControl:
    async def test_an_active_member_gets_the_pack(self, client, member_in_a_group):
        """Otherwise the refusal below could be any other failure."""
        response = await client.get('/portal/me/group/pack', headers=auth_header(member_in_a_group['member_token']))
        assert response.status_code == 200, response.text
        pack = response.json()
        assert pack is not None, 'the fixture produced no pack, so the test below proves nothing'
        assert pack['group_id'] == member_in_a_group['group_id']
        assert any(e['entry_name'] == 'pre-tool' for e in pack['entries']), pack['entries']


class TestADeactivatedMemberGetsNothing:
    async def test_the_pack_stops(self, client, member_in_a_group):
        """The finding.

        The pack carries hook commands and agent prompts. Continuing to
        serve them to somebody who has been deactivated hands the
        customer's internal configuration to a person they removed.
        """
        await _deactivate(client, member_in_a_group)

        response = await client.get('/portal/me/group/pack', headers=auth_header(member_in_a_group['member_token']))

        # 401 now, and this used to be 200-with-null. Deactivation
        # became a fact about the account rather than only about the
        # membership, so the token is refused at the door and the
        # handler is never reached — see
        # `test_somebody_who_left_cannot_get_back_in.py`. A stronger
        # answer to the same question: a deactivated member gets
        # nothing. The null is still accepted so this keeps measuring
        # the handler if the door is ever loosened.
        assert response.status_code == 401 or response.json() is None or response.json().get('no_pack'), (
            "a deactivated member was served their former organisation's group pack, "
            f'including its hook commands: {response.text[:300]}'
        )

    async def test_the_group_summary_stops_too(self, client, member_in_a_group):
        """`/me/group` is the smaller sibling and names the group.

        Less content, same question — and a group name is still the
        customer's information about a team they are no longer on.
        """
        await _deactivate(client, member_in_a_group)

        response = await client.get(
            '/portal/me/group',
            headers={
                **auth_header(member_in_a_group['member_token']),
                'X-Org-Id': member_in_a_group['org_id'],
            },
        )
        # 401 is the strongest form of the same answer; see the note
        # in `test_the_pack_stops`.
        assert response.status_code in (200, 401, 403), response.text
        if response.status_code == 200:
            assert response.json() is None, f'a deactivated member was told their group: {response.text}'

    async def test_reactivating_restores_it(self, client, member_in_a_group):
        """Reversible, like deactivation itself (B1). A one-way door here
        would mean an accidental deactivation cost the person their group
        membership rather than just pausing it."""
        await _deactivate(client, member_in_a_group)
        back = await client.patch(
            f'/portal/team/{member_in_a_group["member_id"]}/status',
            json={'status': 'active'},
            headers=member_in_a_group['admin_headers'],
        )
        assert back.status_code == 200, back.text

        # A *fresh* token. Deactivation withdraws the sessions the
        # person held, and reactivating deliberately does not re-admit
        # them — coming back is not a reason to re-admit the token on
        # the laptop that was the reason they went. The membership is
        # restored, which is what "reversible" meant here; signing in
        # again is the cost.
        # Past the withdrawal watermark deactivation set, which rounds
        # up to the next whole second and treats equal as revoked.
        await anyio.sleep(2.2)
        restored = make_token(member_in_a_group['member_id'])
        response = await client.get('/portal/me/group/pack', headers=auth_header(restored))
        assert response.status_code == 200, response.text
        assert response.json() is not None, 'reactivation did not restore the pack'
        assert response.json()['group_id'] == member_in_a_group['group_id']


class TestTheAdminViewIsUnaffected:
    async def test_an_admin_can_still_see_a_deactivated_members_group(self, client, member_in_a_group):
        """Deliberate. An admin managing their own organisation needs to
        see who is where, including people they have just deactivated —
        that is how they decide whether to reactivate or remove. The
        change is about what the *deactivated person* is served, not
        about hiding their record from the org that holds it.
        """
        await _deactivate(client, member_in_a_group)

        response = await client.get(
            f'/portal/users/{member_in_a_group["member_id"]}/group',
            headers=member_in_a_group['admin_headers'],
        )
        assert response.status_code == 200, response.text
        assert response.json() is not None, "an admin lost sight of a deactivated member's group"
