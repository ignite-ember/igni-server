"""The kinds a group can ship.

One table, in app/services/portal/entry_kinds.py, because the list used
to be spelled out in the service, the schemas and four places in the
portal. What matters here is that it stays honest about the other side:
every id is a directory igni reads, and every content type is the
one its loader parses. A kind that exists only on this side is a picker
entry that ships a file nothing opens.
"""

from app.services.portal.entry_kinds import (
    ALLOWED_CONTENT_TYPE,
    BY_ID,
    ENTRY_KINDS,
    KINDS_WITH_MODEL,
    VALID_KINDS,
)


class TestTheTable:
    def test_ids_are_unique(self):
        assert len({kind.id for kind in ENTRY_KINDS}) == len(ENTRY_KINDS)

    def test_everything_derives_from_it(self):
        assert VALID_KINDS == set(BY_ID)
        assert set(ALLOWED_CONTENT_TYPE) == VALID_KINDS

    def test_every_kind_says_what_it_is(self):
        for kind in ENTRY_KINDS:
            assert kind.label, kind.id
            assert kind.description.endswith('.'), kind.id

    def test_a_hook_says_where_it_runs_and_that_strict_does_not_stop_it(self):
        """F39. igni runs a hook's command with ``bash -c`` on every
        member's machine, and no permission gates that path — a
        developer's ``--strict`` constrains the agent, not the hooks a
        group ships. The admin writing one is the person who has to know,
        and the kind's description is what they read while choosing it."""
        said = BY_ID['hooks'].description

        assert 'every member\u2019s machine' in said
        assert 'outside the agent\u2019s permissions' in said
        assert '--strict' in said

    def test_only_agents_run_against_a_model(self):
        """A model on an MCP server or a settings file would be stored
        and never read."""
        assert KINDS_WITH_MODEL == {'agents'}
        assert KINDS_WITH_MODEL <= VALID_KINDS


class TestWhatTheClientCanActuallyLoad:
    """These ids and formats are a contract with igni. A kind
    added here and not there ships a file into a directory nothing
    scans, which looks exactly like it worked.
    """

    #: kind → the format igni's loader parses.
    EXPECTED = {
        'agents': 'markdown',
        'skills': 'markdown',
        'commands': 'markdown',
        'workflows': 'javascript',
        'rules': 'markdown',
        'output-styles': 'markdown',
        'hooks': 'json',
        'tools': 'python',
        'mcps': 'json',
        'plugins': 'yaml',
        # A shell file a hook invokes. igni ships nothing itself, so a
        # hook that named a path on the developer's machine depended on
        # a file the group never sent — and a missing command exits 127,
        # which the hook runner reads as "did not block", making it a
        # silent no-op rather than an error.
        'scripts': 'shell',
        'settings': 'json',
    }

    def test_the_kinds_are_the_ones_igni_core_reads(self):
        assert VALID_KINDS == set(self.EXPECTED)

    def test_each_carries_the_format_its_loader_parses(self):
        assert ALLOWED_CONTENT_TYPE == self.EXPECTED

    def test_a_workflow_is_javascript(self):
        """It is run by Node as an ES module, not read as text."""
        assert BY_ID['workflows'].content_type == 'javascript'

    def test_a_tool_is_python(self):
        assert BY_ID['tools'].content_type == 'python'

    def test_a_script_is_shell_and_executable(self):
        from app.services.portal.entry_kinds import EXECUTABLE_KINDS

        assert BY_ID['scripts'].content_type == 'shell'
        assert 'scripts' in EXECUTABLE_KINDS, (
            'a script written mode 0644 fails with 127, which the hook runner treats as '
            'non-blocking — the silent no-op this kind exists to remove'
        )
