"""`make lint` and CI must check the same code.

They did not. The Makefile linted `app tests alembic`; CI linted
`app tests alembic tools`. So `make lint` passed on a checkout CI would
reject, and CI had been rejecting it — an unsorted import block in
`tools/generate_records_of_processing.py`, and another in the migration
B5 added. Both fixable in one command, and neither found for as long as
nobody opened a CI run, because the local command said everything was
fine.

That is the "two places, one value" failure this repository keeps
finding, in the one place where the cost is that *every other check
stops being believed*: a red CI that a developer's own tooling calls
green trains people to stop reading CI.

The rule: the paths in the Makefile and the paths in the workflow are
read out of both files and compared. Not "the Makefile includes tools" —
that is the current answer, and it would go stale the same way. Whichever
list changes, the other has to follow or this fails.

The formatter is checked the same way, and separately, because `black`
deliberately covers less than `ruff` does: it formats `app` only. That
is a real decision — the generators and tests are written to be read as
prose, with alignment black would collapse — and a rule that demanded the
two tools agree with each other would be wrong. What it must not do is
differ *between the Makefile and CI*.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MAKEFILE = _ROOT / 'Makefile'
_WORKFLOW = _ROOT / '.github/workflows/ci.yml'


def _make_variable(name: str) -> list[str]:
    """The value of a `NAME := a b c` assignment in the Makefile."""
    match = re.search(rf'^{name}\s*:=\s*(.+)$', _MAKEFILE.read_text(), re.M)
    assert match, f'{name} is no longer assigned in the Makefile'
    return match.group(1).split()


def _ci_paths(tool: str) -> list[str]:
    """The paths CI passes to `tool`, from its `run:` line.

    Anything starting with `-` is a flag, not a path — `black --check
    app` has to read as `['app']` or the comparison is against a list
    that can never match.
    """
    match = re.search(rf'run:\s*uv run {tool}([^\n]*)', _WORKFLOW.read_text())
    assert match, f'CI no longer runs {tool}'
    return [token for token in match.group(1).split() if not token.startswith('-')]


class TestTheListsAreReadCorrectly:
    """Both sides are parsed out of files. A parser that returned nothing
    would make every comparison below trivially true."""

    def test_the_makefile_variables_parse(self):
        assert _make_variable('SRC'), 'SRC parsed empty'
        assert _make_variable('SRC_FMT'), 'SRC_FMT parsed empty'
        assert 'app' in _make_variable('SRC')

    def test_the_workflow_lines_parse(self):
        assert _ci_paths('ruff check'), 'ruff paths parsed empty'
        assert _ci_paths('black'), 'black paths parsed empty'
        assert _ci_paths('black') == ['app'], _ci_paths('black')

    def test_the_flags_are_not_mistaken_for_paths(self):
        """`black --check app` is one path, not two. Without this the
        comparison would be against `['--check', 'app']`, which never
        equals a Makefile variable and so fails for the wrong reason —
        the kind of always-red check that gets deleted."""
        assert '--check' not in _ci_paths('black')


class TestTheyAgree:
    def test_ruff_checks_the_same_paths(self):
        local = sorted(_make_variable('SRC'))
        ci = sorted(_ci_paths('ruff check'))

        assert local == ci, (
            f'`make lint` runs ruff over {local} and CI runs it over {ci}. Whichever is '
            f'narrower lets code through that the other rejects, and a green local lint on '
            f'a red CI is how people stop reading CI at all.'
        )

    def test_black_formats_the_same_paths(self):
        local = sorted(_make_variable('SRC_FMT'))
        ci = sorted(_ci_paths('black'))

        assert local == ci, f'`make lint` formats {local} and CI formats {ci}.'

    def test_mypy_checks_the_same_paths(self):
        makefile = _MAKEFILE.read_text()
        match = re.search(r'-m mypy\s+([^\n]*)', makefile)
        assert match, 'the Makefile no longer runs mypy'
        local = sorted(t for t in match.group(1).split() if not t.startswith('-'))

        assert local == sorted(
            _ci_paths('mypy')
        ), f'`make lint` type-checks {local} and CI checks {sorted(_ci_paths("mypy"))}.'


class TestTheNarrowerFormatterIsDeliberate:
    """`black` covers `app` and `ruff` covers more, which looks like the
    same bug one level up. It is not: the generators and the tests are
    written to be read, and black would reflow prose-shaped call sites
    into something worse. Recorded here so the next person does not
    "fix" it by widening SRC_FMT and reformatting forty files.
    """

    def test_the_formatter_is_narrower_than_the_linter(self):
        assert set(_make_variable('SRC_FMT')) < set(_make_variable('SRC')), (
            'SRC_FMT is no longer a strict subset of SRC. If black now covers everything '
            'ruff does that may be right, but it is a decision — see this test.'
        )

    def test_the_makefile_says_why(self):
        makefile = _MAKEFILE.read_text()
        assert 'SRC_FMT' in makefile
        assert 'ci.yml' in makefile, (
            'the Makefile no longer explains that these lists have to match the workflow, '
            'which is the only reason anybody would keep them in step'
        )


class TestThePortalTypecheckLooksAtSomething:
    """The same failure in the other language, and the other direction.

    CI's *Lint Portal* job ran ``npx tsc --noEmit`` for as long as the
    job existed. That command checks **nothing** here: ``tsconfig.json``
    is a solution file — ``"files": []`` plus three references — so a
    bare ``tsc`` compiles zero files and exits 0. ``tsc --noEmit
    --listFiles`` prints nothing at all.

    Type errors did still fail the build, in the *Image Scan* job,
    because the portal Dockerfile runs ``npm run build`` and that is
    ``tsc -b``. So the check existed and the step named after it was not
    the one performing it — which is worse than having no step, because
    a green "Lint & Type Check" is read as "the types are fine". Four
    real errors sat in the tree while three separate runs of that
    command reported success.

    ``make portal-lint`` already used ``tsc -b``. Here the Makefile was
    right and CI was wrong, which is the first half of this file exactly
    reversed — the lesson is not "trust the Makefile", it is that two
    places holding one value drift in whichever direction nobody is
    looking.

    The rule compares the command CI runs against the script the package
    defines, rather than asserting either one is ``tsc -b``. Naming the
    answer is how the first version of this file would have gone stale.
    """

    @staticmethod
    def _package_script(name: str) -> str:
        import json

        scripts = json.loads((_ROOT / 'portal/package.json').read_text())['scripts']
        assert name in scripts, f'portal/package.json no longer defines a `{name}` script'
        return scripts[name]

    @staticmethod
    def _ci_portal_run_lines() -> list[str]:
        """Every `run:` command in the Lint Portal job."""
        text = _WORKFLOW.read_text()
        start = text.index('  lint-portal:')
        end = text.index('\n  ', text.index('\n', start) + 1)
        # The job ends at the next top-level key; walk to the following
        # two-space-indented name rather than guessing a line count.
        rest = text[start:]
        following = re.search(r'\n  [a-z][a-z-]*:\n', rest[1:])
        end = start + 1 + (following.start() if following else len(rest))
        body = text[start:end]
        return [m.group(1).strip() for m in re.finditer(r'run:\s*([^\n]+)', body)]

    def test_the_job_was_found(self):
        """A parser that returned nothing makes every rule below pass."""
        lines = self._ci_portal_run_lines()

        assert len(lines) > 3, f'only found {lines} in the Lint Portal job'
        assert any(
            'eslint' in line for line in lines
        ), 'the Lint Portal job no longer runs eslint — has it been renamed?'

    def test_ci_typechecks_with_the_package_script(self):
        lines = self._ci_portal_run_lines()

        assert any('npm run typecheck' in line for line in lines), (
            'the Lint Portal job does not run `npm run typecheck`. Whatever it runs '
            'instead has to be the same command `portal/package.json` defines, or the '
            'two drift and one of them is the one nobody reads.'
        )

    def test_no_job_runs_the_command_that_checks_nothing(self):
        """``tsc`` without ``-b`` against this tsconfig is a no-op.

        Stated over the whole workflow rather than the one job, because
        the next person to add a type check will copy the line that was
        here before.

        Scanned over ``run:`` commands rather than every line: the first
        version read the whole file and fired on the *comment* that
        explains this trap, which quotes the offending command twice.
        Narrowing to commands is precision and not slack — a comment
        does not execute — but the narrowed rule was re-checked against
        a restored ``run: npx tsc --noEmit`` before being believed.
        """
        offenders = [
            command.strip()
            for command in re.findall(r'run:\s*([^\n]+)', _WORKFLOW.read_text())
            if re.search(r'\btsc\b', command) and not re.search(r'-b\b|--build|npm run', command)
        ]

        assert offenders == [], (
            f'{offenders} — a bare `tsc` compiles nothing here, because tsconfig.json is a '
            'solution file. Use `npm run typecheck`.'
        )

    def test_the_makefile_typechecks_in_build_mode_too(self):
        makefile = _MAKEFILE.read_text()
        match = re.search(r'^portal-lint:\n\t([^\n]+)$', makefile, re.M)
        assert match, 'the Makefile no longer has a portal-lint target'

        assert re.search(
            r'-b\b|--build|npm run typecheck', match.group(1)
        ), f'`make portal-lint` runs `{match.group(1)}`, which checks no files.'

    def test_the_build_typechecks_before_it_bundles(self):
        """The last line of defence, and the one that caught this.

        If `npm run build` stops running the compiler, the container
        build stops failing on type errors — and with the CI step
        already blind, nothing anywhere would check them.
        """
        build = self._package_script('build')

        assert re.search(
            r'\btsc\b.*(-b|--build)', build
        ), f'portal build is `{build}` — it no longer type-checks before bundling.'


class TestTheTestDatabaseExistsWhereTheSuiteLooksForIt:
    """CI created one database and the suite connected to another.

    The workflow's postgres service was started with
    ``POSTGRES_DB: postgres``; ``tests/conftest.py`` connects to
    ``ember_test``. So the conftest import raised ``database
    "ember_test" does not exist`` and **every test in this repository
    failed at collection** — the job was red at its first step, and had
    been for as long as the two disagreed.

    That is the same shape as the lint paths above with a much larger
    blast radius: not "some checks ran against the wrong file list" but
    "no check ran at all", while the workflow file still listed a Pytest
    step and the repository still counted 2,400 tests.

    Read out of both files and compared, for the same reason as
    everything else here: naming ``ember_test`` in this test would make
    it a third place to change.
    """

    @staticmethod
    def _conftest_database() -> str:
        """The database name in conftest's test URL."""
        conftest = (_ROOT / 'tests/conftest.py').read_text()
        match = re.search(r'TEST_DB_URL_ASYNC\s*=\s*[\'"][^\'"]+/([A-Za-z0-9_]+)[\'"]', conftest)
        assert match, 'conftest no longer defines TEST_DB_URL_ASYNC with a database name'
        return match.group(1)

    @staticmethod
    def _ci_service_env(service: str, key: str) -> str:
        """A `key: value` under the named service's `env:` block."""
        text = _WORKFLOW.read_text()
        start = text.index(f'      {service}:\n')
        # Up to the next service or the `steps:` that ends the block.
        rest = text[start:]
        end = re.search(r'\n    steps:|\n      [a-z][a-z-]*:\n        image:', rest[1:])
        block = rest[: end.start() + 1] if end else rest
        match = re.search(rf'^\s*{key}:\s*(\S+)\s*$', block, re.M)
        assert match, f'the {service} service no longer sets {key}'
        return match.group(1)

    def test_both_sides_were_parsed(self):
        """Neither half may be silently empty."""
        assert self._conftest_database()
        assert self._ci_service_env('postgres', 'POSTGRES_DB')

    def test_ci_creates_the_database_the_suite_connects_to(self):
        conftest_db = self._conftest_database()
        ci_db = self._ci_service_env('postgres', 'POSTGRES_DB')

        assert ci_db == conftest_db, (
            f'conftest connects to `{conftest_db}` and CI creates `{ci_db}`. The suite '
            'will fail at collection with "database does not exist", which reads as an '
            'infrastructure error rather than as a configuration mismatch.'
        )

    def test_the_credentials_match_too(self):
        """The other half of the same connection string.

        A user or password mismatch fails identically and for the same
        reason; checking only the database name would leave two of the
        three ways to get this wrong unguarded.
        """
        conftest = (_ROOT / 'tests/conftest.py').read_text()
        match = re.search(r'TEST_DB_URL_ASYNC\s*=\s*[\'"][^:]+://([^:]+):([^@]+)@', conftest)
        assert match, 'conftest no longer spells out credentials in TEST_DB_URL_ASYNC'
        user, password = match.group(1), match.group(2)

        assert self._ci_service_env('postgres', 'POSTGRES_USER') == user
        assert self._ci_service_env('postgres', 'POSTGRES_PASSWORD') == password


