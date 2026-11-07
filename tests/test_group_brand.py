"""A group wearing its own brand.

The case: an org puts its teams on its own adapters and agents, and then
every window still says igni in igni's orange. A group can now carry a
few brand tokens — accent colours, a wordmark, a logo — which ride the
pack the client already polls and land on the CSS custom properties the
client's stylesheet already defines.

The rules that matter, in order of how much damage getting them wrong
would do:

* **A colour is a colour.** These values become CSS custom properties in
  every client the group's people run. A value that can carry syntax can
  deface the product for a whole team, so the grammar is hex and nothing
  else — checked here, and again in the client, which may be pointed at
  a server it does not own.
* **A brand mark is inline image bytes, not a URL.** A remote URL would
  be a per-user beacon to whoever set it, and the desktop client's CSP
  would refuse to load it anyway.
* **It has a size.** The pack is polled by every running session, so an
  unbounded logo is an unbounded poll.
* **Unset means igni's.** A group that sets an accent and nothing else
  must not blank out the rest of the palette, and clearing has to be
  expressible — a theme that overrides nothing is stored as no theme.
"""

import pytest

from app.api.base_v1.schema.portal.group_theme import GroupTheme, MAX_BRAND_MARK_BYTES
from tests.conftest import auth_header, create_org, create_user, make_token

A_PNG = 'data:image/png;base64,iVBORw0KGgo='


@pytest.fixture
async def admin(db):
    await create_user(db, user_id='admin-1', email='admin@test.com')
    org = await create_org(db, owner_user_id='admin-1', slug='acme', display_name='Acme')
    return auth_header(make_token('admin-1', org_id=str(org.id))), str(org.id)


class TestAColourIsAColour:
    def test_hex_in_every_length_is_accepted(self):
        for good in ('#abc', '#AABBCC', '#aabbccdd'):
            assert GroupTheme(accent=good).accent == good

    def test_a_value_carrying_css_syntax_is_refused(self):
        """The whole reason the grammar is this narrow.

        As a string spliced into a stylesheet this closes the
        declaration and opens its own rules. The client never builds
        CSS text — it goes through ``setProperty`` — but a value that
        cannot be a colour has no business being stored either.
        """
        with pytest.raises(ValueError):
            GroupTheme(accent='#fff; } body { display: none } .x {')

    def test_non_hex_syntax_is_refused(self):
        # ``rgb()`` nests expressions and named colours would have to be
        # enumerated to be checked at all. A colour picker emits hex.
        for bad in ('red', 'rgb(1,2,3)', 'var(--x)', 'url(http://x)', '#12'):
            with pytest.raises(ValueError):
                GroupTheme(accent=bad)

    def test_every_colour_field_is_checked(self):
        for field in ('accent', 'danger', 'success'):
            with pytest.raises(ValueError):
                GroupTheme(**{field: 'red'})


class TestTheBrandMark:
    def test_inline_image_bytes_are_accepted(self):
        assert GroupTheme(brand_mark=A_PNG).brand_mark == A_PNG

    def test_svg_is_allowed(self):
        # Allowed because the client renders it as ``<img src=...>``,
        # where an SVG's script cannot run. Inlining it as markup would
        # hand an org admin script execution in their users' clients.
        svg = 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='
        assert GroupTheme(brand_mark=svg).brand_mark == svg

    def test_a_remote_url_is_refused(self):
        with pytest.raises(ValueError):
            GroupTheme(brand_mark='https://evil.example/logo.png')

    def test_a_non_image_payload_is_refused(self):
        with pytest.raises(ValueError):
            GroupTheme(brand_mark='data:text/html;base64,PGgxPmhp')

    def test_an_oversized_mark_is_refused(self):
        with pytest.raises(ValueError):
            GroupTheme(brand_mark='data:image/png;base64,' + 'A' * (MAX_BRAND_MARK_BYTES + 1))


class TestStoringIt:
    def test_a_theme_that_overrides_nothing_is_no_theme(self):
        # One representation of "no branding" in the database rather
        # than two that behave identically.
        assert GroupTheme().to_column() is None

    def test_a_round_trip_keeps_what_was_set(self):
        stored = GroupTheme(accent='#abc', brand_name='Acme').to_column()
        back = GroupTheme.from_column(stored)
        assert back is not None
        assert back.accent == '#abc'
        assert back.brand_name == 'Acme'
        assert back.danger is None

    def test_an_unreadable_row_serves_the_default_palette(self):
        """A hand-edited row must not take the group down with it.

        Validation happened on the way in, so anything unreadable here
        was written around the API. Refusing to serve the group would
        turn a cosmetic problem into an outage for everyone in it.
        """
        assert GroupTheme.from_column('not json') is None
        assert GroupTheme.from_column('{"accent": "red; }body{"}') is None
        assert GroupTheme.from_column(None) is None


class TestThroughTheApi:
    async def test_a_group_can_be_created_with_a_brand(self, client, admin):
        headers, _ = admin
        resp = await client.post(
            '/portal/groups',
            json={'name': 'Legal', 'theme': {'accent': '#123456', 'brand_name': 'Acme'}},
            headers=headers,
        )
        assert resp.status_code in (200, 201)
        assert resp.json()['theme']['accent'] == '#123456'
        assert resp.json()['theme']['brand_name'] == 'Acme'

    async def test_it_can_be_set_afterwards(self, client, admin):
        headers, _ = admin
        group = (await client.post('/portal/groups', json={'name': 'Legal'}, headers=headers)).json()
        resp = await client.patch(
            f'/portal/groups/{group["id"]}',
            json={'theme': {'accent': '#123456'}},
            headers=headers,
        )
        assert resp.json()['theme']['accent'] == '#123456'

    async def test_a_theme_overriding_nothing_clears_it(self, client, admin):
        headers, _ = admin
        group = (
            await client.post(
                '/portal/groups',
                json={'name': 'Legal', 'theme': {'accent': '#123456'}},
                headers=headers,
            )
        ).json()

        resp = await client.patch(f'/portal/groups/{group["id"]}', json={'theme': {}}, headers=headers)
        assert resp.json()['theme'] is None

    async def test_an_unrelated_edit_leaves_it_alone(self, client, admin):
        headers, _ = admin
        group = (
            await client.post(
                '/portal/groups',
                json={'name': 'Legal', 'theme': {'accent': '#123456'}},
                headers=headers,
            )
        ).json()

        resp = await client.patch(f'/portal/groups/{group["id"]}', json={'description': 'x'}, headers=headers)
        assert resp.json()['theme']['accent'] == '#123456'

    async def test_a_bad_colour_is_refused_at_the_portal(self, client, admin):
        """The admin finds out while looking at the form, not after it
        has shipped to everyone in the group."""
        headers, _ = admin
        resp = await client.post(
            '/portal/groups',
            json={'name': 'Legal', 'theme': {'accent': 'red'}},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_the_pack_carries_the_brand(self, client, admin, db):
        """The delivery path: what the client actually polls."""
        headers, org_id = admin
        group = (
            await client.post(
                '/portal/groups',
                json={'name': 'Legal', 'theme': {'accent': '#123456', 'brand_name': 'Acme'}},
                headers=headers,
            )
        ).json()
        await client.post(
            f'/portal/groups/{group["id"]}/members',
            json={'user_id': 'admin-1'},
            headers=headers,
        )

        resp = await client.get('/portal/me/group/pack', headers=headers)
        assert resp.status_code == 200
        assert resp.json()['theme']['accent'] == '#123456'
        assert resp.json()['theme']['brand_name'] == 'Acme'
