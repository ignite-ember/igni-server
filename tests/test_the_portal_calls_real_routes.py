"""Every API path the portal requests must be served, with that method.

The portal is a React app in this repository talking to the API over
HTTP, and the two are checked against each other by nothing. A renamed
route breaks a page at runtime: the request 404s, the page shows an
error or a blank panel, `tsc` is happy, the Python suite is happy, and
the portal's own tests mock the client.

That is the FE↔BE drift class, and it has cost twice in two days
elsewhere. In the igni CLI, the wire-contract snapshot was two months
stale and unregenerable, so the check that existed for exactly this was
validating a July fossil. In this repository, `README.md` documented
five routes that were not routes (F144).

**Nothing was wrong when this was written.** All 46 paths the portal
calls are served, with the methods it uses. The value is in the next
rename, not in this run — which is the same reason the cross-org sweeps
exist.

## How the paths are found

`client.get('/portal/access')`, and `client` prepends `/v1`. Template
interpolation becomes a path parameter: `` `/portal/repositories/${id}` ``
is `/v1/portal/repositories/{}`, matched against the router's
`/v1/portal/repositories/{repository_id}` with parameter names
normalised away, because the two sides have no reason to agree on
spelling.

A query string is dropped — `?since=` is not part of a route — and that
detail is the one thing this got wrong first: the initial version
reported `/v1/portal/audit-events?{}` as unserved, which is a real route
with parameters attached.

## What it cannot see

A path assembled from variables rather than written as a literal. The
regex needs the string to start in the call, so
`client.get(endpointFor(x))` is invisible. The count assertion below is
what keeps that from quietly becoming most of them.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PORTAL = _ROOT / 'portal/src'

#: `client.get<Type>('/path')` and friends. The method is captured
#: because calling a GET-only route with POST is a 405 the portal
#: cannot recover from, and it is as easy to check as the path.
_CALL = re.compile(r"""client\.(get|post|patch|put|delete|del)\s*(?:<[^>]*>)?\s*\(\s*[`'"]([^`'"]+)""")


def _normalise(path: str) -> str:
    """A comparable route template.

    Parameter names are erased on both sides — the portal writes
    `${repositoryId}` and the router writes `{repository_id}`, and
    neither is more right.
    """
    path = re.sub(r'\$\{[^}]*\}', '{}', path)
    path = path.split('?', 1)[0]
    path = re.sub(r'\{[^}]*\}', '{}', path)
    return path.rstrip('/') or '/'


def _served() -> set[tuple[str, str]]:
    """`(METHOD, template)` for everything the API mounts."""
    from tests.conftest import the_app

    app = the_app()
    out: set[tuple[str, str]] = set()

    def walk(router, prefix=''):
        for route in getattr(router, 'routes', []):
            inner = getattr(route, 'original_router', None)
            if inner is not None:
                context = getattr(route, 'include_context', None)
                walk(inner, prefix + (getattr(context, 'prefix', '') or ''))
            elif getattr(route, 'path', None):
                for method in getattr(route, 'methods', None) or {'GET'}:
                    out.add((method.upper(), _normalise(prefix + route.path)))

    walk(app)
    return out


def _called() -> dict[tuple[str, str], set[str]]:
    """`(METHOD, template) -> {files}` for what the portal requests of this API."""
    out: dict[tuple[str, str], set[str]] = {}
    for source in sorted([*_PORTAL.rglob('*.ts'), *_PORTAL.rglob('*.tsx')]):
        if '__tests__' in str(source):
            continue
        for verb, path in _CALL.findall(source.read_text()):
            method = 'DELETE' if verb == 'del' else verb.upper()
            key = (method, _normalise('/v1' + path))
            out.setdefault(key, set()).add(str(source.relative_to(_ROOT)))
    return out


class TestBothSidesWereFound:
    """Two derived sets, so both need a floor. A comparison of nothing
    against nothing passes, which is the failure this suite keeps
    finding — including twice in the day this file was written."""

    def test_the_portal_source_is_there(self):
        assert _PORTAL.is_dir(), _PORTAL
        assert len(list(_PORTAL.rglob('*.tsx'))) > 30

    def test_calls_were_extracted(self):
        called = _called()

        assert len(called) >= 40, sorted(called)
        assert ('GET', '/v1/portal/team') in called, sorted(called)[:10]

    def test_routes_were_extracted(self):
        served = _served()

        assert len(served) >= 100, len(served)
        assert ('GET', '/v1/portal/team') in served

    def test_the_normaliser_makes_the_two_spellings_meet(self):
        """The portal writes `${repositoryId}`; the router writes
        `{repository_id}`. Neither is more right, so both become `{}`."""
        assert _normalise('/portal/repositories/${repositoryId}') == '/portal/repositories/{}'
        assert _normalise('/v1/portal/repositories/{repository_id}') == '/v1/portal/repositories/{}'
        assert _normalise('/v1/portal/audit-events?since=x') == '/v1/portal/audit-events'


def test_every_path_the_portal_calls_is_served():
    served = _served()
    unserved = {key: files for key, files in _called().items() if key not in served}

    assert not unserved, (
        'the portal requests these and the API does not serve them: '
        + '; '.join(f'{m} {p} (from {sorted(f)[0]})' for (m, p), f in sorted(unserved.items()))
        + '. Each is a page that 404s at runtime with nothing failing beforehand.'
    )


def test_no_path_is_called_with_a_method_the_route_refuses():
    """A 405, which reads to a user as the feature being broken.

    Separate from the test above because the diagnosis differs: there
    the route is gone, here it exists and the verb is wrong, and the
    second is much easier to introduce by copying a nearby call.
    """
    served = _served()
    paths_served = {path for _, path in served}
    wrong: dict[str, str] = {}

    for (method, path), files in _called().items():
        if path in paths_served and (method, path) not in served:
            allowed = sorted(m for m, p in served if p == path)
            wrong[f'{method} {path}'] = f'route allows {allowed}, called from {sorted(files)[0]}'

    assert not wrong, wrong


@pytest.mark.parametrize('page', sorted(p.stem for p in (_PORTAL / 'pages').glob('*.tsx')))
def test_every_page_that_calls_the_api_calls_something_real(page):
    """Per page, so a failure names the screen that breaks.

    The aggregate test above says *a* path is wrong; this says which
    page a user would find broken, which is the first question anybody
    asks.
    """
    source = (_PORTAL / 'pages' / f'{page}.tsx').read_text()
    served = _served()
    unserved = []
    for verb, path in _CALL.findall(source):
        method = 'DELETE' if verb == 'del' else verb.upper()
        key = (method, _normalise('/v1' + path))
        if key not in served:
            unserved.append(f'{key[0]} {key[1]}')

    assert not unserved, f'{page} requests {unserved}, which the API does not serve'
