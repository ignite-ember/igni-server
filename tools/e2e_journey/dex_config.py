"""Write Dex's configuration for one journey run.

Dex is the deployment's identity provider for the run: one static client
(igni server) and a password database holding the people the journey
signs in through it. Its storage is memory, so a restart with a new file
is a new directory.

    python -m tools.e2e_journey.dex_config --domain acme-1a2b3c.com --issuer http://localhost:55561/dex \
        --client-id igni-e2e --client-secret ... --redirect-uri http://localhost:17801/v1/auth/oidc/callback \
        --out run/dex/config.yaml
"""

import argparse
import json
from pathlib import Path
import uuid

import bcrypt
from pydantic import BaseModel

from tools.e2e_journey.people import SSO_PEOPLE


class StaticPassword(BaseModel):
    """One entry of Dex's ``staticPasswords``."""

    email: str
    hash: str
    username: str
    userID: str


def config(*, domain: str, issuer: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    """The YAML Dex serves from. JSON is YAML, and needs no YAML library here."""
    people = [
        StaticPassword(
            email=f'{person.local_part}@{domain}',
            # Cost 10 is Dex's own minimum.
            hash=bcrypt.hashpw(person.password.encode(), bcrypt.gensalt(rounds=10)).decode(),
            username=person.full_name,
            userID=str(uuid.uuid5(uuid.NAMESPACE_DNS, f'{person.local_part}@{domain}')),
        )
        for person in SSO_PEOPLE
    ]
    document = {
        'issuer': issuer,
        'storage': {'type': 'memory'},
        'web': {'http': '0.0.0.0:5556'},
        'logger': {'level': 'info'},
        # Straight back to igni after the password: the consent screen is
        # Dex's, not something a deployment's IdP necessarily shows.
        'oauth2': {'skipApprovalScreen': True},
        'staticClients': [
            {'id': client_id, 'secret': client_secret, 'name': 'igni server', 'redirectURIs': [redirect_uri]}
        ],
        'enablePasswordDB': True,
        'staticPasswords': [p.model_dump() for p in people],
    }
    return json.dumps(document, indent=2) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--client-secret', required=True)
    parser.add_argument('--redirect-uri', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        config(
            domain=args.domain,
            issuer=args.issuer,
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
        )
    )


if __name__ == '__main__':
    main()
