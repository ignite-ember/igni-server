"""The server's pages on igni's site are generated from this repository, and current.

``tools/site_docs.py`` writes ``docs/site/`` — the operator-facing articles under
``docs/site-src/``, stamped with this release's version — and igni-site copies it
(``scripts/sync-docs.mjs server`` there). Edit an article, add one to
``index.toml``, or bump the version and forget ``make docs-site``, and this fails.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DOCS = None


def _docs():  # type: ignore[no-untyped-def]
    """The generator, imported lazily so collection does not need the app."""
    global _DOCS
    if _DOCS is None:
        from tools.site_docs import SiteDocs

        _DOCS = SiteDocs(_ROOT)
    return _DOCS


def test_the_checked_in_pages_are_what_the_sources_generate() -> None:
    stale = _docs().stale()
    assert not stale, f'stale or left over: {[str(p.relative_to(_ROOT)) for p in stale]}. Run `make docs-site`.'


def test_every_source_article_is_in_the_index_and_on_disk_once() -> None:
    from tools.site_docs import PACKAGE

    index = json.loads((_ROOT / 'docs/site/index.json').read_text(encoding='utf-8'))
    articles = _docs().articles()
    files = {p['file'] for p in index['pages']}

    on_disk = {str(p.relative_to(_ROOT / 'docs/site')) for p in (_ROOT / 'docs/site').rglob('*.md')}
    assert files == on_disk
    assert len(files) == len(index['pages'])
    assert [p['slug'] for p in index['pages']] == [a.slug for a in articles]
    for order, (page, article) in enumerate(zip(index['pages'], articles, strict=True)):
        assert set(page) == {
            'package',
            'slug',
            'title',
            'description',
            'category',
            'order',
            'file',
            'sections',
        }
        assert page['package'] == PACKAGE
        assert page['title'] == article.title
        assert page['description'] == article.description
        assert page['category'] == article.category
        assert page['order'] == order
        assert page['description'], page['file']


def test_the_index_is_stamped_with_the_app_version() -> None:
    from tools.site_docs import app_version

    index = json.loads((_ROOT / 'docs/site/index.json').read_text(encoding='utf-8'))
    version = app_version(_ROOT)
    assert index['packages'][0]['version'] == version
    for page in index['pages']:
        assert page['file'].split('/')[1] == version


def test_every_generated_page_is_its_source_verbatim() -> None:
    """The site renders the prose, so the generator adds nothing to it — not even a
    banner. This is what keeps the output byte-for-byte what igni-site copies in."""
    version = _docs().version()
    for article in _docs().articles():
        source = (_ROOT / 'docs/site-src' / f'{article.slug}.md').read_text(encoding='utf-8')
        output = (_ROOT / 'docs/site' / 'server' / version / f'{article.slug}.md').read_text(encoding='utf-8')
        assert output == source, article.slug


def test_the_pages_are_the_same_whatever_the_hash_seed_is() -> None:
    """The generator writes the same files every run, whatever the process hash seed."""
    outputs = []
    for seed in ('0', '1', '424242'):
        done = subprocess.run(  # noqa: S603 — python, on our own generator
            [sys.executable, '-m', 'tools.site_docs', '--check'],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'PYTHONHASHSEED': seed},
        )
        assert done.returncode == 0, done.stdout + done.stderr
        outputs.append((_ROOT / 'docs/site/index.json').read_text(encoding='utf-8'))
    assert outputs[0] == outputs[1] == outputs[2]
