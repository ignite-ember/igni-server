"""Token estimates work with no route to the internet.

`tiktoken.get_encoding` downloads its tables from
openaipublic.blob.core.windows.net on first use. An air-gapped deployment
cannot reach that host, and the server must not try: the estimator ships
the one table it uses (`app/services/ai/tiktoken_cache/`) and points
tiktoken at it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

from app.services.ai.token_estimate import TIKTOKEN_CACHE

CL100K_URL = 'https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken'
CL100K_SHA256 = '223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7'


def test_the_shipped_table_is_the_one_tiktoken_expects():
    entry = TIKTOKEN_CACHE / hashlib.sha1(CL100K_URL.encode()).hexdigest()
    assert entry.is_file()
    assert hashlib.sha256(entry.read_bytes()).hexdigest() == CL100K_SHA256


def test_the_estimator_loads_with_every_connection_refused(tmp_path: Path):
    # A fresh interpreter, so no earlier import has the table in memory,
    # with any socket connection refused outright.
    script = '''
import socket
def refuse(*a, **k):
    raise OSError("network refused in test")
socket.socket.connect = refuse
socket.create_connection = refuse
import os
os.environ.pop("TIKTOKEN_CACHE_DIR", None)
from app.services.ai.token_estimate import TokenEstimator
enc = TokenEstimator().encoder
assert enc is not None, "no encoder"
assert enc.encode("hello world") == [15339, 1917]
print("ok")
'''
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith('ok')
