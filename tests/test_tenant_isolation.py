"""One org must not reach another's data, on any route that takes an id.

Found during the production readiness check by building two complete orgs
and driving every id-taking route with the wrong org's token. Most were
already scoped and answered 404. These were not:

* ``GET /portal/groups/{id}/members`` returned another org's membership —
  ``user_id``, ``email``, ``full_name``, who added them and when.
* ``GET /portal/users/{user_id}/group`` returned another org's group id,
  ``org_id`` and name from nothing but a user id, which is enough to
  enumerate other tenants.

The shape is the same every time, and it is why a review by reading finds
it slowly: ``require_admin`` and ``get_current_portal_user`` prove the
caller is an administrator or a member *of some org*. Turning that into
"of this org" is the org argument, and a handler that accepts
``current_user`` and never reads it looks identical to one that does.

``_group_in_org``'s own docstring records this being fixed once before,
for the entry endpoints. These tests exist so the next route added under
an id does not repeat it a third time.
"""

from __future__ import annotations

import pytest

from tests.conftest import add_member, auth_header, create_org, create_user, make_token

AGENT = '---\nname: b-only\ndescription: d\n---\nbody'


@pytest.fixture
async def orgs(db):
    """Two orgs, each with its own admin."""
    await create_user(db, user_id='alice', email='alice@a.com')
    await create_user(db, user_id='bob', email='bob@b.com')
    await create_user(db, user_id='carol', email='carol@b.com')
    a = await create_org(db, owner_user_id='alice', slug='org-a', display_name='Org A')
    b = await create_org(db, owner_user_id='bob', slug='org-b', display_name='Org B')
    await add_member(db, org_id=str(a.id), user_id='alice', role='admin')
    await add_member(db, org_id=str(b.id), user_id='bob', role='admin')
    await add_member(db, org_id=str(b.id), user_id='carol', role='member')
    return {
        'a': {'org': str(a.id), 'headers': auth_header(make_token('alice', org_id=str(a.id)))},
        'b': {'org': str(b.id), 'headers': auth_header(make_token('bob', org_id=str(b.id)))},
    }


@pytest.fixture
async def group_b(client, orgs):
    """A group in org B with a real member and a real entry.

    The member matters: before the fix, ``/members`` answered 200 with an
    empty list for an org that had none, which reads exactly like a
    scoped 200 and would have let a weaker test pass.
    """
    created = await client.post('/portal/groups', json={'name': 'B Secrets'}, headers=orgs['b']['headers'])
    group_id = created.json()['id']
    await client.post(
        f'/portal/groups/{group_id}/entries',
        json={'kind': 'agents', 'entry_name': 'b-only', 'content': AGENT, 'content_type': 'markdown'},
        headers=orgs['b']['headers'],
    )
    await client.post(f'/portal/groups/{group_id}/members', json={'user_id': 'carol'}, headers=orgs['b']['headers'])
    return group_id


class TestGroupsAreNotReachableAcrossOrgs:
    @pytest.mark.parametrize(
        ('method', 'suffix', 'body'),
        [
            ('GET', '', None),
            ('PATCH', '', {'name': 'pwned'}),
            ('GET', '/entries', None),
            ('GET', '/members', None),
            ('POST', '/members', {'user_id': 'alice'}),
            ('POST', '/copy', {'name': 'Mine now'}),
            ('DELETE', '', None),
        ],
    )
    async def test_org_a_gets_404(self, client, orgs, group_b, method, suffix, body):
        kwargs = {'headers': orgs['a']['headers']}
        if body is not None:
            kwargs['json'] = body

        response = await client.request(method, f'/portal/groups/{group_b}{suffix}', **kwargs)

        assert response.status_code == 404, f'{method} {suffix} leaked: {response.text[:200]}'

    async def test_the_members_route_specifically(self, client, orgs, group_b):
        """Called out because it was the one that leaked, and what it
        leaked was people: emails and names, not just ids."""
        response = await client.get(f'/portal/groups/{group_b}/members', headers=orgs['a']['headers'])

        assert response.status_code == 404
        assert 'carol@b.com' not in response.text

    async def test_the_owner_still_sees_its_own_members(self, client, orgs, group_b):
        """The control. A fix that 404s everybody is not a fix."""
        response = await client.get(f'/portal/groups/{group_b}/members', headers=orgs['b']['headers'])

        assert response.status_code == 200
        assert response.json()['total'] == 1
        assert response.json()['members'][0]['email'] == 'carol@b.com'


class TestAUsersGroupIsNotReadableAcrossOrgs:
    async def test_org_a_learns_nothing(self, client, orgs, group_b):
        response = await client.get('/portal/users/carol/group', headers=orgs['a']['headers'])

        # None rather than 404: the route's shape is "no group", and that
        # is the honest answer to give somebody not entitled to know the
        # difference between "no group" and "not your org".
        assert response.status_code == 200
        assert response.json() is None

    async def test_the_owner_does(self, client, orgs, group_b):
        response = await client.get('/portal/users/carol/group', headers=orgs['b']['headers'])

        assert response.status_code == 200
        assert response.json()['id'] == group_b

    async def test_a_member_can_still_read_their_own(self, client, orgs, group_b, db):
        """``/me/group`` asks about the caller, so it must keep working —
        it passes the caller's own org, but it does pass one."""
        carol = auth_header(make_token('carol', org_id=orgs['b']['org']))

        response = await client.get('/portal/me/group', headers=carol)

        assert response.status_code == 200
        assert response.json()['id'] == group_b


class TestTheAuditItself:
    def test_every_id_route_names_an_org_or_a_stronger_gate(self):
        """A standing sweep, so a new route under an id has to decide.

        Source inspection, which cannot prove a route is safe — the
        holes above both *accepted* ``current_user`` and never read it. It
        can prove a route never mentions scoping at all, which is the
        cheap half, and the allowlist forces a reviewer to say why each
        exemption is one.

        ``current_user=current_user`` counts, because handing the whole
        caller to a service that scopes on ``current_user.org_id`` is how
        the team endpoints do it — and behaviourally they are scoped,
        which the probe that found the holes confirmed. Passing the
        caller and ignoring it is the case this cannot see; the tests
        above are what cover that.
        """
        import inspect
        import re

        from app.main import app

        # Routes that legitimately have no org: they authenticate by a
        # different means, or they are the org endpoint itself.
        allowed = {
            'deregister_node',
            'heartbeat_node',
            'remove_node',
            'edit_node',
        }

        offenders = []
        for route in app.routes:
            path = getattr(route, 'path', '')
            fn = getattr(route, 'endpoint', None)
            if not re.search(r'\{[a-z_]*id\}', path) or fn is None:
                continue
            if fn.__name__ in allowed:
                continue
            try:
                source = inspect.getsource(fn)
            except OSError:  # pragma: no cover — defensive
                continue
            scopes = re.search(
                r'org_id|_in_org|current_user=current_user|super_admin|deployment_admin|node_token|require_service',
                source,
            )
            if not scopes:
                offenders.append(f'{sorted(route.methods)[0]} {path} ({fn.__name__})')

        assert not offenders, (
            'these routes take a resource id and never mention an org scope. '
            f'Add one, or add the name to `allowed` with a reason: {offenders}'
        )
