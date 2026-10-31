"""Nothing this application imports may arrive by accident.

SQLAlchemy was imported by forty-nine modules and required by nothing.
It installed because alembic happens to depend on it — so the ORM this
entire application is built on was a transitive dependency of a
migration tool, and would have stopped installing the day alembic
changed its mind.

That is the hazard `prometheus-client` was made a direct dependency to
avoid, and its note says so in as many words: "relying on a transitive
dependency for an endpoint a customer's monitoring scrapes is how it
disappears in an unrelated upgrade". The same reasoning had not been
applied to the database layer.

So the rule is checked rather than remembered, in the direction that
failed: **every third-party module imported by shipped code must be
named in `pyproject.toml`.**

## The other direction is not enforced here

Ten runtime dependencies are declared and never imported. Four are
legitimate — `asyncpg` and `psycopg2-binary` are named in connection
URLs rather than imported, `greenlet` is SQLAlchemy's async bridge, and
`pydantic-core` is pinned in lockstep with pydantic. The rest
(`aiohttp`, `aioredis`, `gevent`, `python-multipart`, `python-socketio`,
`tenacity`) are open questions, not obvious removals: `python-socketio`
is the AI-IDE surface two documents still describe, and deciding its
future is a product question rather than a lint.

`igni__artifacts` enforces both directions, because that repository had
the luxury of starting from a list somebody had just audited. When this
one's unused entries have each been decided on, the same rule can come
back here. Writing it now would mean a keep-list of six entries whose
reason is "not looked at yet", which is a suppression wearing a rule's
clothes.
"""

from __future__ import annotations

import ast
import importlib.metadata as metadata
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / 'pyproject.toml'

#: Directories whose imports must be satisfiable by a fresh install.
#:
#: `tests` and `alembic` are included: a migration that cannot import
#: what it needs fails a deployment, and a suite that cannot import
#: what it needs fails CI. Both are shipped in the sense that matters.
_SOURCES = ('app', 'tools', 'tests', 'alembic')

#: Imports deliberately satisfied by another declared dependency.
#:
#: Narrow on purpose. Each entry is a package that cannot go missing
#: while the thing that provides it is present — not merely one that
#: happens to be installed today.
_PROVIDED_BY_ANOTHER = {
    'starlette': 'fastapi is built on it and pins its range; declaring it separately invites disagreement',
}


def _declared() -> set[str]:
    """Every name in `[project] dependencies` and every dependency group."""
    text = _PYPROJECT.read_text()
    blocks = []
    start = text.index('dependencies = [')
    blocks.append(text[start : text.index('\n]', start)])
    blocks += re.findall(r'^\w+ = \[[^\]]*\]', text, re.M)
    names: set[str] = set()
    for block in blocks:
        for raw in re.findall(r'^\s*"([^"]+)"', block, re.M):
            names.add(_normalise(re.split(r'[<>=!\[;]', raw, maxsplit=1)[0].strip()))
    return names


def _imported_roots() -> set[str]:
    """Top-level modules imported anywhere in `_SOURCES`."""
    roots: set[str] = set()
    for directory in _SOURCES:
        for path in (_ROOT / directory).rglob('*.py'):
            try:
                tree = ast.parse(path.read_text(errors='ignore'))
            except SyntaxError:
                # A fixture that is deliberately unparseable. Nothing
                # imports it either.
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots |= {alias.name.split('.')[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split('.')[0])
    # `alembic` names a directory here *and* the package its migrations
    # import, so it is not excluded as local — that spelling collision
    # is how an undeclared dependency hides.
    local = {'app', 'tools', 'tests'}
    return {r for r in roots if r not in sys.stdlib_module_names and r not in local}


def _normalise(name: str) -> str:
    return name.lower().replace('_', '-')


class TestTheCheckMeasuresSomething:
    """Both halves are parsed out of files. A parser returning nothing
    would make the rule below pass against an empty set."""

    def test_the_dependencies_parse(self):
        declared = _declared()

        assert len(declared) > 20, declared
        assert {'fastapi', 'alembic', 'pydantic'} <= declared

    def test_the_imports_parse(self):
        roots = _imported_roots()

        assert len(roots) > 20, len(roots)
        assert {'fastapi', 'sqlalchemy', 'alembic'} <= roots, sorted(roots)[:20]

    def test_the_exceptions_are_actually_imported(self):
        """An excused import that nothing imports any more is a comment
        pretending to be a rule."""
        roots = _imported_roots()

        stale = sorted(name for name in _PROVIDED_BY_ANOTHER if name not in roots)

        assert not stale, f'excused as provided-by-another and no longer imported: {stale}'


class TestNothingArrivesTransitively:
    def test_every_imported_package_is_declared(self):
        declared = _declared()

        undeclared = []
        for root in sorted(_imported_roots()):
            if root in _PROVIDED_BY_ANOTHER:
                continue
            providers = {_normalise(p) for p in metadata.packages_distributions().get(root, [])}
            if providers and not (providers & declared):
                undeclared.append(f'{root} (installed by {sorted(providers)})')

        assert not undeclared, (
            f'imported but not declared: {undeclared}. Each is installed today because something '
            f'else happens to require it, and stops being installed the day that changes — which '
            f'is how SQLAlchemy, the ORM under every query in this application, came to be a '
            f'transitive dependency of a migration tool.'
        )

    def test_sqlalchemy_specifically(self):
        """Named on its own so a regression reports the package rather
        than appearing inside a list."""
        assert 'sqlalchemy' in _declared()
