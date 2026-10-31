"""The licence is declared, complete, and free of placeholders.

The last of the marketplace listing's items that is engineering rather than
paperwork. Both container images declared
`org.opencontainers.image.licenses="NOASSERTION"` — which is not an empty
label, it is the literal string SPDX tooling uses for "nobody said". So
every scanner and every marketplace review read the images as
unlicensed, and there was no `LICENSE` file in the repository at all.

Two things are checked here, and the second is the one that earns its
keep.

**That the declaration is real.** Apache-2.0 in `LICENSE`, complete —
every numbered section, not a summary — and the same identifier in both
image labels, `pyproject.toml` and `package.json`. Four places that can
disagree.

**That no placeholder ships.** `NOTICE` and `SECURITY.md` carry values
only a person can supply: the copyright holder, a reporting address,
response times. They were written as `__DOUBLE_UNDERSCORED__` tokens
until somebody supplied them, because a plausible-looking guess would be
worse than a gap. They are filled in now, and this keeps them that way.
"""

from __future__ import annotations

import json
import pathlib
import re
import tomllib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every clause an Apache-2.0 copy must contain. A truncated licence is
#: not a licence, and the failure mode is a file that looks right for
#: sixty lines.
_APACHE_SECTIONS = (
    'Apache License',
    'Version 2.0, January 2004',
    '1. Definitions.',
    '2. Grant of Copyright License.',
    '3. Grant of Patent License.',
    '4. Redistribution.',
    '5. Submission of Contributions.',
    '6. Trademarks.',
    '7. Disclaimer of Warranty.',
    '8. Limitation of Liability.',
    '9. Accepting Warranty or Additional Liability.',
    'APPENDIX: How to apply the Apache License to your work.',
)


class TestTheLicenceFileIsComplete:
    def test_it_exists(self):
        assert (_ROOT / 'LICENSE').is_file(), 'there is no LICENSE file'

    @pytest.mark.parametrize('section', _APACHE_SECTIONS, ids=lambda s: s.split('.')[0][:28])
    def test_every_section_is_present(self, section):
        assert section in (_ROOT / 'LICENSE').read_text(), f'LICENSE is missing {section!r}'

    def test_it_carries_no_other_projects_text(self):
        """It was extracted from a dependency that ships a verbatim copy —
        two of them, diffed against each other — so the thing to check is
        that the surrounding project's own preamble did not come along."""
        text = (_ROOT / 'LICENSE').read_text()

        for foreign in ('typeshed', 'Matthew Barnett', 'CPython', 'Secret Labs'):
            assert foreign not in text, f'LICENSE contains {foreign!r} from the file it was copied from'

    def test_the_appendix_keeps_its_instructional_placeholders(self):
        """`[yyyy] [name of copyright owner]` belongs in the appendix: it
        is instructions for applying the licence, not an unfilled field.
        The actual assertion lives in NOTICE."""
        text = (_ROOT / 'LICENSE').read_text()

        assert 'Copyright [yyyy] [name of copyright owner]' in text

    def test_notice_exists_and_points_at_the_licence(self):
        notice = (_ROOT / 'NOTICE').read_text()

        assert 'Apache License, Version 2.0' in notice
        assert 'igni' in notice
        assert 'Copyright 2026 Ignite Ember' in notice


class TestEveryArtefactDeclaresTheSameLicence:
    """Four places, and they can disagree. A scanner reads the image
    label; a dependency audit reads the manifest; a person reads
    LICENSE."""

    def test_no_artefact_says_noassertion(self):
        """The literal SPDX string for "nobody said", which is how both
        images were reporting as unlicensed."""
        offenders = []

        for path in ('containers/be/Dockerfile', 'portal/Dockerfile'):
            for line in (_ROOT / path).read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith('#'):
                    # The comment explaining what NOASSERTION was. Third
                    # time in this review a source sweep matched its own
                    # explanation, so comments are excluded up front.
                    continue
                if 'NOASSERTION' in stripped:
                    offenders.append(f'{path}: {stripped}')

        assert not offenders, offenders

    @pytest.mark.parametrize('path', ['containers/be/Dockerfile', 'portal/Dockerfile'])
    def test_both_images_label_apache(self, path):
        text = (_ROOT / path).read_text()

        assert 'org.opencontainers.image.licenses="Apache-2.0"' in text, f'{path} does not declare a licence'

    def test_the_python_package_declares_it(self):
        data = tomllib.loads((_ROOT / 'pyproject.toml').read_text())

        assert data['project'].get('license') == 'Apache-2.0'
        assert 'LICENSE' in (data['project'].get('license-files') or [])

    def test_the_portal_package_declares_it(self):
        data = json.loads((_ROOT / 'portal/package.json').read_text())

        assert data.get('license') == 'Apache-2.0'


class TestNoPlaceholderCanShip:
    """A `NOTICE` with `__LEGAL_ENTITY__` in it, or a `SECURITY.md` with no
    address, is exactly the sort of thing that survives to a release."""

    _PLACEHOLDER = re.compile(r'__[A-Z][A-Z0-9_]*__')

    def test_the_pattern_finds_a_placeholder(self):
        """First: prove the pattern matches. If it were wrong, the test
        below would pass on a document full of gaps."""
        assert self._PLACEHOLDER.findall('Copyright 2026 __LEGAL_ENTITY__') == ['__LEGAL_ENTITY__']

    @pytest.mark.parametrize('path', ['NOTICE', 'SECURITY.md'])
    def test_none_is_left(self, path):
        found = self._PLACEHOLDER.findall((_ROOT / path).read_text())

        assert not found, f'{path} still holds placeholders: {sorted(set(found))}'
