"""The igni CLI's ``/login``, run headless: ``AuthController.login`` as it is.

Run with the CLI checkout's Python, not the server's. Nothing in the
flow is replaced. ``/login`` is a slash command inside the interactive
CLI, so this calls the controller behind it: the CLI starts its callback
server and opens the portal's ``/cli-auth`` page with ``webbrowser.open``.
``BROWSER`` (``record_url.sh``) makes that "browser" write the address
down; the journey then opens it in Chromium, where a person signs in
with SSO and the portal redirects to the CLI's callback. The CLI then
does its own ``/v1/auth/exchange``, ``/v1/portal/me`` validation,
credentials file and group-policy refresh.

    BROWSER='record_url.sh %s' CLI_LOGIN_URL_FILE=... python cli_login.py --project DIR

The last line printed is a JSON result.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from ember_code.backend.server_auth import AuthController
from ember_code.core.config.settings import load_settings


class _Session:
    """The one call AuthController makes on its session after a login."""

    reloaded = False

    def replace_cloud_credentials(self, creds: object) -> None:
        self.reloaded = True


async def login(project: Path, timeout: float) -> int:
    settings = load_settings(project_dir=project)
    print(f'settings: api_url={settings.api_url} portal_url={settings.portal_url}', flush=True)
    session = _Session()
    controller = AuthController(session, settings, lambda: None)
    result = await asyncio.wait_for(
        controller.login(on_status=lambda text: print('status:', text.splitlines()[0], flush=True)), timeout
    )
    print(
        json.dumps(
            {
                'ok': result.ok,
                'email': getattr(result, 'email', ''),
                'error': getattr(result, 'error', None),
                'creds_reloaded': session.reloaded,
            }
        )
    )
    return 0 if result.ok else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    sys.exit(asyncio.run(login(Path(args.project), args.timeout)))


if __name__ == '__main__':
    main()
