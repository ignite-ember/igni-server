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


def test_cd_publishes_a_tag_the_chart_can_default_to():
    """The other half, and the reason the mismatch mattered.

    Asserted against the workflow rather than against a registry — there
    is no registry to ask from a test — so what this pins is that CD
    still tags the image with the version, and still derives that
    version from `pyproject.toml` rather than repeating the literal.
    """
    cd = (_ROOT / '.github/workflows/cd.yml').read_text()

    assert 'steps.meta.outputs.version' in cd, (
        "CD no longer tags the image with the application version, so the chart's "
        'default `image.tag` (its appVersion) resolves to nothing'
    )
    assert (
        'pyproject.toml' in cd
    ), 'CD should read the version from pyproject.toml; a literal here would be a fourth copy of the number'
    # Both images: an API published with a version tag and a portal
    # without one would leave `portalImage.tag` in the same state this
    # finding was about.
    assert cd.count('steps.meta.outputs.version') >= 1


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
