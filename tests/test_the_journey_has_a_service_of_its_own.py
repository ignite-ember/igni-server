"""The journey's service steps run without the extension starter.

Steps 7a-7d (and 8d's erasure call) need a registered service that
ships an extension. That was only ever the extension starter's Team
Notes, which CI cannot check out, so CI skipped all of them.
`tools/e2e_journey/service_fixture/` is the journey's own, built with
igni-service from the igni checkout, and `journey.sh` uses it whenever
the starter is not there. These hold it to what igni server accepts and
to that choice; the journey itself runs it end to end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tomllib

import pytest

from app.services.identity.extension_manifest import Manifest
from app.services.identity.impact.extension_impact import ExtensionImpact

_E2E = Path(__file__).resolve().parent.parent / 'tools' / 'e2e_journey'
_EXTENSION = _E2E / 'service_fixture' / 'extension'


def _manifest() -> dict:
    return json.loads((_EXTENSION / 'manifest.json').read_text())


class TestTheFixturesExtension:
    def test_igni_server_accepts_its_manifest(self):
        """What `publish` checks at upload: a manifest it can read, and an
        impact it can work out."""
        raw = _manifest()

        manifest = Manifest.model_validate(raw)
        ExtensionImpact.of(manifest)

        assert manifest.id == 'journey.fixture'

    def test_the_package_it_names_is_the_one_its_pyproject_builds(self):
        """`igni-service-bundle` refuses a bundle whose manifest and
        pyproject disagree — after downloading every wheel."""
        manifest = _manifest()
        project = tomllib.loads((_EXTENSION / 'pyproject.toml').read_text())['project']

        assert project['name'] == manifest['python']['package']
        assert manifest['python']['entry'] in project['scripts']

    def test_every_tool_it_declares_states_its_four_hints(self):
        for tool in _manifest()['permissions']['tools']:
            assert set(tool['annotations']) == {'readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint'}
            assert tool['name'].startswith('journey.fixture.')


def _service(tmp_path: Path, **env: str) -> str:
    """Which service `journey.sh` would run, as it decides it."""
    base = {k: v for k, v in os.environ.items() if k not in ('JOURNEY_SERVICE', 'NO_SERVICE', 'IGNI_STARTER')}
    answered = subprocess.run(
        [str(_E2E / 'journey.sh'), 'service'],
        env={**base, 'E2E_RUN_DIR': str(tmp_path / 'run'), **env},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (answered.stdout + answered.stderr).strip()


class TestWhichServiceTheJourneyRuns:
    def test_without_the_starter_it_is_the_fixture(self, tmp_path):
        """CI's case: no starter beside the checkout."""
        assert _service(tmp_path, IGNI_STARTER=str(tmp_path / 'no-starter')) == 'fixture'

    def test_with_the_starter_it_is_team_notes(self, tmp_path):
        (tmp_path / 'starter').mkdir()

        assert _service(tmp_path, IGNI_STARTER=str(tmp_path / 'starter')) == 'starter'

    @pytest.mark.parametrize('env', [{'NO_SERVICE': '1'}, {'JOURNEY_SERVICE': 'none'}])
    def test_none_when_asked_for_none(self, tmp_path, env):
        assert _service(tmp_path, IGNI_STARTER=str(tmp_path / 'no-starter'), **env) == 'none'

    def test_the_fixture_can_be_chosen_with_the_starter_there(self, tmp_path):
        (tmp_path / 'starter').mkdir()

        assert _service(tmp_path, IGNI_STARTER=str(tmp_path / 'starter'), JOURNEY_SERVICE='fixture') == 'fixture'

    def test_anything_else_is_refused(self, tmp_path):
        assert 'must be starter, fixture or none' in _service(tmp_path, JOURNEY_SERVICE='teamnotes')
