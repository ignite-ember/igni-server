"""A VM profile request's version covers `internet` whatever its value

t0026 let a request ask for the internet. To keep every request kept
before then at the version it had, and so at its decision, the version
left `internet` out while it was false, and a request kept before t0026
has no `internet` in its settings at all. This stores every request as
it reads now: `internet` written into its settings (false where it is
missing) and its version the digest of all of it, so the version is one
rule with no field left out. A request asked again afterwards with the
same values has the same version, and keeps its decision.

The digest is frozen here — `ProfileVersion.digest` of
`{'requested': settings}` — so this migration writes the same versions
however the app's code changes later. The downgrade puts back the old
rule: `internet` left out of the version while it is false.

Revision ID: t0028
Revises: t0027
Create Date: 2026-11-08

"""

import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0028'
down_revision: Union[str, Sequence[str], None] = 't0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROWS = sa.text('SELECT id, settings FROM vm_profile_requests')
_STORE = sa.text(
    'UPDATE vm_profile_requests SET settings = CAST(:settings AS JSONB), version = :version WHERE id = :id'
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]


def upgrade() -> None:
    conn = op.get_bind()
    for request_id, settings in conn.execute(_ROWS).all():
        stored = {**settings, 'internet': bool(settings.get('internet', False))}
        conn.execute(
            _STORE,
            {'id': request_id, 'settings': json.dumps(stored), 'version': _digest({'requested': stored})},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for request_id, settings in conn.execute(_ROWS).all():
        covered = {field: value for field, value in settings.items() if not (field == 'internet' and value is False)}
        conn.execute(
            _STORE,
            {'id': request_id, 'settings': json.dumps(settings), 'version': _digest({'requested': covered})},
        )
