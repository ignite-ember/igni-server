"""A hook must reach a script the group actually ships.

igni ships nothing itself: everything a session runs arrives from the
server. That was already true of agents, skills and workflows, but hooks
were an exception — a hook entry stored only a *declaration*, and its
``command`` named a path on the developer's machine. A group could ship
``.ember/hooks/pre-pr-review.sh`` as a hook and not ship the script.

The failure of that is invisible. A hook command runs through
``bash -c``; a missing file exits 127, and the hook runner treats every
non-zero code except 2 as "did not block". So the hook did nothing on
every matching tool call, silently, for everyone in the group.

``{group_scripts}/<name>`` is a literal placeholder rather than an
environment variable bash would expand for free, precisely so the
reference is checkable here — while somebody is still looking at the
form they typed it into.
"""

import json

import pytest

from app.services.portal.hook_scripts import referenced_scripts, warning_for


def _hook(command: str) -> str:
    return json.dumps({'event': 'PreToolUse', 'type': 'command', 'command': command, 'matcher': 'Bash'})


class TestReferencedScripts:
    def test_finds_a_quoted_reference(self):
        assert referenced_scripts(_hook('"{group_scripts}/pre-pr-review.sh"')) == ['pre-pr-review']

    def test_finds_an_unquoted_reference(self):
        assert referenced_scripts(_hook('{group_scripts}/lint.sh --fix')) == ['lint']

    def test_strips_only_the_extension(self):
        """The entry is named ``a.b``; only the trailing ``.sh`` belongs
        to the materialised file."""
        assert referenced_scripts(_hook('{group_scripts}/a.b.sh')) == ['a.b']

    def test_finds_several(self):
        command = '{group_scripts}/one.sh && {group_scripts}/two.sh'
        assert referenced_scripts(_hook(command)) == ['one', 'two']

    def test_ignores_a_command_with_no_placeholder(self):
        assert referenced_scripts(_hook('/usr/local/bin/check --staged')) == []

    def test_survives_content_that_is_not_a_hook(self):
        for content in ('', 'not json', '[]', '{}', json.dumps({'event': 'X'})):
            assert referenced_scripts(content) == []


class TestWarning:
    def test_warns_when_the_script_is_missing(self):
        message = warning_for(_hook('"{group_scripts}/pre-pr-review.sh"'), available_scripts=set())
        assert message is not None
        assert 'pre-pr-review' in message
        # The silence is the part worth saying out loud.
        assert 'silently' in message

    def test_silent_when_the_group_ships_it(self):
        assert (
            warning_for(
                _hook('"{group_scripts}/pre-pr-review.sh"'),
                available_scripts={'pre-pr-review'},
            )
            is None
        )

    def test_names_every_missing_script(self):
        message = warning_for(
            _hook('{group_scripts}/one.sh && {group_scripts}/two.sh'),
            available_scripts={'one'},
        )
        assert message is not None
        assert 'two' in message
        assert "'one'" not in message

    def test_says_nothing_about_a_command_it_cannot_check(self):
        """A hook may deliberately call something baked into the image."""
        assert warning_for(_hook('/opt/acme/bin/check'), available_scripts=set()) is None


class TestTheSeededDefaultIsConsistent:
    def test_every_seeded_hook_reaches_a_seeded_script(self):
        """The Default group is the one every deployment starts from, so
        it must not ship the broken state this module detects."""
        from app.services.portal.default_group import seed_entries

        entries = seed_entries()
        scripts = {name for kind, name, _ in entries if kind == 'scripts'}
        hooks = [(name, content) for kind, name, content in entries if kind == 'hooks']
        assert hooks, 'the Default group should still seed hooks'

        for name, content in hooks:
            assert (
                warning_for(content, available_scripts=scripts) is None
            ), f'seeded hook {name!r} references a script the seed does not ship'

    def test_the_seeded_hooks_use_the_placeholder(self):
        """Not a machine path: igni ships nothing, so a hook pointing at
        ``.igni/hooks/x.sh`` would depend on a file nobody sent.

        Both directory names are checked. ``.ember`` was the name before
        the rename, and a test that names only the retired one passes for
        the wrong reason — it can no longer fail.
        """
        from app.services.portal.default_group import seed_entries

        for kind, name, content in seed_entries():
            if kind != 'hooks':
                continue
            command = json.loads(content).get('command', '')
            assert '{group_scripts}' in command, f'{name!r} does not reach a group-shipped script'
            for machine_path in ('.igni/hooks', '.ember/hooks'):
                assert machine_path not in command, f'{name!r} points at {machine_path} on the developer machine'

    @pytest.mark.parametrize('expected', ['pre-pr-review', 'post-commit-todo'])
    def test_the_scripts_themselves_are_seeded(self, expected):
        from app.services.portal.default_group import seed_entries

        scripts = {name: content for kind, name, content in seed_entries() if kind == 'scripts'}
        assert expected in scripts
        assert scripts[expected].startswith('#!')
