"""DEC-13 removed the Celery worker. It has to stay removed.

The decision: F55 found a worker at `--concurrency=200`, a Flower
container, a Redis database and a Postgres result backend serving a queue
nothing ever wrote to — no dispatcher, no schedule, and a docstring
promising three trigger paths that did not exist. B8 re-verified all five
of its claims. B6 put the question, and the answer was to remove it: the
indexing pipeline lives in igni__code-index.

Two things this file is for.

**A removal has to be complete or it is worse than not doing it.** A
leftover `CELERY_*` variable in a compose file, a `worker:` key in the
chart's values, or a `celery` dependency in the lockfile each leave a
reader thinking there is a worker somewhere. `tests/test_celery_registration.py`
existed because a stale `include` entry killed the worker at startup; the
same class of half-finished change is what this replaces it with.

**And the thing it was supposed to do is still not done.** The docstring
promised "on a schedule to recover from missed webhooks". Nothing meets
that need now, exactly as nothing met it before — removing the worker did
not remove the gap, and `DEC-13`'s entry in `docs/DECISIONS.md` says so.
If somebody adds a scheduler later, these tests are what they will
delete, and the decision they should re-read first.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _text(*relative: str) -> str:
    return '\n'.join((_ROOT / r).read_text() for r in relative if (_ROOT / r).exists())


class TestNothingRunsAWorker:
    def test_the_celery_modules_are_gone(self):
        for gone in (
            'app/celery_app.py',
            'app/celery_async_pool.py',
            'app/celery_async_tracer.py',
            'app/tasks',
        ):
            assert not (_ROOT / gone).exists(), f'{gone} survived DEC-13'

    def test_the_application_does_not_import_celery(self):
        """Not "no module named celery" — that is the lockfile's job. A
        stray import would break the API rather than a worker, which is
        the failure this checks for."""
        offenders = [
            str(py.relative_to(_ROOT))
            for py in (_ROOT / 'app').rglob('*.py')
            if 'import celery' in py.read_text() or 'from celery' in py.read_text()
        ]
        assert not offenders, offenders

    def test_no_compose_file_defines_a_worker_or_flower(self):
        """A service in a compose file is a container somebody runs.
        Leaving one behind means the resources the decision was about are
        still being spent."""
        for compose in ('containers/docker-compose.prod.yml', 'containers/docker-compose.local.yml'):
            text = _text(compose)
            assert 'celery_worker:' not in text, f'{compose} still defines the worker'
            assert 'flower:' not in text, f'{compose} still defines Flower'
            assert 'CELERY_' not in text, f'{compose} still sets a CELERY_ variable'

    def test_the_chart_has_no_worker_or_flower(self):
        for gone in (
            'charts/igni/templates/worker-deployment.yaml',
            'charts/igni/templates/flower-deployment.yaml',
        ):
            assert not (_ROOT / gone).exists(), f'{gone} survived DEC-13'

        values = _text('charts/igni/values.yaml')
        for key in ('\nworker:', '\nflower:', 'flowerBasicAuth'):
            assert key not in values, f'values.yaml still carries {key.strip()}'

    def test_no_template_references_the_removed_values(self):
        """The failure mode of a half-removal in Helm: a template reading
        `.Values.flower.enabled` after the key is gone renders as `nil
        pointer evaluating interface {}.enabled`, which says nothing
        about Flower. It happened twice while doing this."""
        templates = _ROOT / 'charts/igni/templates'
        offenders = [
            str(path.relative_to(_ROOT))
            for path in templates.rglob('*')
            if path.is_file() and ('.Values.worker' in path.read_text() or '.Values.flower' in path.read_text())
        ]
        assert not offenders, offenders

    def test_the_makefile_has_no_worker_target(self):
        makefile = _text('Makefile')
        assert '\nworker:' not in makefile
        assert 'celery' not in makefile.lower()

    def test_no_startup_check_warns_about_flower(self):
        """The check existed because Flower is a separate process that
        would never warn anybody. With no Flower, a warning about its
        missing password is a warning about nothing — and a deployment
        that emits one has an operator looking for a container that is
        not there."""
        assert 'flower' not in _text('app/security/startup_checks.py').lower()

    def test_the_config_carries_no_worker_settings(self):
        config = _text('app/config.py')
        assert 'flower_basic_auth' not in config
        assert 'celery_result_db_url' not in config

    @pytest.mark.parametrize('doc', ['docs/ASYNC_CELERY_GUIDE.md'])
    def test_the_guide_is_gone(self, doc):
        """A guide to running a worker that does not exist is the
        F139 shape: a document describing a system the product no longer
        is."""
        assert not (_ROOT / doc).exists(), f'{doc} survived DEC-13'


class TestTheGapItLeavesIsRecorded:
    def test_the_decision_says_what_was_removed_and_why(self):
        decisions = _text('docs/DECISIONS.md')
        assert 'DEC-13' in decisions
        assert 'queue nothing writes to' in decisions

    def test_missed_webhook_recovery_is_still_named_as_a_gap(self):
        """The worker's docstring promised it and never delivered it.
        Removing the worker did not remove the need — and a removal that
        quietly dropped the requirement with the code would be the
        tidier, worse outcome.
        """
        decisions = _text('docs/DECISIONS.md')
        assert (
            'missed webhooks' in decisions
        ), 'the decision no longer records that recovering from missed webhooks is a real need nothing meets'

    def test_retention_still_runs_without_a_worker(self):
        """The one periodic job the product actually needs runs on its own
        asyncio loop inside the API, which is why removing Celery does not
        take retention with it. `retention.py` explains the choice; this
        asserts it is still true."""
        retention = _text('app/services/retention.py')
        assert 'asyncio.create_task' in retention or 'create_task' in retention
        assert 'celery' not in retention.lower() or 'F55' in retention
