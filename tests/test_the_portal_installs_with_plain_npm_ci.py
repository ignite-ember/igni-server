"""The portal installs with a plain `npm ci`.

It did not: `@vitest/ui` 4.1.6 pins its peer `vitest` to exactly 4.1.6,
and the lock had `vitest` 4.1.12, so `npm ci` stopped with ERESOLVE. CI
never saw it, because every install in CI, the portal's Dockerfile and
the baselines script ran `npm ci --legacy-peer-deps` — from the portal's
first commit — which skips peer dependencies altogether: the conflict,
and also the `monaco-editor` that `@monaco-editor/react` declares as its
peer. A developer following the README got the error; the real-model
rerun installed with the flag to get past it. B33.

Read from the lock, so it runs without the network: what `npm ci` checks
about peers, for the cases this lock has — an exact pin is met exactly,
and a required peer is in the lock at all. And nothing installs with the
flag again, so the next conflict fails CI rather than a developer.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LOCK = json.loads((_ROOT / 'portal/package-lock.json').read_text())
_PACKAGES: dict[str, dict] = _LOCK['packages']
_EXACT = re.compile(r'^\d+\.\d+\.\d+$')


def _resolve(name: str, at: str) -> dict | None:
    """The lock entry Node would find for *name*, required from the package at *at*."""
    directory = at
    while True:
        candidate = f'{directory}/node_modules/{name}' if directory else f'node_modules/{name}'
        if candidate in _PACKAGES:
            return _PACKAGES[candidate]
        if not directory:
            return None
        # Up out of this package and the node_modules holding it.
        parent = directory.rsplit('/node_modules/', 1)[0] if '/node_modules/' in directory else ''
        directory = parent


def _required_peers():
    for path, entry in _PACKAGES.items():
        if not path:
            continue
        optional = entry.get('peerDependenciesMeta', {})
        for name, wanted in entry.get('peerDependencies', {}).items():
            if not optional.get(name, {}).get('optional'):
                yield path, name, wanted


def test_every_exact_peer_pin_is_met():
    """The failure itself: `@vitest/ui` wanting a `vitest` the lock does not have."""
    unmet = [
        f'{path} wants {name} {wanted}, the lock has {found.get("version")}'
        for path, name, wanted in _required_peers()
        if _EXACT.match(wanted) and (found := _resolve(name, path)) is not None and found.get('version') != wanted
    ]
    assert not unmet, unmet


def test_every_required_peer_is_in_the_lock():
    """`npm ci` without the flag installs peers, and refuses a lock that lacks one."""
    missing = [
        f'{path} wants {name} {wanted}' for path, name, wanted in _required_peers() if _resolve(name, path) is None
    ]
    assert not missing, missing


_INSTALLERS = [
    *sorted((_ROOT / '.github/workflows').glob('*.yml')),
    _ROOT / 'portal/Dockerfile',
    *sorted((_ROOT / 'portal/scripts').glob('*.sh')),
]


@pytest.mark.parametrize('where', _INSTALLERS, ids=lambda p: str(p.relative_to(_ROOT)))
def test_nothing_installs_past_a_peer_conflict(where):
    """The flag is how this went unseen: it makes every conflict install."""
    installs = [line.strip() for line in where.read_text().splitlines() if re.search(r'\bnpm (ci|install)\b', line)]
    flagged = [line for line in installs if '--legacy-peer-deps' in line or '--force' in line]
    assert not flagged, flagged
