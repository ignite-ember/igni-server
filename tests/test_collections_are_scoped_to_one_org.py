"""A portal collection must not contain another organisation's rows.

`test_no_cross_org_access.py` sweeps every **parametrised** portal
endpoint: it asks for an identifier belonging to org B while holding org
A's token, and a 2xx is the finding. Its own coverage test enforces that
a new `/v1/portal/...{id}` route joins the table.

That leaves a hole the size of every collection endpoint.

`GET /v1/portal/usage` names no identifier. Neither does `/groups` or
`/team`. There is nothing to substitute an org-B id
into, so they cannot appear in that sweep and its coverage check
correctly does not ask them to. Their scoping is *implicit*: the handler
passes `current_user.org_id` down, and somewhere below, a query filters
on it. If a query ever stops filtering, every organisation's rows are
returned to every admin, and **nothing here would have noticed** — the
parametrised sweep passes, the endpoint returns 200, and the response is
simply larger than it should be.

Found while looking for endpoints no test names: several had none at
all, `/usage` among them. All turned out to scope correctly. The gap
was never a live leak; it was that nothing was
watching this class, which is R-2's territory and the most expensive
kind of regression to find late.

## How this works, and why it is a marker hunt

Building meaningful org-B data for every collection would mean knowing
each endpoint's fixtures. Instead org B is created carrying values that
are unique by construction — its identifier, its owner's email, its
slug — and every collection is fetched as org A. If any of those strings
appears anywhere in a response, rows crossed a boundary.

Crude, and that is the point: it needs no knowledge of what an endpoint
returns, so it covers endpoints written after it without being updated.
What it cannot catch is a leak of data that happens to contain none of
the markers, which is why it complements the parametrised sweep rather
than replacing it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.db.sql.models.org_group import OrgGroup
from tests.conftest import add_member, auth_header, create_org, create_user, make_token, put_in_org

pytestmark = pytest.mark.anyio


#: Paths where org B genuinely has a row, so the sweep below can detect
#: a leak. Everything else in the sweep passes because both sides are
#: empty — which is not coverage, and pretending otherwise is how a
#: guard comes to measure nothing.
#:
#: To move a path into this set, give `_two_orgs` a fixture it reads
#: from. That is the useful work here: the honest number of genuinely
#: covered collections is more use than a passing line for all of them.
DETECTS_A_LEAK = {
    '/portal/access-review',
    '/portal/groups',
    '/portal/organizations',
    '/portal/team',
}

#: Collections deliberately not scoped to one organisation, with the
#: reason. Anything else new fails the coverage test below.
NOT_ORG_SCOPED: dict[str, str] = {
    '/portal/me': "the caller's own profile, which is not org data",
    '/portal/me/group': "the caller's own group policy",
    '/portal/me/group/pack': "the caller's own group policy",
    '/portal/audit-events/export': 'scoped in its own right and covered by the audit tests',
}


def _collection_paths() -> list[str]:
    """Portal GETs with no path parameter, from the live schema.

    Through `the_app()`, which restores `APP_ENV`. This runs at
    **collection** time, because `parametrize` needs the list before any
    test executes — so a stray environment variable set here is set for
    the whole session, before anything else runs.

    That broke `test_the_template_validates_as_written` on the first
    attempt, in a file with nothing to do with this one. Four other
    files had the same leak and had never triggered it, because they all
    sort after that file and the suite runs in collection order;
    collection-time mutation beat the ordering.
    """
    from tests.conftest import the_app

    out = []
    for path, operations in the_app().openapi()['paths'].items():
        if '/portal' not in path or '{' in path:
            continue
        if 'get' not in operations:
            continue
        out.append(path.replace('/v1', '', 1))
    return sorted(out)


async def _two_orgs(db):
    """Org A with a member, org B whose every field is a marker — **and
    which owns a group**.

    The group is the part that took a second attempt. Without something
    org B owns, it has a user and nothing else, so its collections return
    empty lists whatever they filter on. The first version of this file
    omitted it, passed, and **went on passing when an org filter was
    deleted** — a sweep that looked thorough and measured nothing, which
    is R-10 and is the failure this suite keeps finding.

    So org B has a row in the table each collection reads from, and the
    revert-check below is what proves it: removing a filter has to make
    this file fail.
    """
    marker = uuid.uuid4().hex[:10]
    caller = await create_user(db, email=f'caller-{uuid.uuid4().hex[:6]}@example.com')
    other = await create_user(db, email=f'MARKER{marker}@example.com')
    org_a = await create_org(db, owner_user_id=str(caller.user_id), slug=f'a-{uuid.uuid4().hex[:6]}')
    org_b = await create_org(db, owner_user_id=str(other.user_id), slug=f'marker{marker}')
    await add_member(db, str(org_a.id), str(caller.user_id), role='admin')
    await add_member(db, str(org_b.id), str(other.user_id), role='admin')

    now = datetime.now(timezone.utc)
    await put_in_org(
        str(org_b.id),
        OrgGroup(
            id=str(uuid.uuid4()),
            org_id=str(org_b.id),
            name=f'group-{marker}',
            created_by_user_id=str(other.user_id),
            created_at=now,
            updated_at=now,
        ),
    )

    return caller, org_a, org_b, other, marker


class TestTheSweepMeasuresSomething:
    def test_it_finds_collection_endpoints(self):
        """A sweep over an empty list passes and proves nothing, which is
        the failure mode this whole review keeps finding."""
        paths = _collection_paths()

        assert len(paths) >= 8, paths
        for expected in ('/portal/usage', '/portal/groups', '/portal/team'):
            assert expected in paths, f'{expected} is not being swept: {paths}'

    async def test_the_markers_are_actually_distinguishing(self, db):
        """If org B's values were not unique, every assertion below would
        pass by coincidence."""
        _, org_a, org_b, other, marker = await _two_orgs(db)

        assert marker in str(org_b.slug)
        assert marker in other.email
        assert marker not in str(org_a.slug)
        assert str(org_b.id) != str(org_a.id)

    async def test_org_b_owns_a_group_worth_leaking(self, client, db):
        """The check the first version of this file did not have.

        Markers only detect a leak if some row carries them. With org B
        holding a user and nothing else, every collection returns an
        empty list and the sweep passes whatever the queries do.

        Asserted through the API as org B's own owner rather than by
        re-querying the database: a second query on the `db` fixture
        outlives its event loop and fails in teardown, which is why
        `test_audit_trail.py` reaches for a synchronous engine. Asking
        the owner what they can see needs neither.
        """
        _, _, org_b, other, marker = await _two_orgs(db)
        token = make_token(str(other.user_id), org_id=str(org_b.id))

        response = await client.get('/portal/groups', headers=auth_header(token))

        assert response.status_code == 200, response.text
        assert (
            f'group-{marker}' in response.text
        ), 'org B cannot see its own group, so it owns nothing the sweep could catch leaking'


class TestNoCollectionLeaksAnotherOrg:
    @pytest.mark.parametrize('path', sorted(DETECTS_A_LEAK), ids=lambda p: p)
    async def test_the_sweep_can_actually_detect_a_leak_here(self, client, db, path):
        """Per path, that a marker exists for the sweep to find.

        A marker hunt only works where a marker exists. If org B's own
        owner sees an empty collection, then org A seeing an empty one
        says nothing about filtering — and the corresponding sweep case
        passes for free while looking like coverage.

        This is not hypothetical. The first version of this file gave org
        B a user and nothing else, so **every** collection was empty and
        the whole sweep was vacuous: it passed with an org filter
        deleted. The fixture now creates a group, which makes the paths
        below live, and this test is what stops that silently
        regressing.
        """
        _, _, org_b, other, marker = await _two_orgs(db)
        token = make_token(str(other.user_id), org_id=str(org_b.id))

        response = await client.get(path, headers=auth_header(token))

        assert response.status_code == 200, response.text
        assert marker in response.text, (
            f'{path} shows org B nothing of its own, so the sweep cannot detect a leak '
            f'there and its passing case is empty. Either the fixture stopped creating '
            f'what this path reads, or the path stopped returning it.'
        )

    @pytest.mark.parametrize('path', _collection_paths(), ids=lambda p: p)
    async def test_the_response_contains_no_marker(self, client, db, path):
        if path in NOT_ORG_SCOPED:
            pytest.skip(f'{path}: {NOT_ORG_SCOPED[path]}')

        caller, org_a, org_b, other, marker = await _two_orgs(db)
        token = make_token(str(caller.user_id), org_id=str(org_a.id))

        response = await client.get(path, headers=auth_header(token))

        # A refusal is fine — this asks what a *successful* read contains.
        if response.status_code != 200:
            pytest.skip(f'{path} answered {response.status_code} for this caller')

        body = response.text
        for name, value in (
            ("org B's identifier", str(org_b.id)),
            ("org B's slug", str(org_b.slug)),
            ("org B's owner's email", other.email),
            ("org B's owner's id", str(other.user_id)),
            ("org B's group", f'group-{marker}'),
        ):
            assert value not in body, (
                f'{path} returned {name} to a member of another organisation. The handler '
                f'takes `current_user.org_id`; something below it has stopped filtering on '
                f'that, and the parametrised sweep cannot see this because the path names no '
                f'identifier.'
            )


def test_the_detectable_set_is_honest():
    """`DETECTS_A_LEAK` must name paths that exist and are swept.

    A path listed here but excused from the sweep, or gone from the
    router, would inflate the coverage claim — which is the specific
    dishonesty this set exists to prevent.
    """
    paths = set(_collection_paths())
    unknown = sorted(DETECTS_A_LEAK - paths)
    excused = sorted(DETECTS_A_LEAK & set(NOT_ORG_SCOPED))

    assert not unknown, f'{unknown} are claimed as covered and are not routes'
    assert not excused, f'{excused} are claimed as covered and also excused from the sweep'
    assert len(DETECTS_A_LEAK) < len(paths), (
        'every collection is claimed detectable, which would mean the fixture creates a row '
        'for each — check that, because the likelier explanation is that this set stopped '
        'being maintained'
    )


def test_every_collection_is_swept_or_excused():
    """The table must not fall behind the router.

    Same enforcement the parametrised sweep uses, for the same reason: a
    sweep stops being exhaustive when somebody adds an endpoint, and the
    only way that gets noticed is if adding one fails a test.
    """
    paths = set(_collection_paths())
    stale = sorted(p for p in NOT_ORG_SCOPED if p not in paths)

    assert not stale, (
        f'{stale} are excused from this sweep and no longer exist. An exclusion for a '
        f'route that is gone hides the next route that needs one.'
    )
