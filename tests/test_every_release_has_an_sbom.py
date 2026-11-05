"""Every image that ships has an SBOM, and the portal has two.

CM-8 in `COMPLIANCE_TRACKER.md`. Both marketplaces increasingly expect a
software bill of materials, and SOC 2 CC7.1 vulnerability management is
easier when a customer can answer "what is in this" without asking.

Two things make this worth a rule rather than two workflow steps.

**A third image would silently ship without one.** `release.yml` pushes two
today. A new one added later gets built, tagged and pushed by whoever adds
it, and nothing would notice the missing SBOM until a customer asked. So
the check is: every image `release.yml` pushes has an SBOM step naming it.

**The portal needs two SBOMs and the reason is not obvious.** An image
scan of that container returns 71 components, all of them Alpine packages
— nginx and the built bundle. The 242 npm packages compiled *into* the
bundle appear nowhere, so a customer asking what JavaScript is in it would
have received a list of Alpine libraries. F40 found nine shipped npm
advisories, axios alone carrying 28, none of which an image scan of this
container can see.

Both numbers above were measured against real images before any workflow
was written, which is why the second SBOM exists at all.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_RELEASE = _ROOT / '.github/workflows/release.yml'
_CI = _ROOT / '.github/workflows/ci.yml'


def _workflow(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text())


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow['jobs'][job].get('steps') or []


def _pushed_image_names(release: dict) -> set[str]:
    """The last path segment of every image `release.yml` pushes.

    Read from the `run:` blocks rather than from a hardcoded list, because
    a hardcoded list is the thing this test exists to avoid being.
    """
    names: set[str] = set()
    for job in release['jobs'].values():
        for step in job.get('steps') or []:
            script = step.get('run') or ''
            if 'docker push' not in script:
                continue
            for match in re.finditer(r'IMAGE="[^"]*/([A-Za-z0-9._-]+)"', script):
                names.add(match.group(1))
    return names


class TestTheScanReadsTheWorkflows:
    """A rule that parses nothing passes forever. Two sweeps in this review
    looked like success while measuring nothing."""

    def test_both_workflows_parse(self):
        assert _workflow(_RELEASE)['jobs'], _RELEASE
        assert _workflow(_CI)['jobs'], _CI

    def test_it_finds_the_images_that_are_pushed(self):
        """That the scan parses *something*, not which images exist.

        This pinned the exact set, which made adding the embedder fail
        twice: once in the SBOM rule below — correctly, the image had
        none — and once here, where the only problem was that the list
        was written down in two places. The rule that matters is the
        one that compares pushes against SBOM steps; this one only has
        to prove the parse works, so it asserts the shape instead.
        """
        pushed = _pushed_image_names(_workflow(_RELEASE))

        assert pushed, (
            'no pushed image found in release.yml. If the build step changed shape, the '
            'SBOM check below is about to be measuring nothing.'
        )
        assert 'igni-api' in pushed, f'the API image is not among {sorted(pushed)}'


class TestEveryPushedImageHasAnSbom:
    def test_release_sboms_cover_every_image(self):
        release = _workflow(_RELEASE)
        pushed = _pushed_image_names(release)

        sbom_targets = ' '.join(
            str(step.get('with', {}).get('image-ref', ''))
            for job in release['jobs'].values()
            for step in job.get('steps') or []
            if str(step.get('with', {}).get('format', '')) == 'cyclonedx'
        )

        missing = sorted(name for name in pushed if name not in sbom_targets)

        assert not missing, (
            f'release.yml pushes these images with no SBOM step naming them: {missing}. '
            'A customer or a marketplace listing asking what is inside would get '
            'nothing for them.'
        )

    def test_the_sboms_are_tagged_with_the_commit(self):
        """An SBOM whose filename does not tie it to an image can drift
        from the image it describes, which makes it worse than none — it
        answers the question wrongly."""
        release = _RELEASE.read_text()

        for artefact in ('sbom-api-', 'sbom-portal-'):
            pattern = rf'name: {re.escape(artefact)}\$\{{\{{ steps\.meta\.outputs\.tag \}}\}}'
            assert re.search(
                pattern, release
            ), f'the {artefact} artefact is not named with the image tag, so it can drift from the image it describes'

    def test_the_format_is_a_standard_one(self):
        """CycloneDX or SPDX. A bespoke listing is not an SBOM as far as a
        marketplace review or a customer's tooling is concerned."""
        release = _workflow(_RELEASE)
        # Steps that *declare* a format. The `Upload ... SBOM` steps also
        # have "SBOM" in their names and no format at all, which the first
        # version of this collected as `''` and then rejected.
        formats = {
            str(step.get('with', {}).get('format'))
            for job in release['jobs'].values()
            for step in job.get('steps') or []
            if (step.get('with') or {}).get('format')
        }

        assert formats, 'no SBOM steps found at all'
        assert formats <= {'cyclonedx', 'spdx-json'}, formats


