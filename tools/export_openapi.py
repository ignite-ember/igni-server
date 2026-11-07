"""Write igni server's API schema to `docs/openapi.json` — the contract igni's SDK is generated from.

igni's SDK (`igni-sdk`, imported as `igni`) wraps every igni server API a
person or an app uses, generated from this file rather than written by
hand, so it cannot drift from the routes. The file is committed, and
`tests/test_the_published_api_schema_is_current.py` fails when the routes
and it disagree:

    python tools/export_openapi.py          # rewrite docs/openapi.json
    python tools/export_openapi.py --check  # exit 1 when it is stale
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / 'docs' / 'openapi.json'


def render() -> str:
    """The schema as committed: sorted keys, two-space indent, one trailing newline."""
    sys.path.insert(0, str(ROOT))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def main(argv: list[str]) -> int:
    rendered = render()
    if '--check' in argv:
        if SCHEMA.exists() and SCHEMA.read_text() == rendered:
            print(f'{SCHEMA.relative_to(ROOT)} is current')
            return 0
        print(f'{SCHEMA.relative_to(ROOT)} is stale: run python tools/export_openapi.py', file=sys.stderr)
        return 1
    SCHEMA.write_text(rendered)
    print(f'wrote {SCHEMA.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
