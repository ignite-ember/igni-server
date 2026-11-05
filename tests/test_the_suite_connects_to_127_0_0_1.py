"""The suite reaches its Postgres and Redis at 127.0.0.1, not `localhost`.

`localhost` resolves to ::1 first on macOS. The test containers publish on
127.0.0.1 only, so the first attempt went to whatever else held that port
on ::1 (Docker Desktop answers there) and failed with "invalid response to
SSL negotiation". The driver then tried 127.0.0.1 and usually got through,
so a full run had about two such failures, in different tests each time,
all passing alone: the kind of red that teaches people to re-run.

An address has one meaning; a name has as many as the resolver gives it.
"""

from __future__ import annotations

import pathlib
import re

from sqlalchemy.engine import make_url

from tests.conftest import TEST_DB_URL_ASYNC, TEST_DB_URL_SYNC

_WORKFLOW = pathlib.Path(__file__).resolve().parent.parent / '.github/workflows/ci.yml'


def test_postgres_is_reached_by_address():
    for url in (TEST_DB_URL_ASYNC, TEST_DB_URL_SYNC):
        assert make_url(url).host == '127.0.0.1', url


def test_ci_reaches_redis_by_address():
    """Redis is read through settings, so the workflow names its host."""
    text = _WORKFLOW.read_text()
    start = text.index('      - name: Pytest')
    block = text[start : text.index('run:', start)]

    assert re.search(r'^\s+REDIS_HOST:\s*127\.0\.0\.1\s*$', block, re.M), block
