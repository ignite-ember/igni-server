"""The licence's core is one implementation, copied: igni server's and each paid module's must not drift.

Each paid module verifies the licence itself (docs/licence-file.md), so the
code is in each repository rather than shared through igni server. This
holds the copies together when the siblings are checked out beside this
repository, the way `test_the_docs_name_real_things` checks sibling paths.
"""

from __future__ import annotations

import pathlib

import pytest

_HERE = pathlib.Path(__file__).resolve().parent.parent / 'app' / 'services' / 'licence'
_COPIES = {
    'igni__code-index': 'app/licence',
    'igni__artifacts': 'app/licence',
}
_FILES = sorted(path.name for path in _HERE.glob('*.py'))


@pytest.mark.parametrize('repository', sorted(_COPIES))
@pytest.mark.parametrize('name', _FILES)
def test_each_module_carries_the_same_file(repository: str, name: str) -> None:
    root = _HERE.parents[3] / repository
    if not root.is_dir():
        pytest.skip(f'{repository} is not checked out beside this repository')

    assert (root / _COPIES[repository] / name).read_bytes() == (_HERE / name).read_bytes(), (
        f'{repository}/{_COPIES[repository]}/{name} differs from app/services/licence/{name}: '
        'change every copy together (docs/licence-file.md)'
    )
