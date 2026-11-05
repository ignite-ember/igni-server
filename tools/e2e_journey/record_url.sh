#!/bin/sh
# The "browser" the igni CLI opens during the journey (BROWSER='record_url.sh %s').
# It writes the address down for the journey to open in Chromium, and exits
# at once: webbrowser.open waits for it, inside the CLI's event loop.
printf '%s\n' "$1" >"${CLI_LOGIN_URL_FILE:?}"
