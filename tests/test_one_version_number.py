"""One version, in three files that all have to agree.

F1. Installing the chart on a real cluster turned up three different
answers to "what version is this":

* `pyproject.toml` — `0.6.0`
* `app/config.py` `app_version` — `0.5.0`, which is what `/version`
  reported and what `igni_build_info` labelled every metric with
* `charts/igni/Chart.yaml` `appVersion` — `0.1.0`

The third is not cosmetic. `values.yaml` says `image.tag` "defaults to
.Chart.AppVersion", and refuses `latest` on purpose — "it cannot be
rolled back to, and a tag that never changes gives Kubernetes no reason
to restart pods on upgrade". So a customer who installs the chart the
documented way, without pinning a tag, asks for `igni-api:0.1.0` — and
CD published the commit SHA and `latest` and nothing else. The default
install could only ever have been an `ImagePullBackOff`.

Two things follow, and this file holds both:

* the three numbers are one number;
* CD publishes an image tagged with it, so the chart's default resolves
  to something that exists.

The version tag is derived in CD from `pyproject.toml` rather than
written a fourth time, which is why the rule below reads the workflow
and not a constant.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    data = tomllib.loads((_ROOT / 'pyproject.toml').read_text())
    return str(data['project']['version'])


def _config_version() -> str:
    source = (_ROOT / 'app/config.py').read_text()
    match = re.search(r"^\s*app_version: str = '([^']+)'", source, re.M)
    assert match, 'could not find `app_version` in app/config.py'
    return match.group(1)


def _chart_app_version() -> str:
    source = (_ROOT / 'charts/igni/Chart.yaml').read_text()
    match = re.search(r'^appVersion: "([^"]+)"', source, re.M)
    assert match, 'could not find `appVersion` in charts/igni/Chart.yaml'
    return match.group(1)


def _image_builds() -> list[tuple[str, str]]:
    """Every step of the release workflow that builds an image, with the
    job it is in and the script it runs.

    The workflow builds two images — the API and the portal — and each is
    one `docker build`. The list is what the rule below is about: an
    image built without the version tag is an image the chart's default
    cannot resolve.
    """
    workflow = yaml.safe_load((_ROOT / '.github/workflows/release.yml').read_text())
    builds: list[tuple[str, str]] = []
    for job_name, job in workflow['jobs'].items():
        for step in job.get('steps') or []:
            run = step.get('run') or ''
            if 'docker build' in run:
                builds.append((job_name, run))
    return builds


def _jobs_building_images() -> dict[str, dict[str, str]]:
    """The jobs that build an image, with the script of their `meta` step
    and of their build steps.

    The `meta` step is the one that decides what the build is told: a step
    output nobody wrote is an empty string, not a missing value.
    """
    workflow = yaml.safe_load((_ROOT / '.github/workflows/release.yml').read_text())
    jobs: dict[str, dict[str, str]] = {}
    for job_name, job in workflow['jobs'].items():
        steps = job.get('steps') or []
        builds = [s.get('run') or '' for s in steps if 'docker build' in (s.get('run') or '')]
        if not builds:
            continue
        meta = '\n'.join(s.get('run') or '' for s in steps if s.get('id') == 'meta')
        jobs[job_name] = {'meta': meta, 'builds': builds}
    return jobs


# `echo "version=<something>" >> "$GITHUB_OUTPUT"` — the only way a step
# output is written.
_VERSION_OUTPUT = re.compile(r'echo\s+"?version=(?P<value>[^"\n]*)"?\s*>>\s*"\$GITHUB_OUTPUT"')

# A build that refuses an empty version: `if [ -z "${VERSION}" ]; then`.
_EMPTY_VERSION_GUARD = re.compile(r'if\s+\[\s*-z\s+"\$\{VERSION\}"\s*\]')


def test_the_three_versions_agree():
    """The rule. Bump one, bump all three."""
    versions = {
        'pyproject.toml': _pyproject_version(),
        'app/config.py': _config_version(),
        'charts/igni/Chart.yaml': _chart_app_version(),
    }
    assert len(set(versions.values())) == 1, (
        f"three files disagree about the version: {versions}. The chart's appVersion is "
        'the default image tag, so a mismatch there is an install that cannot pull.'
    )


def test_the_version_looks_like_one():
    """A regex that matched an empty string would make the test above
    pass on three empty strings."""
    assert re.fullmatch(r'\d+\.\d+\.\d+', _pyproject_version()), _pyproject_version()


def test_the_release_publishes_a_tag_the_chart_can_default_to():
    """The other half, and the reason the mismatch mattered.

    Asserted against the workflow rather than against a registry — there
    is no registry to ask from a test — so what this pins is that the
    release still tags the image with the version, and still derives that
    version from `pyproject.toml` rather than repeating the literal.
    """
    release = (_ROOT / '.github/workflows/release.yml').read_text()

    assert 'steps.meta.outputs.version' in release, (
        "the release no longer tags the image with the application version, so the chart's "
        'default `image.tag` (its appVersion) resolves to nothing'
    )
    assert (
        'pyproject.toml' in release
    ), 'the release should read the version from pyproject.toml; a literal here would be a fourth copy of the number'
    # Both images: an API published with a version tag and a portal
    # without one would leave `portalImage.tag` in the same state this
    # finding was about. The rule is per build, not per workflow: the
    # workflow holds the version in two places (the API's and the
    # portal's) and the portal's was missing, which a count over the
    # whole file cannot see.
    builds = _image_builds()
    assert len(builds) >= 2, (
        'the release workflow should build the API and the portal images; '
        f'found {len(builds)} docker build step(s)'
    )
    for job_name, run in builds:
        assert 'steps.meta.outputs.version' in run, (
            f'the image built in job {job_name!r} is not tagged with the application version, '
            "so the chart's default tag for it resolves to an image nothing ever pushed"
        )


def test_every_image_build_gets_its_version_from_its_own_meta_step():
    """The gap that let the portal break, and the reason the rule above
    was not enough.

    The rule above asks whether the build script *mentions*
    `steps.meta.outputs.version`. It cannot see whether the job's `meta`
    step ever *wrote* it — and a step output nobody wrote is not a
    missing value, it is an empty string. The portal's build said
    `-t "${IMAGE}:${{ steps.meta.outputs.version }}"` and its `meta` step
    wrote only `tag=`, so the image was tagged `igni-portal:` and the
    build died with `invalid tag "ghcr.io/ignite-ember/igni-portal:"`
    before anything was pushed.

    So the rule is per job: a job that builds an image tagged with the
    version must have a `meta` step that writes it.
    """
    jobs = _jobs_building_images()
    assert jobs, 'no job in the release workflow builds an image'

    for job_name, job in jobs.items():
        assert any('steps.meta.outputs.version' in run for run in job['builds']), (
            f'job {job_name!r} builds an image but never tags it with the application version'
        )
        match = _VERSION_OUTPUT.search(job['meta'])
        assert match, (
            f'job {job_name!r} tags its image with steps.meta.outputs.version, but its meta step '
            'never writes `version=` to $GITHUB_OUTPUT — the output is empty and the build dies '
            'with "invalid tag"'
        )
        assert match.group('value').strip(), (
            f'job {job_name!r} writes an empty version to $GITHUB_OUTPUT'
        )


def test_a_build_refuses_an_empty_version_instead_of_tagging_nothing():
    """The second half, because the rule above is about the workflow file
    and this one is about what the registry receives.

    An empty version is a tag of `igni-portal:` and a build that dies
    with an invalid reference — which is what the release of 0.7.0 did.
    It is a clearer failure than a tag that names nothing, and it is
    cheap: one check, before the build.
    """
    for job_name, job in _jobs_building_images().items():
        for run in job['builds']:
            assert _EMPTY_VERSION_GUARD.search(run), (
                f'job {job_name!r} builds an image tagged with the version but does not refuse an '
                'empty one, so a missing version reaches the registry as a tag of nothing'
            )


def test_the_chart_still_refuses_latest():
    """The reason the default tag is a version rather than `latest`.

    If somebody 'fixes' this finding by allowing `latest`, the chart
    stops being rollback-able and Kubernetes stops having a reason to
    restart pods on upgrade — which is what the refusal is for.
    """
    values = (_ROOT / 'charts/igni/values.yaml').read_text()
    assert 'latest' in values, 'values.yaml no longer explains why `latest` is refused'

    helpers = (_ROOT / 'charts/igni/templates/_helpers.tpl').read_text()
    assert 'latest' in helpers, 'the refusal of `latest` is no longer implemented'


def test_the_version_has_a_changelog_entry_and_release_notes():
    """A release is the version, its CHANGELOG.md section and its notes
    (docs/RELEASING.md). Bumping the number without them is how a tag
    ends up with nothing to say what it holds."""
    version = _pyproject_version()
    changelog = (_ROOT / 'CHANGELOG.md').read_text()

    assert '## [Unreleased]' in changelog, 'CHANGELOG.md has no [Unreleased] section to collect the next changes in'
    assert re.search(
        rf'^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$', changelog, re.M
    ), f'CHANGELOG.md has no dated section for {version}'
    assert (
        _ROOT / 'docs' / 'releases' / f'v{version}.md'
    ).is_file(), f'no release notes at docs/releases/v{version}.md'
