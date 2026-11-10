"""Telling somebody they mistyped a tool name.

An agent names its tools in frontmatter. A name igni does not know is
accepted by every step until the agent is invoked, and then it raises —
mid-task, for whoever called it, possibly weeks after the typo was
saved. The person who wrote it never finds out.

So the write path says something. What matters most here is the second
half: it is a *warning*. This list is a copy of igni's and a copy can
lag, so refusing an unknown name would block a deployment running a
newer igni from using its newer tools. A false rejection is worse than
a missed typo, because it stops work that is correct.
"""

from pathlib import Path
import subprocess

import pytest

from app.services.portal.agent_tools import declared_tools, KNOWN_TOOLS, unknown_tools, warning_for
from app.services.portal.default_group_seed import BUNDLED_SEED
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

AGENT = """---
name: reviewer
description: reviews things
tools: {tools}
---

Body.
"""


class TestReadingTheFrontmatter:
    def test_a_comma_joined_string(self):
        assert declared_tools(AGENT.format(tools='WebSearch, Bash')) == ['WebSearch', 'Bash']

    def test_a_yaml_list(self):
        """The loader takes both spellings, so this has to as well."""
        content = '---\nname: r\ntools:\n  - WebSearch\n  - Bash\n---\nBody.\n'
        assert declared_tools(content) == ['WebSearch', 'Bash']

    def test_an_agent_that_names_none(self):
        assert declared_tools('---\nname: r\n---\nBody.\n') == []

    def test_content_with_no_frontmatter(self):
        assert declared_tools('just a body') == []

    def test_unparseable_frontmatter(self):
        """A malformed agent has a bigger problem than its tool names,
        and it is not this function's to report."""
        assert declared_tools('---\n: : :\n---\nBody.\n') == []


class TestSpottingTheOnesThatDoNotExist:
    def test_a_typo_is_caught(self):
        assert unknown_tools(AGENT.format(tools='WebSarch, Bash')) == ['WebSarch']

    def test_real_tools_are_left_alone(self):
        assert unknown_tools(AGENT.format(tools='WebSearch, Bash, Edit')) == []

    def test_a_tool_igni_dropped_is_unknown_now(self):
        """igni reads files through Bash: `Read`, `Grep`, `Glob` and `LS`
        are gone, and an agent still naming them fails when it is called."""
        assert unknown_tools(AGENT.format(tools='Read, Grep, Glob, LS, Bash')) == ['Read', 'Grep', 'Glob', 'LS']

    def test_orchestrate_is_not_a_typo(self):
        """A pseudo-tool the agent builder attaches. igni lists it among
        the names a definition may declare, so declaring it is correct."""
        assert unknown_tools(AGENT.format(tools='Orchestrate, Bash')) == []

    def test_an_mcp_tool_is_not_a_typo(self):
        """Those resolve at runtime from whatever MCP servers the group
        ships, so this list cannot know them."""
        assert unknown_tools(AGENT.format(tools='MCP:github, Bash')) == []


class TestWhatThePersonIsTold:
    def test_nothing_when_the_tools_are_real(self):
        assert warning_for(AGENT.format(tools='WebSearch, Bash')) is None

    def test_it_names_the_tool(self):
        warning = warning_for(AGENT.format(tools='WebSarch, Bash'))
        assert 'WebSarch' in warning

    def test_it_guesses_what_was_meant(self):
        """A typo is nearly always one character from the tool it meant,
        and that guess is the whole message somebody needs."""
        assert "did you mean 'WebSearch'?" in warning_for(AGENT.format(tools='WebSarch'))

    def test_it_says_what_will_happen(self):
        assert 'fail' in warning_for(AGENT.format(tools='Nonsense'))

    def test_something_unlike_any_tool_gets_no_guess(self):
        warning = warning_for(AGENT.format(tools='Frobnicate'))
        assert 'Frobnicate' in warning
        assert 'did you mean' not in warning

    def test_several_at_once(self):
        warning = warning_for(AGENT.format(tools='WebSarch, Bsh'))
        assert 'WebSarch' in warning and 'Bsh' in warning
        assert 'are not tools' in warning


