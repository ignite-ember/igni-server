"""The bundle job survives a runner whose apt is broken.

F2. The release's last job — `offline-bundle` — installs skopeo with
`sudo apt-get update && sudo apt-get install -y --no-install-recommends
skopeo` before it mirrors the third-party images. On 2026-11-11 the
Azure-hosted runners could not resolve `azure.archive.ubuntu.com`, so
every host apt on them failed: the job died on a tool it needs, after
every image had been built and signed. The apt-free fallback is the
image `quay.io/skopeo/stable` publishes — skopeo's own GitHub releases
carry no binary, only source — and the binary is copied out of it.

The rules below are about the fallback, not about skopeo:

* the apt path is still tried first (a working runner should not reach
  for a container image it does not need);
* the image is pinned by digest, so the fallback is the same bytes every
  time — a tag would make the release's reproducibility depend on what
  quay.io serves that day;
* the binary is copied out of the image at the path the image puts it
  at, and installed where the rest of the job finds it;
* the step ends by running skopeo, so a fallback that did not work
  fails here and not in the mirror job.

Asserted against the workflow, as `test_one_version_number.py` does:
there is no runner here to fail, and the rule is about what the
workflow says.
"""

from __future__ import annotations

import pathlib
import re

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_WORKFLOW = _ROOT / '.github/workflows/release.yml'


def _jobs() -> dict:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    return workflow['jobs']


def _skopeo_step() -> tuple[str, dict]:
    """The step that installs skopeo, and the job it is in."""
    for job_name, job in _jobs().items():
        for step in job.get('steps') or []:
            if step.get('name') == 'Install skopeo':
                return job_name, step
    raise AssertionError('the release workflow has no step named "Install skopeo"')


def _skopeo_step_run() -> str:
    return _skopeo_step()[1].get('run') or ''


def test_the_job_that_mirrors_the_images_is_the_one_that_installs_skopeo():
    """The install belongs to the job that needs it.

    A step that installs skopeo in a job that never calls it, while the
    mirroring job calls it and does not install it, is the shape this
    finding took in other guises.
    """
    install_job, _ = _skopeo_step()
    callers = []
    for job_name, job in _jobs().items():
        for step in job.get('steps') or []:
            run = step.get('run') or ''
            if re.search(r'(?<![\w/-])skopeo\s+(copy|inspect|manifest-digest)', run):
                callers.append(job_name)
    assert install_job in callers, (
        f'the skopeo install is in job {install_job!r}, but the mirroring happens in {callers!r}; '
        'a job that calls skopeo without installing it dies on the same broken apt'
    )


def test_the_apt_path_is_still_tried_first():
    """A working runner should not reach for a container image."""
    run = _skopeo_step_run()
    assert 'apt-get install' in run, 'the step no longer installs skopeo with apt'
    assert re.search(r'if ! \(sudo apt-get update', run), (
        'the apt path is not the one tried first; the fallback should run only when apt fails'
    )


def test_the_fallback_image_is_pinned_by_digest():
    """A tag would make the release depend on what quay.io serves that
    day; the digest pins the bytes."""
    step = _skopeo_step()[1]
    image = (step.get('env') or {}).get('SKOPEO_IMAGE') or ''
    assert image.startswith('quay.io/skopeo/stable@'), (
        f'the fallback image is {image!r}; it should be quay.io/skopeo/stable@<digest>'
    )
    assert re.search(r'@sha256:[0-9a-f]{64}$', image), (
        f'the fallback image is not pinned by digest: {image!r}'
    )


def test_the_binary_is_taken_out_of_the_image_and_installed_where_the_job_looks():
    """The copy path is the image's own layout, and the install path is
    on the runner's PATH."""
    run = _skopeo_step_run()
    assert 'docker cp' in run and '/usr/bin/skopeo' in run, (
        'the fallback does not copy the binary out of the image at the path the image puts it at'
    )
    assert re.search(r'install -m 0755\s+\S+\s+/usr/local/bin/skopeo', run), (
        'the fallback does not install the binary where the rest of the job finds it'
    )


def test_the_step_ends_by_running_skopeo():
    """A fallback that did not work has to fail here, in the step that
    made it, and not three jobs later in the mirror."""
    run = _skopeo_step_run().rstrip()
    assert run.endswith('skopeo --version'), (
        'the install step does not end by running skopeo; a broken install would be '
        'found by the mirror job instead of here'
    )
