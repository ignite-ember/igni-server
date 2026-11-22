"""The server's published documentation for igni's site, generated: ``make docs-site``.

The site (igni.sh, the ``igni-site`` repository) shows the operator-facing
articles under ``/docs/server/<version>/<page>``. Those articles are authored
here, next to the code they document, under ``docs/site-src/`` — their prose in
one ``.md`` file each, and their titles, order, slugs, descriptions and
categories in ``docs/site-src/index.toml``. This is DIFFERENT from the internal
documentation under ``docs/`` (architecture, compliance, ``self-hosting.md``);
it is the published articles only.

This mirrors igni-sdk's ``tools/sdk_docs.py``: a release produces its own doc
snapshot with no hand-typed version folder. It writes ``docs/site/``:

* ``server/<version>/<slug>.md`` — one article each, the source ``.md`` verbatim
  (the site renders the prose, so nothing is added to it — not even a banner,
  which is why the output is byte-for-byte the source);
* ``index.json`` — the version (the ``major.minor`` series of ``app/config.py``'s
  ``app_version``, so patches within a minor share one snapshot — see ``doc_version``)
  and every page's slug, title, description, category, order, file and sections,
  in the same shape igni-sdk emits, so ``scripts/sync-docs.mjs`` in igni-site
  consumes both uniformly.

``tests/test_the_site_docs_are_generated.py`` regenerates it all in memory and
fails when what is checked in differs. Run ``make docs-site`` after editing an
article, adding one to ``index.toml``, or bumping the version.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import tomllib

from pydantic import BaseModel

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SOURCE = _ROOT / 'docs/site-src'
_INDEX_SOURCE = _SOURCE / 'index.toml'
_OUTPUT = _ROOT / 'docs/site'
_INDEX = _OUTPUT / 'index.json'

#: The site routes the server's documentation under this package slug.
PACKAGE = 'server'

GENERATED_BY = 'igni-server tools/site_docs.py (make docs-site); do not edit'

_HEADING = re.compile(r'^(#{2,3})\s+(.+?)\s*$')


class DocsError(ValueError):
    """The site docs cannot be generated as the sources are."""


def app_version(root: pathlib.Path = _ROOT) -> str:
    """The application version, read from ``app/config.py`` the way the repository does."""
    source = (root / 'app/config.py').read_text(encoding='utf-8')
    match = re.search(r"^\s*app_version: str = '([^']+)'", source, re.M)
    if not match:
        raise DocsError('could not find `app_version` in app/config.py')
    return match.group(1)


def doc_version(root: pathlib.Path = _ROOT) -> str:
    """The documentation version: the ``major.minor`` series of the app version.

    The docs snapshot per minor series, not per patch — a code-only patch
    (0.8.0 → 0.8.1) shares the 0.8 docs, so it does not churn a new snapshot.
    The changelog article still records each patch within the series.
    """
    return '.'.join(app_version(root).split('.')[:2])


def heading_id(text: str) -> str:
    """A heading's anchor id, the way igni-site names it (``docsManifest.headingId``)."""
    return re.sub(r'(^-|-$)', '', re.sub(r'[^a-z0-9]+', '-', text.lower()))


class Section(BaseModel):
    """One ``##`` or ``###`` heading of a page, as the site names it."""

    level: int
    id: str
    title: str


def sections_of(body: str) -> list[Section]:
    """A page's ``##`` and ``###`` headings, skipping fenced code, for search and a table of contents."""
    out: list[Section] = []
    in_code = False
    for line in body.split('\n'):
        if line.lstrip().startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = _HEADING.match(line)
        if match:
            title = match.group(2).strip()
            out.append(Section(level=len(match.group(1)), id=heading_id(title), title=title))
    return out


class Article(BaseModel):
    """One source article: its metadata from ``index.toml`` and its prose from the ``.md`` beside it."""

    slug: str
    title: str
    description: str
    category: str


class IndexPage(BaseModel):
    """One page in the index."""

    package: str
    slug: str
    title: str
    description: str
    category: str
    order: int
    file: str
    sections: list[Section]


