"""An entry's content is bounded, because it does not just sit in Postgres.

``content`` was the one field on the entry requests with no ``max_length``
— ``entry_name``, ``source_url``, ``source_ref`` and ``source_subdir`` all
had one. A 32 MB entry was accepted with 201, and an entry is served in
the group pack to every client and written to disk on every developer's
machine: one request, N machines.

Not an unauthenticated hole — creating an entry needs an org admin — so
this is robustness and blast radius rather than a way in. The ceiling is
set from what real entries weigh: the largest the default seed ships is
an agent prompt just under 20 KB, and all 29 together come to 262 KB.
"""

from __future__ import annotations

import pytest

from app.api.base_v1.schema.portal.group_entries import MAX_ENTRY_CONTENT_BYTES
from tests.conftest import add_member, auth_header, create_org, create_user, make_token


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='a1', email='a@a.com')
    org = await create_org(db, owner_user_id='a1', slug='o', display_name='O')
    await add_member(db, org_id=str(org.id), user_id='a1', role='admin')
    return auth_header(make_token('a1', org_id=str(org.id)))


@pytest.fixture
async def group(client, admin):
    return (await client.post('/portal/groups', json={'name': 'G'}, headers=admin)).json()['id']


def _agent(size: int) -> str:
    return '---\nname: big\ndescription: d\n---\n' + ('x' * size)


class TestCreate:
    @pytest.mark.parametrize('size', [1024, 20 * 1024, MAX_ENTRY_CONTENT_BYTES - 1024])
    async def test_realistic_sizes_are_accepted(self, client, admin, group, size):
        """The ceiling has to clear real content by a wide margin, or it
        becomes the bug instead of the fix."""
        response = await client.post(
            f'/portal/groups/{group}/entries',
            json={'kind': 'agents', 'entry_name': f'e{size}', 'content': _agent(size), 'content_type': 'markdown'},
            headers=admin,
        )

        assert response.status_code == 201, response.text

    # 4 MB rather than 32 MB. 32 MB is now exactly
    # ``settings.max_request_body_bytes``, so ``BodySizeLimitMiddleware``
    # refuses the request with 413 before this endpoint's own bound is
    # consulted — which is correct behaviour but tests the wrong limit.
    # 4 MB is 16x over the entry bound and well under the body one, so it
    # still exercises what this file is about.
    @pytest.mark.parametrize('size', [MAX_ENTRY_CONTENT_BYTES + 1024, 4 * 1024 * 1024])
    async def test_oversized_is_refused(self, client, admin, group, size):
        response = await client.post(
            f'/portal/groups/{group}/entries',
            json={'kind': 'agents', 'entry_name': f'e{size}', 'content': _agent(size), 'content_type': 'markdown'},
            headers=admin,
        )

        assert response.status_code == 422


class TestUpdate:
    async def test_the_edit_path_is_bounded_too(self, client, admin, group):
        """Otherwise the limit is a formality: create small, then grow."""
        created = await client.post(
            f'/portal/groups/{group}/entries',
            json={'kind': 'agents', 'entry_name': 'small', 'content': _agent(512), 'content_type': 'markdown'},
            headers=admin,
        )
        entry_id = next(e['id'] for e in created.json()['entries'] if e['entry_name'] == 'small')

        response = await client.patch(
            f'/portal/groups/{group}/entries/{entry_id}',
            json={'content': _agent(4 * 1024 * 1024)},  # see the note above on 4 MB vs 32 MB
            headers=admin,
        )

        assert response.status_code == 422


class TestTheCeilingItself:
    def test_it_clears_the_default_seed_comfortably(self):
        """Pinned against the seed rather than a number in a comment, so
        an entry that grows past the limit fails here rather than on
        somebody's deployment."""
        from app.services.portal.default_group_seed import BUNDLED_SEED

        largest = max(len(entry.content) for entry in BUNDLED_SEED.entries())

        assert largest < MAX_ENTRY_CONTENT_BYTES
        # And by a real margin, not by a byte.
        assert largest * 4 < MAX_ENTRY_CONTENT_BYTES

    def test_every_string_field_on_the_request_is_bounded(self):
        """The sweep that would have caught this.

        ``content`` was the only field with no bound of any kind, among
        fields that otherwise all had one. The three exemptions are
        constrained by an allowlist in the service layer instead, which is
        stronger than a length — and each was verified by probing the
        endpoint rather than by reading it:

        * ``kind`` — ``VALID_KINDS``; ``'../../../.ssh'`` answers 422.
        * ``content_type`` — ``VALID_CONTENT_TYPES``.
        * ``model`` — ``ServedModels.refuse_unserved`` rejects a name no node in
          the chat pool answers to.

        A new string field here has to pick one: a length, a pattern, or a
        line below saying which check covers it.
        """
        from app.api.base_v1.schema.portal.group_entries import CreateEntryRequest

        allowlisted = {'kind', 'content_type', 'model'}

        unbounded = []
        for name, field in CreateEntryRequest.model_fields.items():
            if name in allowlisted or 'str' not in str(field.annotation):
                continue
            bounded = any(getattr(m, 'max_length', None) or getattr(m, 'pattern', None) for m in field.metadata)
            if not bounded:
                unbounded.append(name)

        assert not unbounded, (
            f'unbounded string fields on entry creation: {unbounded}. Give each a '
            f'max_length or pattern, or allowlist it naming the service check that '
            f'constrains it.'
        )
