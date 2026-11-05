"""Write a development ``.env`` that `make run` starts against: `make dev-env`.

    python tools/dev_env.py [--out .env] [--pg-port 5432] [--redis-port 6379]
                            [--api-port 7777] [--portal-port 3000] [--force]

The Quick start used to say "copy dist.env to .env and set at minimum
SECRET_ENCRYPTION_KEY". Done exactly that way on a fresh clone, `make migrate`
connected to a Unix socket (POSTGRES_HOST is blank), then to a database named
after the user (POSTGRES_DB is blank, which is not "unset"), and `make run`
refused to start (APP_ENV=prod with no https:// PUBLIC_BASE_URL); with that
fixed, nobody could sign in (no mail relay, LOG_SIGN_IN_CODES off). Six edits,
each found by reading a traceback.

This makes the same edits to a copy of dist.env, the template, so every other
setting and comment is the one an operator reads: development mode, Postgres
and Redis on localhost, a fresh encryption key, the API and portal URLs, and
one-time codes written to the server log — the way in when there is no mail
relay, and the reason this file is for a laptop and nothing else.

It refuses to overwrite an existing file unless ``--force``.
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent


def settings(*, pg_port: int, redis_port: int, api_port: int, portal_port: int) -> dict[str, str]:
    """The development values, keyed as dist.env names them."""
    api = f'http://localhost:{api_port}'
    portal = f'http://localhost:{portal_port}'
    return {
        'APP_ENV': 'dev',
        'POSTGRES_HOST': 'localhost',
        'POSTGRES_PORT': str(pg_port),
        'POSTGRES_USER': 'myuser',
        'POSTGRES_PASSWORD': 'mysecretpassword',
        'POSTGRES_DB': 'igni',
        'REDIS_HOST': 'localhost',
        'REDIS_PORT': str(redis_port),
        'SECRET_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'PUBLIC_BASE_URL': api,
        'PORTAL_BASE_URL': portal,
        'OAUTH_REDIRECT_BASE_URL': api,
        'OAUTH_FRONTEND_BASE_URL': portal,
        # Google sign-in needs a client of your own; without one the button
        # leads nowhere, so a development install starts with none.
        'OAUTH_PROVIDERS': '[]',
        'LOG_SIGN_IN_CODES': 'true',
    }


def apply(text: str, values: dict[str, str]) -> str:
    """Rewrite each ``KEY=`` line of ``text`` where it stands; append a missing one."""
    for key, value in values.items():
        text, count = re.subn(rf'^{re.escape(key)}=.*$', lambda _m, k=key, v=value: f'{k}={v}', text, flags=re.M)
        if not count:
            text += f'\n{key}={value}\n'
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, default=ROOT / '.env')
    parser.add_argument('--pg-port', type=int, default=5432)
    parser.add_argument('--redis-port', type=int, default=6379)
    parser.add_argument('--api-port', type=int, default=7777)
    parser.add_argument('--portal-port', type=int, default=3000)
    parser.add_argument('--force', action='store_true', help='overwrite an existing file')
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        print(f'{args.out} exists; not overwriting it (pass --force, or FORCE=1 to make dev-env)', file=sys.stderr)
        return 1
    values = settings(
        pg_port=args.pg_port, redis_port=args.redis_port, api_port=args.api_port, portal_port=args.portal_port
    )
    args.out.write_text(apply((ROOT / 'dist.env').read_text(), values))
    args.out.chmod(0o600)
    print(f'Wrote {args.out} for development:')
    print(f'  Postgres localhost:{args.pg_port} as myuser, Redis localhost:{args.redis_port}')
    print(f'  API http://localhost:{args.api_port}, portal http://localhost:{args.portal_port}')
    print('  a new SECRET_ENCRYPTION_KEY, and one-time sign-in codes in the server log')
    print('  (LOG_SIGN_IN_CODES=true). Development only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