class TestCIPointsTheSuiteAtTheServicesItStarts:
    """A default that names a compose service does not resolve in CI.

    ``Settings.redis_host`` defaults to ``redis`` — the service name in
    ``docker-compose.local.yml``. The workflow's ``test`` job is not
    containerised, so its services are published on the runner's
    localhost and that hostname resolves to nothing. The first CI run
    that got past collection produced 83 failures and 58 errors, every
    one of them ``Temporary failure in name resolution``.

    It passes locally only because a developer's **untracked** ``.env``
    sets ``REDIS_HOST=localhost``. That is the worst way for a suite to
    be green: the thing making it work is not in the repository, so no
    local run — however careful — can reveal the gap, and the CI
    failure looks like broken infrastructure rather than missing
    configuration.

    ``conftest.py`` sidesteps this for Postgres by hardcoding
    ``localhost`` in its URLs. Anything the suite reaches through the
    settings object instead needs the workflow to say so, and this is
    what notices when a new one appears.
    """

    #: Settings whose default is a hostname, and the variable CI must
    #: set to override it. Add a row when a new backing service arrives.
    _HOSTS = (('redis_host', 'REDIS_HOST'),)

    @staticmethod
    def _default_for(setting: str) -> str:
        config = (_ROOT / 'app/config.py').read_text()
        match = re.search(rf"^\s*{setting}\s*:\s*str\s*=\s*Field\(default='([^']*)'", config, re.M)
        assert match, f'`{setting}` is no longer a Field with a string default'
        return match.group(1)

    @staticmethod
    def _pytest_step_env() -> dict[str, str]:
        """The `env:` block on the workflow's Pytest step."""
        text = _WORKFLOW.read_text()
        start = text.index('      - name: Pytest')
        block = text[start : text.index('run:', start)]
        return dict(re.findall(r'^\s{10}([A-Z_]+):\s*(\S+)\s*$', block, re.M))

    def test_the_step_was_found(self):
        """A parser returning nothing would make the rule vacuous."""
        env = self._pytest_step_env()

        assert 'APP_ENV' in env, f'could not read the Pytest step env, got {env}'

    def test_every_service_hostname_is_overridden(self):
        env = self._pytest_step_env()

        wrong = []
        for setting, variable in self._HOSTS:
            default = self._default_for(setting)
            if default in {'localhost', '127.0.0.1'}:
                continue  # resolves on a runner as-is
            if env.get(variable) not in {'localhost', '127.0.0.1'}:
                wrong.append(f'{setting} defaults to {default!r}; CI sets {variable}={env.get(variable)!r}')

        assert wrong == [], (
            f'{wrong} — these resolve to a compose service name that does not exist on '
            'a GitHub runner. The suite will fail with "Temporary failure in name '
            'resolution", which reads as broken infrastructure.'
        )

    def test_the_service_is_actually_declared(self):
        """Overriding to localhost only helps if something is listening.

        Guards the pairing from the other side: a `REDIS_HOST:
        localhost` with no redis service is the same failure wearing a
        different message.
        """
        text = _WORKFLOW.read_text()
        services = text[text.index('  test:') : text.index('    steps:', text.index('  test:'))]

        for setting, _variable in self._HOSTS:
            name = setting.removesuffix('_host')
            assert (
                f'      {name}:' in services
            ), f'the test job declares no `{name}` service, but the suite connects to one'
