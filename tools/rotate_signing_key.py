#!/usr/bin/env python
"""Rotate the key igni signs its tokens with.

    python tools/rotate_signing_key.py            # show the keys
    python tools/rotate_signing_key.py --stage    # add a new one

`--stage` writes a new key that is published at once and starts signing
after `ACTIVATES_IN`. Each replica re-reads the keys every five minutes
and every service caches igni's key set for as long, so by the time the
new key signs anything, everybody verifying has it. Nothing to restart.

The old key keeps verifying what it signed, and every replica prunes it
31 days after the new one took over — the longest any token lives, and a
day more.

Nothing here prints a private key. If one has leaked, stage a new key and
withdraw everybody's sessions; tokens it signed stay valid until they
expire otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import pathlib
import sys

# Run as `python tools/rotate_signing_key.py`; see rotate_secret_key.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db.sql.signing_keys import SigningKeyDB
from app.services.identity.key_ring import RELOAD_EVERY

#: Twice the reload interval: one for replicas, one for service caches.
ACTIVATES_IN = RELOAD_EVERY * 2


async def _main(stage: bool) -> int:
    store = SigningKeyDB()
    if stage:
        key = await store.stage(activates_in=ACTIVATES_IN)
        print(f'Staged {key.kid}; it signs from {key.activates_at.isoformat()}.')
    now = datetime.now(timezone.utc)
    keys = await store.all()
    signing = max((k for k in keys if k.activates_at <= now), key=lambda k: k.activates_at, default=None)
    for key in keys:
        if key is signing:
            state = 'signing'
        elif key.activates_at > now:
            state = 'staged'
        else:
            state = 'superseded, still verifying'
        print(f'{key.kid}  {state:28}  activates {key.activates_at.isoformat()}')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--stage', action='store_true', help='add a new key that signs after a delay')
    sys.exit(asyncio.run(_main(parser.parse_args().stage)))
