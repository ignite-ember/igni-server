"""Token counts for requests whose provider did not report any.

A fallback, and only that: when an upstream returns a `usage` block it
is used verbatim, because the provider is the one billing. This is what
fills the gap so a request still lands in `chat_usage` with something
other than zero.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import tiktoken

from app.api.base_v1.schema.chat import ChatMessage, CompletionUsage

logger = logging.getLogger(__name__)

#: tiktoken's cache directory, shipped with the server. `get_encoding`
#: otherwise downloads `cl100k_base` from openaipublic.blob.core.windows.net
#: on first use, which an air-gapped deployment cannot reach and should not
#: try to. The one file here is named as tiktoken names its cache entries
#: (the sha1 of the download URL), and tiktoken still checks its sha256
#: (223921b7…) before using it.
TIKTOKEN_CACHE = Path(__file__).with_name('tiktoken_cache')


class TokenEstimator:
    """A lazily-loaded `cl100k_base` encoder, or none at all.

    One instance per app, injected into whatever estimates. The tables
    are loaded on first use, so a deployment whose providers all report
    usage never reads them.
    """

    def __init__(self) -> None:
        self._encoder: tiktoken.Encoding | None = None
        self._tried = False

    @property
    def encoder(self) -> tiktoken.Encoding | None:
        """The encoder, or None if it could not be loaded.

        Tried exactly once. A failure is logged and remembered, so a
        missing or corrupt table costs one attempt per instance rather
        than one per request.
        """
        if not self._tried:
            self._tried = True
            # Only when the deployment has not chosen a cache of its own.
            os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(TIKTOKEN_CACHE))
            try:
                self._encoder = tiktoken.get_encoding('cl100k_base')
            except (OSError, ValueError):
                logger.warning('cl100k_base could not be loaded; estimated usage will read zero', exc_info=True)
        return self._encoder

    def estimate(self, *, messages: list[ChatMessage], output_pieces: list[str]) -> CompletionUsage:
        """The prompt's and the output's token counts, or zeros if there is no encoder.

        Zeros rather than an exception: this runs after the response has
        been delivered. Only text content is counted, and a special-token
        string in it (`<|endoftext|>`) counts as plain text rather than
        being refused.
        """
        encoder = self.encoder
        if encoder is None:
            return CompletionUsage()
        prompt = ' '.join(
            message.content for message in messages if isinstance(message.content, str) and message.content
        )
        inputs = len(encoder.encode(prompt, disallowed_special=()))
        outputs = len(encoder.encode(''.join(output_pieces), disallowed_special=()))
        return CompletionUsage(prompt_tokens=inputs, completion_tokens=outputs, total_tokens=inputs + outputs)
