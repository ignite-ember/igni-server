#!/usr/bin/env python
"""Re-encrypt every stored secret under the current key.

DP-2, F84. `SECRET_ENCRYPTION_KEY` protects the secrets this deployment
keeps in its own databases — the token-signing private key and GPU nodes'
API keys in the shared plane, and VM credentials awaiting pickup in each
organisation's own database. ISO 27001 A 5.33 asks that such a key can be rotated.

Every organisation's database is walked, detached ones included: their
data is kept, and a row in one left on the old key is unreadable the day
that key is removed. The first version walked only the shared plane, and
missed `vm_credentials.pending_pickup` in every organisation.

## Why this is in `tools/` and not `scripts/`

`.gitignore` has `/scripts/*`. This was written there first and would
never have been committed — it would have existed on one laptop, which
for the tool that finishes a key rotation is worse than not having it.
Found by asking git rather than by noticing later.

## The procedure

    # 1. Mint a new key.
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # 2. In the deployment's environment: move the current key to
    #    SECRET_ENCRYPTION_KEYS_PREVIOUS, put the new one in
    #    SECRET_ENCRYPTION_KEY. Restart. Everything still decrypts.

    # 3. See what is still on an old key.
    python tools/rotate_secret_key.py

    # 4. Re-encrypt it.
    python tools/rotate_secret_key.py --apply

    # 5. Remove SECRET_ENCRYPTION_KEYS_PREVIOUS. Restart again.

Step 5 is the one people skip, and skipping it leaves the old key working
— which is most of what rotating was for. The startup checks warn on
every boot while it is set.

## Why a pass and not lazy migration

`MultiFernet` makes lazy migration easy: decrypt on read, re-encrypt on
write. But these rows may be written once and never again — so "lazily" means "possibly
never", and the old key stays load-bearing with nothing saying so.

## What it does not do

It does not touch the database unless `--apply` is given, and it never
writes a plaintext credential anywhere — each value is decrypted in
memory, re-encrypted, and written back in the same statement. Each row is
its own transaction, and the write only lands if the row still holds the
ciphertext that was read, so a row the running server changed meanwhile
is left alone and counted. A failure mid-pass leaves a mix of old-key and
new-key rows, which is exactly the state the tool is designed to read and
resume from: both keys are still configured, so run it again.

Runs inside the API image (`containers/be/Dockerfile` copies it), with
that deployment's own settings.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Run as `python tools/rotate_secret_key.py`, which puts `tools/` on the
# path and not the repository root — so `app` is not importable without
# this. The alternative is asking an operator mid-rotation to remember
# `PYTHONPATH=.`, which is a way to have the tool fail at the moment it
# is needed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from cryptography.fernet import InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.sql.tenant_migrator import TenantMigrator
from app.db.sql.tenant_names import TenantNames
from app.services.secrets import decrypt_secret, encrypt_secret, previous_keys, primary_cipher

#: Ciphertext columns in the shared plane: `(table, primary key, column)`.
SHARED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ('signing_keys', 'kid', 'encrypted_private_key'),
    ('inference_nodes', 'id', 'api_key'),
    ('inference_nodes', 'id', 'credential'),
)

#: Ciphertext columns in every organisation's own database.
TENANT_COLUMNS: tuple[tuple[str, str, str], ...] = (('vm_credentials', 'id', 'pending_pickup'),)

#: Every column holding a Fernet ciphertext, wherever it lives.
#:
#: A list rather than reflection: reflection would quietly include a
#: column somebody adds later without deciding whether its contents
#: belong here. Tests compare it with the columns the models mark
#: `info={'encrypted': True}`, with every caller of `encrypt_secret`, and
#: with what is actually stored, so a new encrypted column fails until
#: it is added.
ENCRYPTED_COLUMNS: tuple[tuple[str, str, str], ...] = SHARED_COLUMNS + TENANT_COLUMNS

#: The counters `rotate` reports, in the order they are printed.
COUNTS = ('databases', 'checked', 'already_current', 'rotated', 'unreadable', 'changed_underneath', 'missing')


def _on_current_key(ciphertext: str) -> bool:
    """Whether this value already decrypts under the primary key alone."""
    try:
        primary_cipher().decrypt(ciphertext.encode())
        return True
    except InvalidToken:
        return False


def _databases() -> list[tuple[str, str, tuple[tuple[str, str, str], ...]]]:
    """`(label, database, columns)` for the shared plane and every organisation.

    Detached organisations included: a deleted organisation's database is
    kept, and a row in it left on the old key becomes unreadable the day
    that key is removed.
    """
    names = TenantNames()
    shared = names.base_name()
    found: list[tuple[str, str, tuple[tuple[str, str, str], ...]]] = [('shared', shared, SHARED_COLUMNS)]
    engine = create_engine(names.sync_url(shared), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            for row in conn.execute(text('SELECT org_id, database, state FROM tenants ORDER BY number')):
                found.append((f'organisation {row.org_id} ({row.state})', str(row.database), TENANT_COLUMNS))
    finally:
        engine.dispose()
    return found


def _rotate_one(
    engine: Engine, label: str, columns: tuple[tuple[str, str, str], ...], *, apply: bool, counts: dict[str, int]
) -> None:
    for table, pk, column in columns:
        with engine.begin() as conn:
            rows = conn.execute(
                text(f'SELECT {pk} AS row_id, {column} AS value FROM {table} WHERE {column} IS NOT NULL')
            ).fetchall()

        for row in rows:
            counts['checked'] += 1
            where = f'{label}: {table}.{column} id={row.row_id}'

            if _on_current_key(row.value):
                counts['already_current'] += 1
                continue

            try:
                plaintext = decrypt_secret(row.value)
            except Exception:
                counts['unreadable'] += 1
                print(f'  UNREADABLE {where}', file=sys.stderr)
                continue

            if not apply:
                counts['rotated'] += 1
                print(f'  would rotate {where}')
                continue

            # Compare-and-set on the old ciphertext. The server is running
            # while this does, and a row can change between the read above
            # and this write — a node agent picking up a VM credential
            # clears `pending_pickup`. Writing back unconditionally would
            # put a picked-up credential back up for pickup.
            with engine.begin() as conn:
                written = conn.execute(
                    text(f'UPDATE {table} SET {column} = :value WHERE {pk} = :row_id AND {column} = :old'),
                    {'value': encrypt_secret(plaintext), 'row_id': row.row_id, 'old': row.value},
                ).rowcount
            if written:
                counts['rotated'] += 1
                print(f'  rotated {where}')
            else:
                counts['changed_underneath'] += 1
                print(f'  changed while this ran, left alone: {where}')


def rotate(*, apply: bool) -> dict[str, int]:
    """Report — and optionally perform — the re-encryption, in every database.

    One row per transaction, and a row already on the current key is
    left alone, so an interrupted pass resumes by being run again: what
    it finished is counted as current, what it did not is still readable
    under the previous key, which is still configured.

    Returns counts, so the caller can exit non-zero when something could
    not be read: a row that decrypts under no configured key is a lost
    credential, and the operator has to know before they remove the old
    key.
    """
    counts = dict.fromkeys(COUNTS, 0)
    names = TenantNames()
    migrator = TenantMigrator(names=names)

    for label, database, columns in _databases():
        if label != 'shared' and not migrator.exists(database):
            counts['missing'] += 1
            print(f'  {label}: database {database} is registered but does not exist; skipped')
            continue
        counts['databases'] += 1
        engine = create_engine(names.sync_url(database), poolclass=NullPool)
        try:
            _rotate_one(engine, label, columns, apply=apply, counts=counts)
        finally:
            engine.dispose()

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--apply',
        action='store_true',
        help='write the re-encrypted values. Without this, only reports what would change.',
    )
    args = parser.parse_args()

    if not settings.secret_encryption_key:
        print('SECRET_ENCRYPTION_KEY is not set; there is nothing to rotate to.', file=sys.stderr)
        return 2

    previous = previous_keys()
    print(f'Primary key configured. {len(previous)} previous key(s) accepted for decryption.')
    if not previous:
        # Not an error: re-running after a completed rotation is a
        # reasonable way to confirm there is nothing left on an old key.
        print('No previous keys are configured, so nothing can be on an older key than the current one.')

    counts = rotate(apply=args.apply)

    print()
    print(f'  databases          {counts["databases"]}')
    print(f'  checked            {counts["checked"]}')
    print(f'  already current    {counts["already_current"]}')
    print(f'  {"rotated" if args.apply else "to rotate":<18} {counts["rotated"]}')
    print(f'  unreadable         {counts["unreadable"]}')
    if args.apply:
        print(f'  changed meanwhile  {counts["changed_underneath"]}')
    if counts['missing']:
        print(f'  missing databases  {counts["missing"]}')

    if counts['unreadable']:
        print(
            '\nSome rows decrypt under no configured key. Do not remove '
            'SECRET_ENCRYPTION_KEYS_PREVIOUS: those credentials would have to be '
            'supplied again.',
            file=sys.stderr,
        )
        return 1

    if not args.apply and counts['rotated']:
        print('\nRe-run with --apply to write these.')

    if counts['changed_underneath']:
        print('\nSome rows changed while this ran. Run it again to pick them up.')
        return 0

    if args.apply and previous:
        print(
            '\nDone. Now remove SECRET_ENCRYPTION_KEYS_PREVIOUS and restart — until you '
            'do, the old key still works on this deployment.'
        )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
