#!/usr/bin/env python
"""Issue igni licence files. Ignite Ember's tool; customers have no use for it.

    # once, on the issuing machine, outside any repository:
    python tools/licence/issue_licence.py keygen --out ~/igni-licence/ie-2026.pem --key-id ie-2026

    # a licence: one --module per paid module, NAME[=USERS][@EXPIRES]
    IGNI_LICENCE_KEY_FILE=~/igni-licence/ie-2026.pem \\
    python tools/licence/issue_licence.py issue --key-id ie-2026 \\
        --licence-id IE-2026-0042 --customer "Example Bank N.V." \\
        --expires 2027-10-29 --module codeindex=100 --module artifacts=50 \\
        --module factory --module autopilot=50 --out example-bank.licence.json

    # what a file says, and whether this release accepts it:
    python tools/licence/issue_licence.py inspect example-bank.licence.json

The private key is read from `--key-file`, or the file named by
`IGNI_LICENCE_KEY_FILE`, or the PEM in `IGNI_LICENCE_KEY`. It is never
written into a git working tree: `keygen` refuses to.

Rules the tool keeps so a customer never receives a licence that will
not work: bands are 50, 100, 200, 500, 1,000 and then thousands (an
evaluation is at most 25 people); the Factory has no band; Autopilot
needs the Factory in the same licence.

**Renewing without a module.** A module that is not renewed stays in the
licence with its old expiry, so it goes on to read-only rather than off:
`--module artifacts=50@2026-10-29`. Dropping it would switch it off.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
import os
import pathlib
import sys

# Run as `python tools/licence/issue_licence.py` from the repository root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from app.services.licence import (
    Base64Url,
    Entitlement,
    LicenceError,
    LicenceKind,
    LicencePayload,
    LicenceReading,
    LicenceSigner,
    LicenceStatus,
    LicenceVerifier,
    Module,
    RELEASE_KEYS,
    TEST_KEY_ID,
    TrustedKeys,
)
from app.services.licence.keys import TEST_KEYS


class IssueRefused(Exception):
    """The licence asked for would not work for the customer, or the key cannot be used."""


class ModuleSpec:
    """`NAME[=USERS][@YYYY-MM-DD]` from the command line."""

    @staticmethod
    def parse(text: str, *, default_expires: date | None) -> tuple[Module, Entitlement]:
        rest, _, expires_text = text.partition('@')
        name, _, users_text = rest.partition('=')
        try:
            module = Module(name.strip().lower())
        except ValueError as exc:
            raise IssueRefused(f'{name!r} is not a module: {", ".join(m.value for m in Module)}') from exc
        expires_day = date.fromisoformat(expires_text) if expires_text else default_expires
        if expires_day is None:
            raise IssueRefused(f'{module.value} has no expiry: pass --expires or {module.value}@YYYY-MM-DD')
        users = int(users_text) if users_text else None
        return module, Entitlement(users=users, expires=Dates.midnight(expires_day))


class Dates:
    @staticmethod
    def midnight(day: date) -> datetime:
        """A licence lapses at 00:00 UTC on its expiry date."""
        return datetime.combine(day, time(0, 0), tzinfo=timezone.utc)


class SigningKey:
    """Ignite Ember's private key, from wherever the issuing machine keeps it."""

    @staticmethod
    def load(key_file: str | None) -> Ed25519PrivateKey:
        path = key_file or os.environ.get('IGNI_LICENCE_KEY_FILE')
        if path:
            pem = pathlib.Path(path).expanduser().read_bytes()
        elif os.environ.get('IGNI_LICENCE_KEY'):
            pem = os.environ['IGNI_LICENCE_KEY'].encode('utf-8')
        else:
            raise IssueRefused('no signing key: pass --key-file, or set IGNI_LICENCE_KEY_FILE or IGNI_LICENCE_KEY')
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise IssueRefused('the signing key is not an Ed25519 key')
        return key

    @staticmethod
    def inside_a_repository(path: pathlib.Path) -> bool:
        return any((parent / '.git').exists() for parent in [path, *path.parents])


