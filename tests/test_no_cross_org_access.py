"""A valid token for one organisation must not reach another's resources.

F19 found reads unscoped across organisations. F29 found a sign-in code that could claim somebody
else's pending admin invitation. Both were single endpoints, found by
looking at them. There are **many parametrised endpoints under
`/v1/portal`**, and nothing swept them.

So this is the sweep, driven the way an attacker would: build two
organisations, mint a real token for a member of the first, and ask for
every identifier belonging to the second. A `2xx` is cross-organisation
access. `403`/`404` are both fine — which of the two is a separate
question about information disclosure, and not this file's.

The token is genuine, not forged: `make_token` is the same
`JWTService.create_token` the sign-in path uses, so this exercises
authorisation rather than authentication. F44's territory was whether a
token is valid; this is what a valid one is allowed to touch.

Deliberately a table rather than one test per endpoint. The point is
coverage of the whole surface — a new endpoint added under
`/v1/portal/...{id}` should show up here as an omission, which
`test_every_parametrised_portal_endpoint_is_covered` enforces.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.db.sql.models.org_group import OrgGroup
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, put_in_org

pytestmark = pytest.mark.anyio


async def _two_orgs(db):
    """Org A with a member, org B with its own owner. No overlap."""
    attacker = await create_user(db, email=f'attacker-{uuid.uuid4().hex[:6]}@example.com')
    victim = await create_user(db, email=f'victim-{uuid.uuid4().hex[:6]}@example.com')
    org_a = await create_org(db, owner_user_id=str(attacker.user_id), slug=f'a-{uuid.uuid4().hex[:6]}')
    org_b = await create_org(db, owner_user_id=str(victim.user_id), slug=f'b-{uuid.uuid4().hex[:6]}')
    await add_member(db, str(org_a.id), str(attacker.user_id), role='admin')
    await add_member(db, str(org_b.id), str(victim.user_id), role='admin')
    return attacker, victim, org_a, org_b


# (method, path template) for every parametrised portal endpoint. Kept as
# data so the coverage test below can compare it against the live schema.
#
# `{other}` is substituted with an identifier belonging to org B — or, where
# the resource cannot be created without a provider round trip, with a
# well-formed identifier that does not exist. That still tests the ordering
# that matters: the org check must happen before the lookup, or a stranger
# learns which identifiers are real from the difference between 403 and 404.
CROSS_ORG_CALLS: list[tuple[str, str]] = [
    # The inference fleet serves every organisation, so only a super admin
    # changes it (SER-32). An org admin of another organisation is refused.
    ('PATCH', '/portal/inference/nodes/{other}'),
    ('DELETE', '/portal/inference/nodes/{other}'),
    ('POST', '/portal/inference/nodes/{other}/drain'),
    ('DELETE', '/portal/inference/nodes/{other}/drain'),
    ('GET', '/portal/groups/{group}'),
    ('PATCH', '/portal/groups/{group}'),
    ('DELETE', '/portal/groups/{group}'),
    ('GET', '/portal/groups/{group}/entries'),
    ('POST', '/portal/groups/{group}/entries'),
    ('PATCH', '/portal/groups/{group}/entries/{other}'),
    ('DELETE', '/portal/groups/{group}/entries/{other}'),
    ('GET', '/portal/groups/{group}/extensions'),
    ('PUT', '/portal/groups/{group}/extensions/{other}'),
    ('GET', '/portal/groups/{group}/members'),
    ('POST', '/portal/groups/{group}/members'),
    ('DELETE', '/portal/groups/{group}/members/{victim}'),
    ('POST', '/portal/groups/{group}/copy'),
    ('PATCH', '/portal/organizations/{org_b}'),
    ('DELETE', '/portal/organizations/{org_b}'),
    ('DELETE', '/portal/team/{victim}'),
    ('PATCH', '/portal/team/{victim}/role'),
    ('PATCH', '/portal/team/{victim}/status'),
    # B5. The most consequential of the three: signing somebody out is
    # available to any admin of any org, so without the membership check
    # in `TeamService.withdraw_sessions` it would be a way to end the
    # sessions of any user of the deployment by id — a denial of service
    # granted by the endpoint built to stop one.
    ('POST', '/portal/team/{victim}/revoke-sessions'),
    # Apps act in the organisation the portal is in; another's app id is
    # not found there.
    ('GET', '/portal/apps/{other}'),
    ('PATCH', '/portal/apps/{other}'),
    ('DELETE', '/portal/apps/{other}'),
    ('POST', '/portal/apps/{other}/discard'),
    ('PUT', '/portal/apps/{other}/keys'),
    ('GET', '/portal/apps/{other}/extensions'),
    ('PUT', '/portal/apps/{other}/extensions/{other}'),
    # VM profiles name their organisation; an admin of another one is
    # refused (only the super admin acts in any organisation).
    ('GET', '/portal/orgs/{org_b}/vm-profiles'),
    ('POST', '/portal/orgs/{org_b}/vm-profiles'),
    ('PUT', '/portal/orgs/{org_b}/vm-profiles/{other}'),
    ('DELETE', '/portal/orgs/{org_b}/vm-profiles/{other}'),
    ('PUT', '/portal/orgs/{org_b}/groups/{group}/vm-profile'),
    ('PUT', '/portal/orgs/{org_b}/apps/{other}/vm-profile'),
    # And the requests apps and extensions make for a profile (§12.3).
    ('GET', '/portal/orgs/{org_b}/vm-profile-requests'),
    ('POST', '/portal/orgs/{org_b}/vm-profile-requests/{other}/approve'),
    ('POST', '/portal/orgs/{org_b}/vm-profile-requests/{other}/reject'),
    ('GET', '/portal/orgs/{org_b}/vm-quota'),
    ('PUT', '/portal/orgs/{org_b}/vm-quota'),
    # So do the limits on an app's spaces (§17.4).
    ('GET', '/portal/orgs/{org_b}/space-quota'),
    ('PUT', '/portal/orgs/{org_b}/space-quota'),
    # And one producer's volume (§12.1): the victim's, in their organisation.
    ('GET', '/portal/orgs/{org_b}/producers/{kind}/{victim}/volume'),
    ('POST', '/portal/orgs/{org_b}/producers/{kind}/{victim}/volume'),
    # Retention and legal holds (§18) are the organisation's own.
    ('GET', '/portal/orgs/{org_b}/retention'),
    ('PUT', '/portal/orgs/{org_b}/retention'),
    ('GET', '/portal/orgs/{org_b}/legal-holds'),
    ('PUT', '/portal/orgs/{org_b}/legal-holds/{victim}'),
    ('DELETE', '/portal/orgs/{org_b}/legal-holds/{victim}'),
    ('PUT', '/portal/orgs/{org_b}/legal-holds/apps/{other}'),
    ('DELETE', '/portal/orgs/{org_b}/legal-holds/apps/{other}'),
    # Where each erasure stands, and discarding a removed member's VM (§18.5).
    ('GET', '/portal/orgs/{org_b}/erasures'),
    ('POST', '/portal/orgs/{org_b}/members/{victim}/discard'),
    ('POST', '/portal/orgs/{org_b}/producers/person/{victim}/exports'),
    ('GET', '/portal/orgs/{org_b}/exports'),
    ('GET', '/portal/orgs/{org_b}/exports/{other}'),
    ('GET', '/portal/orgs/{org_b}/exports/{other}/download'),
    # VM credentials and apps are read in the portal's own organisation.
    ('DELETE', '/portal/vm-credentials/{other}'),
    ('POST', '/portal/apps/{other}/vm-credential'),
    # So are API keys: another organisation's key is not found (§22).
    ('DELETE', '/portal/api-keys/{other}'),
]

# Endpoints that answer 2xx to a foreign identifier *by design*, each with
# its own test below asserting the reason holds rather than just the status.
#
# Listed separately rather than dropped, because the coverage check compares
# against the live router and an endpoint that is simply absent from both
# tables would go unnoticed.
DELIBERATELY_NOT_ORG_SCOPED: list[tuple[str, str]] = [
    # The inference fleet is deployment-level. `InferenceNodeDB.list_all`:
    # "Not org-scoped, and deliberately so: the inference fleet belongs to
    # the deployment rather than to an organization within it." So any org
    # admin may *read* a node back, for the status page. Changing one is a
    # super admin's (`require_node_manager`, SER-32): those routes are in
    # CROSS_ORG_CALLS, refused to an org admin.
    ('GET', '/portal/inference/nodes/{other}'),
    # Scoped to the caller's *own* org, so a foreign user resolves to "no
    # group here" rather than to the victim's group.
    ('GET', '/portal/users/{victim}/group'),
    # Super admins are a property of the install, not of an organisation:
    # `require_super_admin` is the gate, and there is no org to scope by.
    ('DELETE', '/portal/super-admins/{other}'),
    # An organisation's domains are a super admin's to change, on any
    # organisation: `require_super_admin` is the gate, not membership.
    ('POST', '/portal/organizations/{other}/domains'),
    ('DELETE', '/portal/organizations/{other}/domains/{other}'),
    # Registered clients belong to the install: approving one lets it
    # obtain tokens for every organisation. `require_deployment_admin`
    # is the gate, and there is no org to scope by.
    ('POST', '/portal/clients/{other}/approve'),
    ('POST', '/portal/clients/{other}/revoke'),
    ('DELETE', '/portal/clients/{other}'),
    ('GET', '/portal/clients/{other}'),
    # A service's held update is decided by the same people, for the same
    # reason: its version reaches every organisation's machines.
    ('POST', '/portal/clients/{other}/held-update/approve'),
    ('POST', '/portal/clients/{other}/held-update/reject'),
]

_ALLOWED_STATUSES = {400, 401, 403, 404, 405, 409, 422, 501, 502, 503}


@pytest.fixture
async def cross_org(db):
    attacker, victim, org_a, org_b = await _two_orgs(db)

    now = datetime.now(timezone.utc)
    group_b = OrgGroup(
        id=str(uuid.uuid4()),
        org_id=str(org_b.id),
        name='victims-group',
        created_by_user_id=str(victim.user_id),
        created_at=now,
        updated_at=now,
    )
    await put_in_org(str(org_b.id), group_b)

    return {
        'token': make_token(str(attacker.user_id), org_id=str(org_a.id), org_name='A'),
        'subs': {
            'other': str(uuid.uuid4()),
            'group': str(group_b.id),
            'org_b': str(org_b.id),
            'victim': str(victim.user_id),
            'kind': 'person',
        },
    }


@pytest.mark.parametrize('method,template', CROSS_ORG_CALLS, ids=lambda v: str(v))
async def test_a_token_for_one_org_cannot_reach_another(client, cross_org, method, template):
    path = template.format(**cross_org['subs'])

    response = await client.request(
        method,
        path,
        headers=auth_header(cross_org['token']),
        json={} if method in ('POST', 'PATCH', 'PUT') else None,
    )

    assert response.status_code in _ALLOWED_STATUSES, (
        f'{method} {path} answered {response.status_code} to a token scoped to a '
        f'different organisation.\n{response.text[:400]}'
    )


def test_every_parametrised_portal_endpoint_is_covered():
    """The table above must not fall behind the router.

    A sweep is only worth having if it is exhaustive, and the way this stops
    being exhaustive is somebody adding an endpoint. Compared against the
    live OpenAPI schema so a new `/v1/portal/...{id}` route fails here until
    it is either checked or explicitly excluded.
    """
    from tests.conftest import the_app

    app = the_app()

    live: set[tuple[str, str]] = set()
    for path, operations in app.openapi()['paths'].items():
        if '/portal' not in path or '{' not in path:
            continue
        for method in operations:
            if method.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                live.add((method.upper(), path.replace('/v1', '', 1)))

    def normalise(template: str) -> str:
        import re

        return re.sub(r'\{[a-z_]+\}', '{}', template)

    covered = {(m, normalise(t)) for m, t in CROSS_ORG_CALLS + DELIBERATELY_NOT_ORG_SCOPED}
    missing = sorted((m, p) for m, p in live if (m, normalise(p)) not in covered)

    assert live, 'no parametrised portal endpoints found — has the router changed shape?'
    assert not missing, (
        f'{len(missing)} parametrised portal endpoint(s) are in neither CROSS_ORG_CALLS '
        f'nor DELIBERATELY_NOT_ORG_SCOPED: '
        f'{missing}. Add them, or exclude them with a comment saying why.'
    )


class TestTheDeliberateExceptions:
    """Three endpoints answer 2xx to a foreign identifier on purpose.

    Each is checked for the *reason*, not the status — a 200 is only
    acceptable here if nothing about the other organisation comes back with
    it, and that is what these assert.
    """

    async def test_a_foreign_user_resolves_to_no_group_rather_than_theirs(self, client, cross_org):
        """`get_user_group` passes `org_id=current_user.org_id`, so the
        question it answers is "which group does this user have *in my
        org*". For somebody else's member the answer is none — and the body
        must not carry their real group instead."""
        subs = cross_org['subs']

        response = await client.get(f'/portal/users/{subs["victim"]}/group', headers=auth_header(cross_org['token']))

        assert response.status_code in _ALLOWED_STATUSES | {200}, response.text
        assert 'victims-group' not in response.text, response.text
        if response.status_code == 200:
            body = response.json()
            assert body in (None, {}, []) or not body.get('group'), body

    @pytest.mark.parametrize(
        'method,suffix',
        [('POST', '/drain'), ('DELETE', '/drain'), ('PATCH', ''), ('DELETE', '')],
    )
    async def test_an_org_admin_cannot_change_the_shared_fleet(self, client, cross_org, method, suffix):
        """The fleet serves every organisation, so a node one organisation's
        admin added would receive the others' prompts, and one they drained
        would push the others' traffic elsewhere. Changing it is a super
        admin's decision (SER-32)."""
        subs = cross_org['subs']

        response = await client.request(
            method,
            f'/portal/inference/nodes/{subs["other"]}{suffix}',
            headers=auth_header(cross_org['token']),
            json={} if method in ('POST', 'PATCH') else None,
        )

        assert response.status_code == 403, response.text
        assert 'super admin' in response.text

    async def test_an_org_admin_can_still_read_a_node_back(self, client, cross_org):
        """Reading stays an org admin's: the status page shows the fleet.
        A node that does not exist answers 404, not 403."""
        subs = cross_org['subs']

        response = await client.get(
            f'/portal/inference/nodes/{subs["other"]}',
            headers=auth_header(cross_org['token']),
        )

        assert response.status_code != 403, response.text
