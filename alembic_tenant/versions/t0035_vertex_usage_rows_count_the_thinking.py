"""Usage rows Vertex AI's Gemini served before t0034 count its thinking

Before t0034 a `vertex_openai` row recorded `completion_tokens` as its
output, which leaves Gemini's thinking out while `total_tokens` counts it
(R529). For those rows the thinking is exactly `total - input - output`,
so it is put back: `output_tokens` becomes `total - input`, and
`reasoning_tokens` the difference. The value the row held is kept in
`output_tokens_reported`, which is both the record of what this changed
and what the downgrade restores.

Idempotent: a corrected row has `total = input + output` and a value in
`output_tokens_reported`, so running it again touches nothing. Rows of
any other kind, and rows whose total is not above input plus output, are
never touched.

Revision ID: t0035
Revises: t0034
Create Date: 2026-11-21

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0035'
down_revision: Union[str, Sequence[str], None] = 't0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger('alembic.runtime.migration')

# Frozen here rather than read from `BackendKind`: a migration says what it did.
_VERTEX_OPENAI = 'vertex_openai'

_CORRECT = """
UPDATE chat_usage
   SET output_tokens_reported = output_tokens,
       reasoning_tokens = total_tokens - input_tokens - output_tokens,
       output_tokens = total_tokens - input_tokens
 WHERE kind = :kind
   AND total_tokens > input_tokens + output_tokens
   AND output_tokens_reported IS NULL
"""

_RESTORE = """
UPDATE chat_usage
   SET output_tokens = output_tokens_reported,
       reasoning_tokens = NULL
 WHERE output_tokens_reported IS NOT NULL
"""


def upgrade() -> None:
    op.execute('ALTER TABLE chat_usage ADD COLUMN IF NOT EXISTS output_tokens_reported INTEGER')
    corrected = op.get_bind().execute(sa.text(_CORRECT), {'kind': _VERTEX_OPENAI}).rowcount
    logger.info('t0035: %s vertex_openai usage rows now count their thinking as output', corrected)


def downgrade() -> None:
    restored = op.get_bind().execute(sa.text(_RESTORE)).rowcount
    logger.info('t0035: %s usage rows restored to the output their provider reported', restored)
    op.drop_column('chat_usage', 'output_tokens_reported')