class LicenceIssuer:
    """The three commands."""

    def keygen(self, args: argparse.Namespace) -> int:
        out = pathlib.Path(args.out).expanduser().resolve()
        if SigningKey.inside_a_repository(out.parent):
            raise IssueRefused(
                f'{out.parent} is inside a git working tree; keep the signing key out of every repository'
            )
        if out.exists():
            raise IssueRefused(f'{out} exists; a signing key is never overwritten')
        key = Ed25519PrivateKey.generate()
        out.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(
                key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
            )
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(f'Private key written to {out} (mode 600). Keep it offline and backed up.')
        print('Add the public half to RELEASE_KEYS in every copy of app/licence/keys.py')
        print('(igni-server app/services/licence/keys.py) and in igni__cloudvm crates/licence/src/keys.rs:')
        print(f"    '{args.key_id}': '{Base64Url.encode(public)}',")
        return 0

    def issue(self, args: argparse.Namespace) -> int:
        try:
            default_expires = date.fromisoformat(args.expires) if args.expires else None
            modules = dict(ModuleSpec.parse(spec, default_expires=default_expires) for spec in args.module)
        except ValueError as exc:  # a bad date or number; ValidationError is one too
            raise IssueRefused(f'cannot read the modules: {exc}') from exc
        if Module.AUTOPILOT in modules and Module.FACTORY not in modules:
            raise IssueRefused('Autopilot needs the Factory: add --module factory')
        issued = Dates.midnight(date.fromisoformat(args.issued)) if args.issued else datetime.now(timezone.utc)
        try:
            payload = LicencePayload(
                format=1,
                licence_id=args.licence_id,
                kind=LicenceKind(args.kind),
                customer=args.customer,
                issued=issued.replace(microsecond=0),
                modules=modules,
            )
        except ValidationError as exc:
            raise IssueRefused(exc.errors()[0]['msg']) from exc
        signed = LicenceSigner(SigningKey.load(args.key_file), key_id=args.key_id).sign(payload)
        out = pathlib.Path(args.out)
        out.write_bytes(signed)
        print(f'{payload.licence_id} for {payload.customer} written to {out}')
        return 0

    def inspect(self, args: argparse.Namespace) -> int:
        raw = pathlib.Path(args.file).read_bytes()
        keys = dict(RELEASE_KEYS) | (TEST_KEYS if args.test_key else {})
        try:
            payload = LicenceVerifier(TrustedKeys.parse(keys)).verify(raw)
        except LicenceError as exc:
            print(f'NOT ACCEPTED ({exc.reason}): {exc}')
            return 1
        print(payload.model_dump_json(indent=2))
        now = datetime.now(timezone.utc)
        for module in payload.modules:
            status = LicenceStatus.of(LicenceReading(payload=payload), module, now=now)
            print(f'{module.value:10} {status.state.value:10} {status.message or ""}')
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Issue igni licence files (Ignite Ember only).')
    commands = parser.add_subparsers(dest='command', required=True)

    keygen = commands.add_parser('keygen', help='make a new signing key pair')
    keygen.add_argument('--out', required=True, help='where to write the private key (outside any repository)')
    keygen.add_argument('--key-id', required=True, help='the id licences signed with it will name, e.g. ie-2026')

    issue = commands.add_parser('issue', help='sign a licence file')
    issue.add_argument('--key-file', help='the private key (or IGNI_LICENCE_KEY_FILE / IGNI_LICENCE_KEY)')
    issue.add_argument('--key-id', required=True)
    issue.add_argument('--licence-id', required=True)
    issue.add_argument('--customer', required=True)
    issue.add_argument('--kind', choices=[kind.value for kind in LicenceKind], default=LicenceKind.COMMERCIAL.value)
    issue.add_argument('--issued', help='YYYY-MM-DD; today if omitted')
    issue.add_argument('--expires', help='YYYY-MM-DD for every module without its own @date')
    issue.add_argument('--module', action='append', required=True, help='NAME[=USERS][@YYYY-MM-DD]; repeat')
    issue.add_argument('--out', required=True)

    inspect = commands.add_parser('inspect', help='verify a licence file and say where each module stands')
    inspect.add_argument('file')
    inspect.add_argument('--test-key', action='store_true', help=f'also accept the test key ({TEST_KEY_ID})')

    args = parser.parse_args(argv)
    issuer = LicenceIssuer()
    try:
        return {'keygen': issuer.keygen, 'issue': issuer.issue, 'inspect': issuer.inspect}[args.command](args)
    except IssueRefused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
