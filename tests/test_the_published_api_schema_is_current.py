"""igni server publishes its API schema, and the published copy is the live one.

igni's SDK is generated from `docs/openapi.json`, not written by hand. A
route added, renamed or reshaped without regenerating the file would
leave the SDK describing an API that is not there — so the file must equal
what the app serves. Regenerate rather than edit:

    python tools/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.export_openapi import render, SCHEMA

ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_schema_is_what_the_app_serves():
    assert SCHEMA.exists(), 'docs/openapi.json is missing: run python tools/export_openapi.py'
    assert SCHEMA.read_text() == render(), (
        'docs/openapi.json and the routes disagree. Regenerate it: python tools/export_openapi.py'
    )


def test_it_names_the_areas_an_sdk_is_built_from():
    # The SDK groups operations by tag; an untagged operation would land nowhere.
    spec = json.loads(SCHEMA.read_text())
    untagged = [
        f'{method.upper()} {path}'
        for path, operations in spec['paths'].items()
        for method, operation in operations.items()
        if method in ('get', 'post', 'put', 'patch', 'delete') and not operation.get('tags')
    ]
    assert not untagged, f'these operations have no tag, so no SDK area: {untagged}'