class TestThePortalHasTheOneThatIsEasyToMiss:
    """The npm SBOM. This is the finding inside CM-8 rather than the task."""

    def test_the_portal_has_a_source_scan_as_well_as_an_image_scan(self):
        release = _workflow(_RELEASE)
        steps = _steps(release, 'build-portal')

        scan_types = {str(s.get('with', {}).get('scan-type', '')) for s in steps if s.get('with')}

        assert 'image' in scan_types, 'no image SBOM for the portal'
        assert 'fs' in scan_types, (
            'the portal has no source SBOM. Its runtime image holds nginx and the '
            'built bundle, so an image scan reports 71 Alpine packages and none of '
            'the 242 npm packages compiled into that bundle — the ones F40 found '
            'nine shipped advisories in.'
        )

    def test_the_source_scan_skips_node_modules(self):
        """Otherwise it inventories the build tree rather than what ships,
        which is a different and much longer answer."""
        release = _RELEASE.read_text()

        assert 'skip-dirs: portal/node_modules' in release

    def test_ci_exercises_the_same_steps(self):
        """So a broken SBOM step is found on a pull request rather than
        during a release. The `bandit`-before-F42 shape: a step nobody runs
        until it matters."""
        ci = _workflow(_CI)
        names = [str(s.get('name', '')) for s in _steps(ci, 'image-scan')]

        assert any('SBOM' in n and 'backend' in n for n in names), names
        assert any('SBOM' in n and 'npm' in n for n in names), (
            'CI does not generate the portal npm SBOM, so the step that is easiest '
            'to get wrong is only ever run at release time'
        )


class TestTheDocumentationSaysWhereToFindThem:
    def test_the_compliance_tracker_points_at_the_artefacts(self):
        """An SBOM nobody can find is not evidence. CM-8 is satisfied by a
        customer being able to obtain one, not by CI producing a file."""
        tracker = (_ROOT / 'docs/COMPLIANCE_TRACKER.md').read_text()

        assert 'CM-8' in tracker
        row = next(line for line in tracker.splitlines() if line.startswith('| CM-8'))
        assert 'cyclonedx' in row.lower() or 'sbom' in row.lower(), row
        assert '`Gap`' not in row, 'CM-8 is still recorded as a gap'


@pytest.mark.parametrize('workflow', [_RELEASE, _CI], ids=['release', 'ci'])
def test_the_trivy_action_is_pinned(workflow):
    """An unpinned action is a supply-chain hole in the thing that exists
    to describe the supply chain."""
    text = workflow.read_text()
    uses = re.findall(r'uses: (aquasecurity/trivy-action@\S+)', text)

    assert uses, f'{workflow.name} has no trivy-action step'
    unpinned = [u for u in uses if u.endswith('@master') or u.endswith('@main')]
    assert not unpinned, unpinned
