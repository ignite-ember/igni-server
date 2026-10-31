"""An agent that loads under the wrong name loads as nobody.

The loader keys agents on the ``name:`` in their frontmatter, not the
entry name they are stored under. Two entries declaring the same name are
one agent by the time a session sees them, and the loser disappears
silently.

Found live during the production check: the demo group shipped three
agents — ``coder``, ``designer``, ``party-maker`` — all still carrying
``name: <entry-name>`` from the portal's template. The session loaded one
agent, called ``<entry-name>``. Nothing anywhere said so; the group's
entry list showed three.
"""

from __future__ import annotations

import pytest

from app.services.portal.agent_names import frontmatter_name, PLACEHOLDER_NAME, warning_for
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


def _agent(name: str | None = 'reviewer', *, body: str = 'Review carefully.') -> str:
    name_line = f'name: {name}\n' if name is not None else ''
    return f'---\n{name_line}description: does things\n---\n\n{body}\n'


class TestReadingTheName:
    def test_it_reads_the_frontmatter_name(self):
        assert frontmatter_name(_agent('reviewer')) == 'reviewer'

    @pytest.mark.parametrize(
        'content',
        [
            'no frontmatter at all',
            '',
            '---\ndescription: no name key\n---\nbody',
            '---\nname:\n---\nbody',
            '---\nname: "   "\n---\nbody',
            '---\n[this: is: not: yaml\n---\nbody',
            '---\n- a list, not a mapping\n---\nbody',
        ],
    )
    def test_shapes_that_declare_no_name(self, content: str):
        """All of these mean the same thing to a caller — nothing to
        compare — so they answer the same way rather than being told
        apart for no benefit."""
        assert frontmatter_name(content) is None


class TestThePlaceholder:
    def test_the_template_default_is_flagged(self):
        warning = warning_for(_agent(PLACEHOLDER_NAME))

        assert warning is not None
        assert PLACEHOLDER_NAME in warning
        # The consequence, not just the observation: an admin needs to
        # know the other agents vanish, or this reads as pedantry.
        assert 'silently dropped' in warning
        assert 'Change it to' in warning

    def test_a_real_name_is_quiet(self):
        assert warning_for(_agent('reviewer')) is None


class TestCollisions:
    def test_a_duplicate_name_is_flagged(self):
        warning = warning_for(_agent('reviewer'), other_names={'reviewer', 'other'})

        assert warning is not None
        assert 'reviewer' in warning
        assert 'drops the other' in warning

    def test_a_unique_name_against_others_is_quiet(self):
        assert warning_for(_agent('reviewer'), other_names={'planner', 'explorer'}) is None

    def test_no_other_names_means_placeholder_only(self):
        """Called without the group's other names, it can still catch the
        placeholder — the check must not depend on a lookup succeeding."""
        assert warning_for(_agent(PLACEHOLDER_NAME)) is not None
        assert warning_for(_agent('reviewer')) is None


class TestAMissingName:
    def test_it_says_the_agent_is_skipped_entirely(self):
        """Different outcome from a collision, so a different sentence: a
        nameless definition is not loaded at all rather than losing a
        race with another entry."""
        warning = warning_for(_agent(None))

        assert warning is not None
        assert 'no `name:`' in warning
        assert 'skip it entirely' in warning


class TestItIsAdvisory:
    def test_nothing_here_raises(self):
        """Matching agent_tools and hook_scripts. Entries are saved one
        at a time, so refusing on a collision would make the order
        somebody creates them in matter, and an admin mid-edit should be
        told rather than blocked."""
        for content in ('', 'garbage', _agent(PLACEHOLDER_NAME), _agent(None), _agent('x')):
            result = warning_for(content, other_names={'x'})
            assert result is None or isinstance(result, str)


class TestThroughTheApiItDoesNotCollideWithItself:
    """The bug the first version of this check shipped with.

    Both endpoints save the entry and *then* ask for warnings, so the
    entry being written is already in the group's list. Comparing against
    that list unfiltered meant every correctly-named agent was told
    another agent had claimed its name — a false positive on every single
    save, create and edit alike.

    Caught by an existing test rather than a new one, which is the
    argument for running the whole suite after adding a check like this.
    """

    @pytest.fixture
    async def admin_headers(self, db):
        await create_user(db, user_id='admin-1', email='admin@test.com')
        org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
        await add_member(db, org_id=str(org.id), user_id='admin-1', role='admin')
        return auth_header(make_token('admin-1', org_id=str(org.id)))

    @pytest.fixture
    async def group(self, client, admin_headers):
        resp = await client.post('/portal/groups', json={'name': 'Eng'}, headers=admin_headers)
        return resp.json()['id']

    async def test_creating_a_uniquely_named_agent_warns_about_nothing(self, client, admin_headers, group):
        resp = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'reviewer',
                'content': _agent('reviewer'),
                'content_type': 'markdown',
            },
            headers=admin_headers,
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()['warnings'] == []

    async def test_editing_it_again_still_warns_about_nothing(self, client, admin_headers, group):
        """The edit path reads the stored entry to learn its kind, so it
        had a second chance to include itself."""
        created = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'reviewer',
                'content': _agent('reviewer'),
                'content_type': 'markdown',
            },
            headers=admin_headers,
        )
        entry_id = next(e['id'] for e in created.json()['entries'] if e['entry_name'] == 'reviewer')

        resp = await client.patch(
            f'/portal/groups/{group}/entries/{entry_id}',
            json={'content': _agent('reviewer', body='Review even more carefully.')},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()['warnings'] == []

    async def test_a_genuine_collision_between_two_entries_is_still_caught(self, client, admin_headers, group):
        """The exclusion must not blunt the check it guards: two
        *different* entries declaring one name is the real problem."""
        for entry_name in ('reviewer', 'second-reviewer'):
            resp = await client.post(
                f'/portal/groups/{group}/entries',
                json={
                    'kind': 'agents',
                    # Both declare `name: reviewer` — different entries,
                    # one loaded agent.
                    'entry_name': entry_name,
                    'content': _agent('reviewer'),
                    'content_type': 'markdown',
                },
                headers=admin_headers,
            )

        warnings = resp.json()['warnings']
        assert any('already declares' in w for w in warnings), warnings
        # And it saved anyway — advisory, not a refusal.
        assert len(resp.json()['entries']) == 2
