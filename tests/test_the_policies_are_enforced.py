"""Two policy clauses had nothing behind them.

C3. `docs/POLICIES.md` states requirements and must cite what enforces
each one. Writing it found two clauses whose enforcement nothing checked:

**The password policy.** `_validate_password_strength` requires eight
characters with an upper-case letter, a lower-case letter, a digit and a
symbol — and no test asserted any of it. The policy states those numbers
because they are what the code does, so a change to the validator that
nobody noticed would leave the policy describing a rule the product no
longer had. Which is F139's failure mode aimed at a document that is
supposed to be binding.

**That CI runs the test suite at all.** F7 was "CI never runs the test
suite": lint, types and the portal's `tsc`, and no `pytest`, for either
repository. 851 server tests existed and nothing enforced them on a pull
request. It was fixed, and nothing has guarded it since — so the same
regression could arrive in a workflow edit, and every other rule in this
repository would go quiet at once without a single test failing.

That second one is the more interesting: it is the guard on the guards.
A repository whose safety comes from 1900 assertions has, as its single
point of failure, the line that runs them.
"""

from __future__ import annotations

import pathlib

from pydantic import ValidationError
import pytest

from app.api.base_v1.schema.auth import ResetNewPasswordRequest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestThePasswordPolicyIsWhatTheDocumentSays:
    """AC-P4. Eight characters and four character classes.

    Asserted through a request model rather than by importing the
    validator, because what matters is that a *request* carrying a weak
    password is refused — a validator nothing is wired to would pass a
    unit test and protect nothing.
    """

    def test_a_compliant_password_is_accepted(self):
        """The control. Without it, a model that rejected everything
        would pass every case below."""
        request = ResetNewPasswordRequest(email='drill@example.com', code='123456', new_password='Str0ng!Pass')
        assert request.new_password == 'Str0ng!Pass'

    @pytest.mark.parametrize(
        'password,missing',
        [
            ('Sh0rt!A', 'at least 8 characters'),
            ('nouppercase1!', 'uppercase'),
            ('NOLOWERCASE1!', 'lowercase'),
            ('NoDigitsHere!', 'digit'),
            ('NoSymbolHere1', 'special character'),
        ],
    )
    def test_each_requirement_is_enforced_separately(self, password, missing):
        """One case per rule, so a validator that dropped a single class
        check fails specifically rather than a compound password hiding
        which rule went missing."""
        with pytest.raises(ValidationError) as caught:
            ResetNewPasswordRequest(email='drill@example.com', code='123456', new_password=password)
        assert missing in str(caught.value), str(caught.value)

    def test_the_document_states_the_number_the_code_enforces(self):
        """The policy says "at least eight characters" and four classes.
        If somebody raises the validator to twelve, this fails and the
        policy gets updated with it — which is the only way a policy and
        a product stay in agreement.
        """
        source = (_ROOT / 'app/api/base_v1/schema/auth.py').read_text()
        assert 'len(password) < 8' in source, 'the minimum length changed; update AC-P4'

        policy = ' '.join((_ROOT / 'docs/POLICIES.md').read_text().split())
        assert 'at least eight characters' in policy
        for characterclass in ('upper-case letter', 'lower-case letter', 'digit', 'symbol'):
            assert characterclass in policy, characterclass


class TestCiRunsTheSuiteThatEnforcesEverythingElse:
    """SD-P1, and the guard on the guards.

    F7: CI ran lint, types and `tsc`, and no tests, for either
    repository. Every rule in this repository would go quiet at once if
    that returned, and nothing would fail — because the thing that would
    have failed is what stopped running.
    """

    @pytest.fixture
    def ci(self) -> str:
        return (_ROOT / '.github/workflows/ci.yml').read_text()

    def test_the_workflow_was_read(self, ci):
        """A comparison against an empty string agrees with everything."""
        assert 'jobs:' in ci
        assert len(ci.splitlines()) > 100

    def test_it_runs_the_server_suite(self, ci):
        assert 'pytest tests/' in ci, (
            'no CI job runs the server test suite. This is F7 returning, and it silences every '
            'other rule in this repository without failing anything.'
        )

    def test_it_runs_the_type_checker_and_the_linter(self, ci):
        assert 'mypy' in ci, 'CI no longer type-checks'
        assert 'ruff' in ci, 'CI no longer lints'

    def test_it_runs_the_portal_suite(self, ci):
        assert 'vitest' in ci or 'npm test' in ci, 'CI no longer runs the portal unit tests'

    def test_it_runs_the_browser_suites(self, ci):
        assert 'playwright test' in ci, 'CI no longer runs a browser suite'

    def test_it_installs_the_chart_and_drills_the_backup(self, ci):
        """F1 and F5. Both were added because a shipped artefact had
        never been executed, and both are one workflow edit from being
        unexecuted again."""
        assert 'tools/kind/install.sh' in ci, 'CI no longer installs the chart on a cluster'
        assert 'tools/restore-drill.sh' in ci, 'CI no longer runs the restore drill'

    def test_it_runs_the_journey_with_a_real_sign_in(self, ci):
        """The one job that crosses the repositories, and the only place
        SSO runs end to end — the first run of it found every SSO login
        signing nobody in."""
        assert 'tools/e2e_journey/journey.sh run' in ci, 'CI no longer runs the free-base journey'
        assert 'repository: ignite-ember/igni' in ci, 'the journey no longer checks out the igni CLI'

    def test_the_gates_are_not_allowed_to_fail_silently(self, ci):
        """`|| true` on a gate is a gate that reports rather than gates.
        The Makefile uses it deliberately for the formatter; a CI job
        must not.
        """
        offenders = [line.strip() for line in ci.splitlines() if '|| true' in line and not line.strip().startswith('#')]
        assert not offenders, f'these CI steps cannot fail: {offenders}'
