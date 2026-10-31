"""What went wrong with an inference node, and whether to ask another.

Every call this server makes to a GPU meets the same four failures: no
answer, an answer that is an error, an answer that is unparseable, and
an answer that stops partway. Each one has a different remedy and only
some of them are worth a second node.

Lifted out of the chat endpoint, which is still its only caller — the
second one it was extracted for, embeddings, never used it and has
since left this server for igni__artifacts. Kept out anyway: inside
the endpoint, the streaming and non-streaming paths had each grown
their own copy of this policy, and the two disagreed for months about
whether a node that answered badly deserved another try. Which
behaviour you got depended on `stream: true`. A single definition that
both import is what stops that recurring, and it does not stop being
worth it because the caller count went back to one.

This module is deliberately HTTP-free. It says what happened and
whether to try again; turning that into a status code is the caller's
job.
"""

from __future__ import annotations

import httpx

#: Attempts per request, including the first.
MAX_RETRIES = 3

#: Seconds before attempt *n*. A retry normally lands on a *different*
#: node, so the pause is not there to let anything recover — it is there
#: for the case the pool has one node, or hands the same one back,
#: where retrying instantly is just hitting a struggling machine three
#: times in a row. Short, because somebody is waiting on this request.
RETRY_BACKOFF = 0.25


class UpstreamStale(Exception):
    """The node stopped sending data for too long, mid-answer."""


class UpstreamError(Exception):
    """The node answered with a non-200 status."""

    #: `body` is kept for logging only — the streaming path reads it as
    #: bytes, the non-streaming one as text. Never put on the wire: the
    #: body of a failed completion can carry the prompt back.
    def __init__(self, status_code: int, body: bytes | str):
        self.status_code = status_code
        self.body = body
        super().__init__(f'Upstream returned {status_code}')


class UpstreamUnreadable(Exception):
    """A 200 carrying something that is not the response we asked for.

    Distinct from `UpstreamError` because there is no status to reason
    about — the node said everything was fine and then sent nonsense,
    which is a broken node rather than a refused request, and always
    worth asking somebody else.
    """

    def __init__(self, excerpt: str):
        self.excerpt = excerpt
        super().__init__('Upstream returned an unreadable body')


def worth_another_node(exc: Exception) -> bool:
    """Whether a different node could plausibly answer this.

    A 4xx is about the *request*, and the request does not change
    between nodes — retrying it burns three nodes to collect the same
    refusal three times, and delays telling the caller the one thing
    that would help. 408 and 429 are the exceptions: those are about
    this node's moment rather than the request.

    Everything else — no answer, a 5xx, a silent stream, a 200 full of
    something that is not JSON — is a property of the node that
    happened to be picked, which is precisely what another node fixes.
    """
    if isinstance(exc, UpstreamError):
        return exc.status_code in (408, 429) or exc.status_code >= 500
    return True


def describe(exc: Exception) -> tuple[int, str]:
    """`(status, sentence)` for a failure, once retrying is over.

    The status is what an HTTP caller should answer with; a caller that
    is not serving a request can ignore it and use the sentence.

    Each failure gets its own sentence. They are different problems
    with different fixes — a node that refused the request, a node that
    answered with nonsense, and no node answering at all are three
    things, and collapsing them into one line is how a reason becomes
    unactionable.
    """
    if isinstance(exc, UpstreamError):
        if exc.status_code == 429:
            return 429, 'Every inference node is at capacity. Please retry shortly.'
        if 400 <= exc.status_code < 500:
            # Passed through rather than flattened to 502: the caller
            # asked for something this model will not do — too long a
            # context, a message sequence it rejects — and a 502 would
            # send them to look at our infrastructure for it.
            return exc.status_code, f'The inference node refused the request ({exc.status_code}).'
        return 502, f'The inference node returned {exc.status_code}.'
    if isinstance(exc, UpstreamUnreadable):
        return 502, 'The inference node returned an unreadable response.'
    if isinstance(exc, UpstreamStale | httpx.TimeoutException):
        # Distinguished from "did not answer": these nodes *did* answer
        # and then stopped partway. Somebody reading it after a long
        # wait needs to know their half-written reply is not coming
        # back, not that nothing was ever reached.
        return 504, 'The inference node stopped responding partway through.'
    return 502, 'No inference node answered. Try again shortly.'
