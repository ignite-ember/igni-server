"""Set ``KEY=value`` lines in a copy of dist.env, the way an operator edits it.

    python -m tools.e2e_journey.server_env run/server/.env APP_ENV=dev POSTGRES_PORT=55501 ...

A key dist.env has is rewritten where it stands; one it lacks is appended.
"""

from pathlib import Path
import re
import sys


def apply(text: str, settings: list[str]) -> str:
    for setting in settings:
        key, _, value = setting.partition('=')
        text, count = re.subn(rf'^{re.escape(key)}=.*$', lambda _m, k=key, v=value: f'{k}={v}', text, flags=re.M)
        if not count:
            text += f'\n{key}={value}\n'
    return text


def main() -> None:
    path = Path(sys.argv[1])
    path.write_text(apply(path.read_text(), sys.argv[2:]))


if __name__ == '__main__':
    main()
