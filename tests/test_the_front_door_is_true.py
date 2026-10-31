"""The README is the install instructions, so its claims are testable.

For a self-hosted product the README *is* the product's first five
minutes, and this one had rotted in every direction at once:

* four of six links in its documentation table pointed at files that do
  not exist, plus a `CODEINDEX.md` at the repository root
* three of the five endpoints it documented
  (`/v1/repositories/{id}/commits`, `/v1/commits/{id}`,
  `/v1/commits/{id}/processing-status`) are not routes
* `make run`, the last step of its Quick Start, was not a target
* it described the worker as "Celery + gevent (100 concurrency)" when
  both compose files ran the custom AsyncIO pool at 200 — a row corrected
  once, then removed entirely by DEC-13, which is the better fix for a
  description of a process serving a queue nothing wrote to
* it said the prod compose file "declares exactly one volume and it is
  Flower's" while that file declared `pgdata` and `redisdata`. Two
  separate changes made it wrong and neither came back to the paragraph,
  which is what `TestTheStateItClaimsToKeep` is for: the list of durable
  volumes is now read out of the compose file
* its environment table omitted the one variable without which the
  server refuses to start — then `JWT_SECRET_KEY`, now
  `SECRET_ENCRYPTION_KEY`

None of that fails a build, which is exactly why it accumulated. These
tests check the mechanically checkable half — links, targets, settings
names, route paths — so the next drift is caught by CI rather than by a
customer.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_README = _ROOT / 'README.md'

_MARKDOWN = [_README, *sorted((_ROOT / 'docs').glob('*.md'))]


def _links(doc: pathlib.Path) -> list[str]:
    """Relative markdown link targets, anchors and URLs excluded."""
    out = []
    for target in re.findall(r'\]\(([^)]+)\)', doc.read_text()):
        target = target.split('#', 1)[0].strip()
        if not target or target.startswith(('http://', 'https://', 'mailto:')):
            continue
        out.append(target)
    return out


def _mounted_paths(router, prefix: str = '') -> set[str]:
    """Every path the app actually serves, prefixes resolved.

    `app.routes` is not a flat list: FastAPI 0.141 puts a lazy
    `_IncludedRouter` in it per `include_router` call and resolves the
    real routes on demand, so a naive comprehension over it sees `/docs`
    and `/health` and nothing else — including nothing under `/v1`. The
    caller asserts a known path is present, because a walk that silently
    returned the wrong shape would make every check below vacuous.
    """
    found: set[str] = set()
    for route in getattr(router, 'routes', []):
        inner = getattr(route, 'original_router', None)
        if inner is not None:
            context = getattr(route, 'include_context', None)
            found |= _mounted_paths(inner, prefix + (getattr(context, 'prefix', '') or ''))
        elif getattr(route, 'path', None):
            found.add(prefix + route.path)
    return found


@pytest.mark.parametrize('doc', _MARKDOWN, ids=lambda d: d.name)
def test_every_relative_link_resolves(doc):
    base = doc.parent
    broken = [t for t in _links(doc) if not (base / t).exists()]

    assert not broken, f'{doc.name} links to files that do not exist: {broken}'


class TestTheStateItClaimsToKeep:
    """ "Data you own" is the section a customer plans backups from (F143).

    It listed one volume and named the wrong one. The bundled Postgres
    and Redis profile added two real ones and the paragraph was never
    revisited, so the README understated what there is to back up — the
    direction that costs a customer data rather than time.

    So the volumes are derived. A compose file that grows a volume fails
    this until the README names it.
    """

    def _prod_volumes(self) -> set[str]:
        """Top-level `volumes:` keys, which is what `docker volume ls`
        would show. Read with a small parser rather than yaml.safe_load
        so a null-valued key (`pgdata:` with nothing under it, the normal
        way to declare one) does not need special handling."""
        text = (_ROOT / 'containers/docker-compose.prod.yml').read_text()
        block = text.split('\nvolumes:\n', 1)
        assert len(block) == 2, 'the prod compose file has no top-level volumes section'
        names = set()
        for line in block[1].splitlines():
            if line and not line.startswith((' ', '\t')):
                break
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                names.add(stripped.rstrip(':'))
        return names

    def test_the_parser_found_the_volumes(self):
        """A comparison against an empty set would pass everything."""
        found = self._prod_volumes()
        assert found, 'parsed no volumes, so the checks below prove nothing'
        assert 'pgdata' in found, sorted(found)

    def test_the_readme_names_every_volume_the_prod_stack_declares(self):
        readme = _README.read_text()
        unnamed = sorted(v for v in self._prod_volumes() if v not in readme)

        assert not unnamed, (
            f'the prod compose file declares volumes the README does not mention: {unnamed}. '
            'A customer plans backups from that section.'
        )

    def test_it_no_longer_claims_a_single_volume(self):
        """The specific false sentence, pinned. A rule that only counted
        would pass a rewrite that kept the word "one".
        """
        prose = ' '.join(_README.read_text().split())
        assert (
            'exactly one volume and it is Flower' not in prose or 'read "exactly one' in prose
        ), 'the README asserts the one-volume claim again'


class TestTheUpgradeStopsWhatAReleaseRemoved:
    """F146. DEC-13 removed the worker and Flower from the compose files,
    and on the machine that change was written both containers were still
    running days later. Compose only manages services the current file
    declares: ``up`` will not recreate a removed one and ``down`` will not
    stop it. Only ``--remove-orphans`` does, so the upgrade the README
    tells a customer to run has to carry it."""

    def test_the_upgrade_command_removes_orphans(self):
        readme = _README.read_text()
        upgrade = readme[readme.index('**Upgrading') :]
        command = re.search(r'docker compose [^\n]*\bup\b[^\n]*', upgrade)

        assert command, 'the README has no upgrade command under **Upgrading**'
        assert '--remove-orphans' in command.group(0), (
            f'the upgrade command does not remove orphans: {command.group(0)!r}. A service a '
            'release removed keeps running against the database until somebody notices'
        )


class TestTheQuickStartWorks:
    def test_every_make_target_the_readme_names_exists(self):
        makefile = (_ROOT / 'Makefile').read_text()
        rules = set(re.findall(r'^([a-zA-Z][\w-]*):', makefile, re.M))

        named = set(re.findall(r'`?make ([a-z][a-z-]*)`?', _README.read_text()))
        missing = sorted(named - rules)

        assert not missing, f'README documents targets that do not exist: {missing}'

    def test_phony_and_the_real_rules_agree(self):
        """`.PHONY` declared eight targets that had no rules — including all
        four `*-prod` ones — while omitting two that did. It does not create
        rules, so `make up-prod` failed with "No rule to make target"; the
        list was a record of intentions."""
        makefile = (_ROOT / 'Makefile').read_text()
        block = re.search(r'^\.PHONY:((?:.*\\\n)*.*)$', makefile, re.M).group(1)
        phony = set(block.replace('\\', '').split())
        rules = set(re.findall(r'^([a-zA-Z][\w-]*):', makefile, re.M))

        assert not phony - rules, f'.PHONY declares targets with no rule: {sorted(phony - rules)}'
        assert not rules - phony, f'targets missing from .PHONY: {sorted(rules - phony)}'

    def test_the_documented_port_is_the_one_the_app_uses(self):
        """`PORT ?= 8080` was referenced by nothing, so the single place a
        reader could look up the port gave the wrong number."""
        makefile = (_ROOT / 'Makefile').read_text()
        port = re.search(r'^PORT \?= (\d+)', makefile, re.M)

        assert port and port.group(1) == '7777', (
            'the Makefile PORT should match the port every compose file, the '
            "Dockerfile's EXPOSE and both health checks use"
        )


class TestItDocumentsRealSettings:
    def test_every_environment_variable_named_is_a_real_setting(self):
        """A documented variable that is not a settings field does nothing
        at all when an operator sets it — the failure is silent and looks
        like the feature is broken."""
        from app.config import Settings

        fields = set(Settings.model_fields)
        # Table rows look like: | `POSTGRES_HOST` / `POSTGRES_PORT` | ... |
        rows = re.findall(r'^\| ((?:`[A-Z][A-Z0-9_]*`(?:\s*/\s*)?)+) \|', _README.read_text(), re.M)
        named = {v for row in rows for v in re.findall(r'`([A-Z][A-Z0-9_]*)`', row)}

        assert named, 'no environment table found — did the README change shape?'
        # Nested settings use `AI__FOO`; those map to a submodel, not a
        # top-level field, so they are checked by prefix.
        unknown = sorted(v for v in named if v.lower() not in fields and v.split('__')[0].lower() not in fields)

        assert not unknown, f'README documents variables that are not settings: {unknown}'

    def test_the_variable_without_which_nothing_works_is_documented(self):
        """`JWT_SECRET_KEY` was once absent from the table, and the server
        refused to start without it outside dev, so an operator following
        the README hit a hard failure the README never mentioned. That
        setting is gone; `SECRET_ENCRYPTION_KEY` is now the one the server
        refuses to start without, in every environment, and
        `PUBLIC_BASE_URL` the one it refuses outside dev unless https."""
        readme = _README.read_text()

        assert 'SECRET_ENCRYPTION_KEY' in readme
        assert 'PUBLIC_BASE_URL' in readme


class TestItDocumentsRealRoutes:
    def test_every_endpoint_path_quoted_in_the_readme_exists(self):
        from tests.conftest import the_app

        app = the_app()

        # Every mounted path, not just the documented ones. The question
        # this asks is "does the path exist", and the router is the
        # honest answer: it includes what is deliberately kept out of the
        # schema — `/docs`, `/openapi.json`, `/metrics` — which were
        # otherwise a hardcoded exclusion list that grew by one every
        # time the README mentioned another of them.
        real = set(app.openapi()['paths']) | _mounted_paths(app)
        assert '/metrics' in real, (
            'the walk below found no /metrics, so it is either unmounted or the router '
            'internals changed shape and this check has stopped measuring anything'
        )

        quoted = set(re.findall(r'`(/(?:v1/)?[a-z0-9][\w/{}.-]*)`', _README.read_text()))
        # Prefix groups and shorthand are illustrative, not paths.
        concrete = {p for p in quoted if '*' not in p and '{,' not in p and not p.endswith('/')}

        assert concrete, 'no endpoint paths found — did the README change shape?'
        missing = sorted(p for p in concrete if p not in real)

        assert not missing, f'README documents routes that do not exist: {missing}'
