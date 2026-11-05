"""Write the alembic_tenant migration that carries a refreshed seed to existing orgs.

`make refresh-default-seed` changes what a *new* organisation's Default
group is seeded with. The ones that already exist keep the old entries
until a migration rewrites them, and the suite fails until there is one
(`test_an_existing_default_group_is_brought_to_what_igni_ships`). This
writes that migration: it freezes today's `seed_entries()` as `_SEED`,
and, for the downgrade, the entries of the previous frozen seed (the
latest migration that has a `_SEED`) that differ from it.

Run through ``make default-seed-migration SLUG=<words_in_snake_case>``.
Prints what changed and refuses when nothing did.
"""

from __future__ import annotations

import argparse
from datetime import date
import importlib.util
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / 'alembic_tenant' / 'versions'
sys.path.insert(0, str(ROOT))

from app.services.portal.default_group import seed_entries  # noqa: E402
from app.services.portal.entry_kinds import ALLOWED_CONTENT_TYPE  # noqa: E402

Seed = list[tuple[str, str, str]]

_REVISION = re.compile(r"^revision: str = '([^']+)'", re.MULTILINE)
_DOWN = re.compile(r"^down_revision: [^=]+= '([^']+)'", re.MULTILINE)


def _revisions() -> dict[str, tuple[str | None, Path]]:
    out: dict[str, tuple[str | None, Path]] = {}
    for path in VERSIONS.glob('t*.py'):
        source = path.read_text(encoding='utf-8')
        revision = _REVISION.search(source)
        if revision:
            down = _DOWN.search(source)
            out[revision.group(1)] = (down.group(1) if down else None, path)
    return out


def _head(revisions: dict[str, tuple[str | None, Path]]) -> str:
    downs = {down for down, _ in revisions.values()}
    heads = [revision for revision in revisions if revision not in downs]
    if len(heads) != 1:
        raise SystemExit(f'alembic_tenant has {len(heads)} heads ({heads}); merge them first.')
    return heads[0]


def _previous_frozen_seed(revisions: dict[str, tuple[str | None, Path]], head: str) -> tuple[str, Seed]:
    """The `_SEED` of the newest migration that froze one, walking down from head."""
    revision: str | None = head
    while revision:
        down, path = revisions[revision]
        if '\n_SEED: ' in path.read_text(encoding='utf-8'):
            spec = importlib.util.spec_from_file_location(f'seed_{revision}', path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return revision, list(module._SEED)
        revision = down
    raise SystemExit('No earlier migration froze a seed; this script starts from t0025.')


def _literal(text: str) -> str:
    body = text.replace('\\', '\\\\').replace("'''", "''\\'")
    if body.endswith("'"):
        body = body[:-1] + "\\'"
    return "'''\\\n" + body + "'''"


def _block(name: str, rows: Seed) -> str:
    parts = [f'{name}: tuple[tuple[str, str, str], ...] = (']
    for kind, entry, content in rows:
        parts.append(f'    (\n        {kind!r},\n        {entry!r},\n        {_literal(content)},\n    ),')
    parts.append(')')
    return '\n'.join(parts)


def _names(keys: list[tuple[str, str]]) -> str:
    return ', '.join(f'`{name}`' for _, name in keys) or 'none'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--slug', required=True, help='the file name after the revision, in snake_case')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_]+', args.slug):
        raise SystemExit('--slug is lower-case words joined by underscores.')

    revisions = _revisions()
    head = _head(revisions)
    since, before = _previous_frozen_seed(revisions, head)
    after = seed_entries()

    after_map = {(kind, name): content for kind, name, content in after}
    before_map = {(kind, name): content for kind, name, content in before}
    changed = [(kind, name, content) for kind, name, content in before if after_map.get((kind, name)) != content]
    added = sorted(key for key in after_map if key not in before_map)
    removed = sorted(key for key in before_map if key not in after_map)
    rewritten = sorted(key for key in after_map if key in before_map and before_map[key] != after_map[key])
    if not (added or removed or rewritten):
        print(f'The seed is what {since} froze; no migration needed.')
        return 1

    kinds = sorted({kind for kind, _, _ in after + before})
    missing = set(kinds) - set(ALLOWED_CONTENT_TYPE)
    if missing:
        raise SystemExit(f'No content type for {sorted(missing)}.')
    content_types = {kind: ALLOWED_CONTENT_TYPE[kind] for kind in kinds}

    number = int(head.lstrip('t')) + 1
    revision = f't{number:04d}'
    template = (Path(__file__).resolve().parent / 'default_seed_migration.py.tmpl').read_text(encoding='utf-8')
    source = (
        template.replace('@REVISION@', revision)
        .replace('@DOWN_REVISION@', head)
        .replace('@SINCE@', since)
        .replace('@DATE@', date.today().isoformat())
        .replace('@REWRITTEN@', _names(rewritten))
        .replace('@ADDED@', _names(added))
        .replace('@REMOVED@', _names(removed))
        .replace('#@CONTENT_TYPES@', f'_CONTENT_TYPES: dict[str, str] = {content_types!r}')
        .replace('#@ONLY_AFTER@', f'_ONLY_IN_THE_NEW_SEED: frozenset[tuple[str, str]] = frozenset({added!r})')
        .replace('#@SEED@', _block('_SEED', after))
        .replace('#@BEFORE@', _block('_BEFORE', changed))
    )
    target = VERSIONS / f'{revision}_{args.slug}.py'
    target.write_text(source, encoding='utf-8')

    # The literals read back as exactly what they froze.
    namespace: dict = {}
    exec(compile(source, str(target), 'exec'), namespace)
    assert list(namespace['_SEED']) == after
    assert list(namespace['_BEFORE']) == changed
    assert sorted(namespace['_previous_seed']()) == sorted(before)

    print(f'Wrote {target.relative_to(ROOT)} (revises {head}; downgrade restores {since}\'s seed)')
    print(f'  rewritten: {_names(rewritten)}')
    print(f'  added:     {_names(added)}')
    print(f'  removed:   {_names(removed)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
