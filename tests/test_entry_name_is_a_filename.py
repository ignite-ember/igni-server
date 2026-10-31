"""An entry name becomes a filename on every developer's machine.

The client materialises a group's pack to
``~/.igni/group-policy/<kind>/<entry_name><ext>``, and ``Path`` accepts a
separator without complaint. So an entry named
``../../../../../../tmp/PWNED`` wrote ``/tmp/PWNED.md`` — outside the
cache, on every machine that synced the pack. Verified against the real
client before the fix: ``agents`` and ``scripts`` both escaped.

``scripts`` is the one that matters most. Those files are written mode
0700, so the primitive is an executable dropped anywhere the user can
write — ``~/.zshrc``, a directory already on ``PATH`` — on every machine
in the org, from one form in the portal.

It needs an org admin, so it is not a way in. It is an amplifier: one
request, N machines, and a compromised admin token or a hostile server
reaches every client at once.

Fixed on both sides. The client refuses these itself because it must —
it can be pointed at a server somebody else runs, and "the server checks
it" is not a property the client can verify. The server refuses them too,
because a name that cannot be materialised has no business being stored,
and the portal is where an admin finds out while still looking at the
form.
"""

from __future__ import annotations

import pytest

from tests.conftest import add_member, auth_header, create_org, create_user, make_token

TRAVERSALS = [
    '../../../../../../tmp/PWNED',
    '../escape',
    '/tmp/PWNED',
    '..',
    '.',
    '...',
    'a/b/c',
    '~/.ssh/authorized_keys',
    '..\\..\\evil',
    'ok\x00.md',
    'has space',
    'semi;colon',
]

ACCEPTED = ['reviewer', 'pre-pr-review', 'a.b', 'a_b-c.d', '.mcp', 'UPPER123']


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='a1', email='a@a.com')
    org = await create_org(db, owner_user_id='a1', slug='o', display_name='O')
    await add_member(db, org_id=str(org.id), user_id='a1', role='admin')
    return auth_header(make_token('a1', org_id=str(org.id)))


@pytest.fixture
async def group(client, admin):
    return (await client.post('/portal/groups', json={'name': 'G'}, headers=admin)).json()['id']


class TestTheServerRefusesThem:
    @pytest.mark.parametrize('entry_name', TRAVERSALS)
    async def test_a_path_shaped_name_is_422(self, client, admin, group, entry_name):
        response = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': entry_name,
                'content': '---\nname: x\ndescription: d\n---\nbody',
                'content_type': 'markdown',
            },
            headers=admin,
        )

        assert response.status_code == 422, f'{entry_name!r} was accepted'

    @pytest.mark.parametrize('entry_name', ACCEPTED)
    async def test_ordinary_names_still_work(self, client, admin, group, entry_name):
        """The names real groups use, including a leading dot — a pattern
        that rejected those would be the bug instead of the fix."""
        response = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': entry_name,
                'content': '---\nname: x\ndescription: d\n---\nbody',
                'content_type': 'markdown',
            },
            headers=admin,
        )

        assert response.status_code == 201, response.text

    async def test_the_scripts_kind_is_covered_too(self, client, admin, group):
        """Called out because its files are executable."""
        response = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'scripts',
                'entry_name': '../../../../../../tmp/PWNED',
                'content': '#!/bin/sh\necho hi',
                'content_type': 'shell',
            },
            headers=admin,
        )

        assert response.status_code == 422

    async def test_the_seeded_names_all_pass_the_pattern(self):
        """The default every deployment starts from must not be refused
        by the rule protecting it."""
        import re

        from app.api.base_v1.schema.portal.groups import ENTRY_NAME_PATTERN
        from app.services.portal.default_group import seed_entries

        for _kind, name, _content in seed_entries():
            assert re.match(ENTRY_NAME_PATTERN, name), f'seeded entry name rejected: {name!r}'