class IndexPackage(BaseModel):
    """The package in the index, with the version this snapshot was stamped from."""

    name: str
    title: str
    version: str
    description: str
    source: str


class SiteIndex(BaseModel):
    """The whole index."""

    generated_by: str = GENERATED_BY
    packages: list[IndexPackage]
    pages: list[IndexPage]

    def text(self) -> str:
        """The index as written: two-space indents, Unicode as it is, one trailing newline."""
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False) + '\n'


class SiteDocs:
    """The server's site pages and their index, and whether what is checked in is current."""

    def __init__(self, root: pathlib.Path = _ROOT) -> None:
        self.root = root
        self.source = root / 'docs/site-src'
        self.output = root / 'docs/site'

    def _meta(self) -> dict[str, object]:
        return tomllib.loads((self.root / 'docs/site-src/index.toml').read_text(encoding='utf-8'))

    def articles(self) -> list[Article]:
        meta = self._meta()
        entries = meta.get('article') or []
        if not isinstance(entries, list) or not entries:
            raise DocsError('docs/site-src/index.toml lists no articles under [[article]]')
        articles = [Article.model_validate(entry) for entry in entries]
        seen: set[str] = set()
        for article in articles:
            if article.slug in seen:
                raise DocsError(f'two articles are {article.slug}')
            seen.add(article.slug)
            if not (self.source / f'{article.slug}.md').exists():
                raise DocsError(f'docs/site-src/{article.slug}.md is listed but does not exist')
        return articles

    def version(self) -> str:
        return doc_version(self.root)

    def _package(self) -> IndexPackage:
        package = self._meta().get('package')
        if not isinstance(package, dict):
            raise DocsError('docs/site-src/index.toml has no [package] table')
        return IndexPackage(version=self.version(), **package)

    def outputs(self) -> dict[pathlib.Path, str]:
        version = self.version()
        articles = self.articles()
        out: dict[pathlib.Path, str] = {}
        pages: list[IndexPage] = []
        for order, article in enumerate(articles):
            body = (self.source / f'{article.slug}.md').read_text(encoding='utf-8')
            file = f'{PACKAGE}/{version}/{article.slug}.md'
            out[self.output / file] = body
            pages.append(
                IndexPage(
                    package=PACKAGE,
                    slug=article.slug,
                    title=article.title,
                    description=article.description,
                    category=article.category,
                    order=order,
                    file=file,
                    sections=sections_of(body),
                )
            )
        out[self.output / 'index.json'] = SiteIndex(packages=[self._package()], pages=pages).text()
        return out

    def _strays(self, outputs: dict[pathlib.Path, str]) -> list[pathlib.Path]:
        """Files under the output directory that the generator does not write."""
        if not self.output.exists():
            return []
        return [p for p in sorted(self.output.rglob('*')) if p.is_file() and p not in outputs]

    @staticmethod
    def _differs(path: pathlib.Path, text: str) -> bool:
        return not path.exists() or path.read_text(encoding='utf-8') != text

    def write(self) -> list[pathlib.Path]:
        outputs = self.outputs()
        written: list[pathlib.Path] = []
        for path, text in outputs.items():
            if self._differs(path, text):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
                written.append(path)
        for path in self._strays(outputs):
            path.unlink()
            written.append(path)
        return written

    def stale(self) -> list[pathlib.Path]:
        outputs = self.outputs()
        stale = [path for path, text in outputs.items() if self._differs(path, text)]
        return stale + self._strays(outputs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the checked-in docs are stale')
    args = parser.parse_args(argv)

    docs = SiteDocs()
    if args.check:
        stale = docs.stale()
        if stale:
            print(f'stale or left over: {[str(p.relative_to(_ROOT)) for p in stale]}. Run `make docs-site`.')
            return 1
        print('docs/site/ is current')
        return 0

    for path in docs.write():
        print('wrote' if path.exists() else 'removed', path.relative_to(_ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
