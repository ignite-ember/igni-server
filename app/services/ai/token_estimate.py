"""Token counts for requests whose provider did not report any.

A fallback, and only that: when an upstream returns a `usage` block it
is used verbatim, because the provider is the one billing. This is what
fills the gap so a request still lands in `chat_usage` with something
other than zero.

The encoder used to be a module-level `_tiktoken_encoder` mutated
through `global`, with `False` overloaded into the same slot to mean
"tried and unusable" — so the cache could not tell that apart from "not
tried yet" without an `or None` on the way out. It is a class now and
the two states are two fields.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.utils.singleton import SingletonMeta

#: tiktoken's cache directory, shipped with the server. `get_encoding`
#: otherwise downloads `cl100k_base` from openaipublic.blob.core.windows.net
#: on first use, which an air-gapped deployment cannot reach and should not
#: try to. The one file here is named as tiktoken names its cache entries
#: (the sha1 of the download URL), and tiktoken still checks its sha256
#: (223921b7…) before using it.
TIKTOKEN_CACHE = Path(__file__).with_name('tiktoken_cache')


class TokenEstimator(metaclass=SingletonMeta):
    """A lazily-loaded `cl100k_base` encoder, or none at all.

    `tiktoken` is imported on first use rather than at module scope
    because loading its tables is the expensive part and a deployment
    whose providers all report usage never needs it.
    """

    def __init__(self) -> None:
        self._encoder: Any = None
        self._tried = False

    @property
    def encoder(self) -> Any:
        """The encoder, or None if it could not be loaded.

        Tried exactly once. A failure is remembered, so a missing or
        broken `tiktoken` costs one import attempt per process rather
        than one per request.
        """
        if not self._tried:
            self._tried = True
            try:
                # Only when the deployment has not chosen a cache of its own.
                os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(TIKTOKEN_CACHE))
                import tiktoken

                self._encoder = tiktoken.get_encoding('cl100k_base')
            except Exception:  # noqa: BLE001 — an estimate is not worth a 500
                self._encoder = None
        return self._encoder

    def estimate(self, *, body: dict, output_pieces: list[str]) -> tuple[int, int, int]:
        """`(input, output, total)`, or zeros if nothing can be counted.

        Zeros rather than an exception: this runs after the response has
        been delivered, and a usage row that reads zero is a worse
        outcome than no row only in a report nobody has written yet.
        """
        encoder = self.encoder
        if encoder is None:
            return 0, 0, 0
        try:
            input_text = ' '.join(
                message.get('content', '') for message in body.get('messages', []) if message.get('content')
            )
            inputs = len(encoder.encode(input_text))
            outputs = len(encoder.encode(''.join(output_pieces)))
        except Exception:  # noqa: BLE001 — same reason
            return 0, 0, 0
        return inputs, outputs, inputs + outputs

    def forget_for_tests(self) -> None:
        """Drop the cached encoder so the next call loads it again."""
        self._encoder = None
        self._tried = False