class TestThroughTheApi:
    """The warning rides back on the write, and the write still happens."""

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

    async def test_a_typo_warns_but_saves(self, client, admin_headers, group):
        resp = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'reviewer',
                'content': AGENT.format(tools='WebSarch, Bash'),
                'content_type': 'markdown',
            },
            headers=admin_headers,
        )

        assert resp.status_code in (200, 201)
        body = resp.json()
        assert any('WebSarch' in w for w in body['warnings'])
        # And the entry is there — a warning is not a refusal.
        assert [e['entry_name'] for e in body['entries']] == ['reviewer']

    async def test_real_tools_warn_about_nothing(self, client, admin_headers, group):
        resp = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'agents',
                'entry_name': 'reviewer',
                'content': AGENT.format(tools='WebSearch, Bash'),
                'content_type': 'markdown',
            },
            headers=admin_headers,
        )

        assert resp.json()['warnings'] == []

    async def test_editing_one_in_warns_too(self, client, admin_headers, group):
        created = (
            await client.post(
                f'/portal/groups/{group}/entries',
                json={
                    'kind': 'agents',
                    'entry_name': 'reviewer',
                    'content': AGENT.format(tools='WebSearch'),
                    'content_type': 'markdown',
                },
                headers=admin_headers,
            )
        ).json()
        entry_id = created['entries'][0]['id']

        resp = await client.patch(
            f'/portal/groups/{group}/entries/{entry_id}',
            json={'content': AGENT.format(tools='WebSarch')},
            headers=admin_headers,
        )

        assert any('WebSarch' in w for w in resp.json()['warnings'])

    async def test_a_kind_without_tools_is_not_inspected(self, client, admin_headers, group):
        resp = await client.post(
            f'/portal/groups/{group}/entries',
            json={
                'kind': 'settings',
                'entry_name': 'conf',
                'content': '{"tools": "WebSarch"}',
                'content_type': 'json',
            },
            headers=admin_headers,
        )

        assert resp.json()['warnings'] == []


class TestTheToolsEndpoint:
    """The portal warns as somebody types, and it asks the server what
    the real names are rather than shipping a copy — a list in the
    frontend would be a third place to keep in step, and the first to
    fall behind."""

    @pytest.fixture
    async def headers(self, db):
        await create_user(db, user_id='u1', email='u1@test.com')
        org = await create_org(db, owner_user_id='u1', slug='acme2', display_name='Acme')
        await add_member(db, org_id=str(org.id), user_id='u1', role='member')
        return auth_header(make_token('u1', org_id=str(org.id)))

    async def test_it_lists_the_tools(self, client, headers):
        resp = await client.get('/portal/agent-tools', headers=headers)

        assert resp.status_code == 200
        assert 'WebSearch' in resp.json()['tools']

    async def test_the_list_is_sorted(self, client, headers):
        """So a diff of two deployments is readable."""
        tools = (await client.get('/portal/agent-tools', headers=headers)).json()['tools']

        assert tools == sorted(tools)

    async def test_it_is_the_same_list_the_write_path_checks(self, client, headers):
        """Two lists that can disagree would be worse than one warning
        in one place."""
        tools = (await client.get('/portal/agent-tools', headers=headers)).json()['tools']

        assert set(tools) == set(KNOWN_TOOLS)

    async def test_a_member_can_read_it(self, client, headers):
        """Not admin-only: anybody who can open an entry can be shown
        the warning."""
        assert (await client.get('/portal/agent-tools', headers=headers)).status_code == 200


def _igni_checkout() -> Path:
    """Where igni's core is checked out, if it is.

    It left the monorepo for a repository of its own, `igni-core`, which
    CI checks out under that name beside this one. A checkout under the
    old name is used too — but only if it holds `src/igni_core`: the
    frozen monorepo that stands at that name locally has stopped
    following igni.
    """
    root = Path(__file__).resolve().parents[2]
    for name in ('igni-core', 'igni'):
        candidate = root / name
        if (candidate / 'src' / 'igni_core').is_dir():
            return candidate
    return root / 'igni-core'


#: The CLI checkout beside this one, where `make refresh-agent-tools` reads.
_IGNI_PYTHON = _igni_checkout() / '.venv' / 'bin' / 'python'


class TestTheListIsIgnis:
    """A copy of igni's list, refreshed by `make refresh-agent-tools`.

    It had lagged: `Read`, `Grep`, `Glob`, `LS`, `Python` and `CodeIndex`
    were still accepted after igni dropped them, and `Orchestrate` was
    warned about though igni lists it.
    """

    @pytest.mark.skipif(not _IGNI_PYTHON.is_file(), reason='no igni checkout beside this one — skipped, not passed')
    def test_it_is_what_igni_answers(self):
        answered = subprocess.run(
            [
                str(_IGNI_PYTHON),
                '-c',
                'from igni_core.core.tools.registry import ToolRegistry; '
                "print(chr(10).join(ToolRegistry(base_dir='.').available_tools))",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout

        assert set(answered.split()) == set(KNOWN_TOOLS)

    def test_a_modules_tool_named_in_tools_is_still_warned_about(self):
        """`CodeIndex` is a paid module's, and igni gives a module's or an
        extension's tools to agents over MCP, not by name: igni cannot
        resolve it in `tools:` either, and the agent raises when called.
        The warning is the true answer, not a gap in this list."""
        assert unknown_tools(AGENT.format(tools='CodeIndex, Bash')) == ['CodeIndex']

    def test_no_agent_the_default_group_ships_is_warned_about(self):
        """A new member must not be told about an agent nobody in their
        organisation has touched."""
        warned = [
            (entry.entry_name, unknown_tools(entry.content))
            for entry in BUNDLED_SEED.entries()
            if entry.kind == 'agents'
        ]

        assert [(name, unknown) for name, unknown in warned if unknown] == []
