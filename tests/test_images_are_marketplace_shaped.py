"""The published images must not run as root.

Distribution is the GCP and AWS marketplaces. GCP's container requirements
forbid running as root outright, and every image scanner an AWS listing
goes through flags it. Both images did:

    docker run --rm <backend> id -u   → 0
    docker run --rm <portal>  id -u   → 0

Asserted on the Dockerfiles rather than by running the images, because
these tests run wherever `pytest` runs and a Docker daemon is not
guaranteed. The behaviour was verified by hand when the change was made —
backend `id -u` → 10001, portal → 101 for every process including the
nginx master, and the portal still answered 200 with its `index.html`.

**The numeric USER matters more than it looks.** Kubernetes'
`runAsNonRoot: true` inspects the image's configured user and refuses to
start a container whose user it cannot prove is non-zero. A *named* user
fails with "container has runAsNonRoot and image has non-numeric user",
so a marketplace Kubernetes listing would be rejected by a `USER igni`
that works perfectly under Docker.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / 'containers/be/Dockerfile'
_PORTAL = _ROOT / 'portal/Dockerfile'

_UNPRIVILEGED_BASES = ('nginxinc/nginx-unprivileged',)


@pytest.mark.parametrize('path', [_BACKEND, _PORTAL], ids=lambda p: str(p.relative_to(_ROOT)))
def test_the_dockerfile_exists(path):
    """A regex against a missing file finds nothing and passes."""
    assert path.exists(), path


def test_the_backend_runs_as_a_numeric_non_root_user():
    text = _BACKEND.read_text()
    users = re.findall(r'^USER\s+(\S+)', text, re.M)

    assert users, 'no USER directive — the image runs as root'
    final = users[-1]
    assert final.isdigit(), (
        f'USER is {final!r}. Kubernetes runAsNonRoot cannot verify a named user and '
        'refuses to start the container; use the numeric uid.'
    )
    assert int(final) != 0, 'USER 0 is root'


def test_the_backend_creates_the_account_it_switches_to():
    """A `USER 10001` with no matching account leaves the uid nameless, with
    no home and no shell — which some libraries assume exist."""
    text = _BACKEND.read_text()
    uid = re.findall(r'^USER\s+(\d+)', text, re.M)[-1]

    assert re.search(rf'useradd[^\n]*--uid\s+{uid}\b', text), f'uid {uid} is switched to but never created'


def test_every_named_volume_mounted_into_our_image_is_writable_by_its_user():
    """A named volume inherits the ownership of the image directory it
    covers, so mounting one over a path owned by root leaves the non-root
    user unable to write it.

    This was written as "the Flower state directory is chowned to uid
    10001", which was true and is now a rule about a path DEC-13 deleted.
    Restated as the rule it always was: every named-volume target inside
    a service running our image needs the chown.

    **There are no such mounts today**, and the assertion below is the
    guard on that: it proves the scan found the compose files and the
    services running our image, so an empty offender list means empty
    rather than unmeasured. The check exists to fire when somebody adds
    the first one back — which is precisely when the ownership question
    gets forgotten, because it worked on their machine as root.
    """
    import yaml

    text = _BACKEND.read_text()
    uid = re.findall(r'^USER\s+(\d+)', text, re.M)[-1]

    scanned, offenders = [], []
    for name in ('docker-compose.prod.yml', 'docker-compose.local.yml'):
        compose = yaml.safe_load((_ROOT / 'containers' / name).read_text())
        declared = set(compose.get('volumes') or {})
        for service, definition in (compose.get('services') or {}).items():
            image = str(definition.get('image') or '')
            if 'IGNI_API_IMAGE' not in image and 'igni-api' not in image:
                continue
            scanned.append(f'{name}:{service}')
            for mount in definition.get('volumes') or []:
                source, _, target = str(mount).partition(':')
                target = target.split(':')[0]
                if source in declared and not re.search(rf'chown\s+{uid}:\S*\s+{re.escape(target)}', text):
                    offenders.append(f'{name}:{service} mounts {source} at {target}')

    assert scanned, (
        'found no service running our image in either compose file, so this rule measured '
        'nothing — the image reference or the file names have changed'
    )
    assert not offenders, f'{offenders} — the volume will be root-owned and uid {uid} cannot write it'


def test_the_portal_uses_an_unprivileged_base_image():
    """The stock `nginx` image starts its master as root. Swapping the base
    is the fix rather than adding a `USER` line, because the unprivileged
    variant also owns `/var/cache/nginx`, `/var/run` and the config
    directories as the runtime user — which is the actual work."""
    text = _PORTAL.read_text()
    froms = re.findall(r'^FROM\s+(\S+)', text, re.M)

    serving_base = froms[-1]
    assert serving_base.startswith(
        _UNPRIVILEGED_BASES
    ), f'the serving stage is {serving_base!r}; the stock nginx image runs its master process as root'


def test_the_portal_listens_above_1024():
    """An unprivileged process cannot bind a privileged port, so the base
    image swap only works because the config already listens on 3000. If
    that ever moves to 80 the container stops starting."""
    conf = (_ROOT / 'portal/nginx.conf').read_text()
    ports = [int(p) for p in re.findall(r'^\s*listen\s+(\d+)', conf, re.M)]

    assert ports, 'no listen directive found'
    assert all(p > 1024 for p in ports), ports


@pytest.mark.parametrize(
    'path,image',
    [(_BACKEND, 'server'), (_PORTAL, 'portal')],
    ids=['backend', 'portal'],
)
def test_the_image_says_what_it_is(path, image):
    """Both marketplaces expect an image to carry its own provenance, and
    scanners and `docker inspect` read these labels."""
    text = path.read_text()

    for label in (
        'org.opencontainers.image.title',
        'org.opencontainers.image.source',
        'org.opencontainers.image.vendor',
    ):
        assert label in text, f'{path.name} has no {label}'
    assert f'igni {image}' in text, f'{path.name} does not name itself'
