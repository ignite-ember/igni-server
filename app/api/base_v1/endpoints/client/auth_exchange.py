"""Trading a one-time code for the token it stands for.

F107. See ``app/db/redis/auth_handoff.py`` for why the token is not in
the redirect any more.

## Unauthenticated, necessarily

This is how a caller becomes authenticated, so it cannot require a
token. What it requires instead is a code that was issued seconds
earlier by a callback this server handled, has not been used, and has
not expired.

## POST, and the reason is the same one as F96's

A ``GET /v1/auth/exchange?code=...`` would put the code in a URL — the
access log, the history, the referrer — which is the problem being
fixed. It would also make the response, which contains a bearer token,
something a cache could plausibly store.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.db.redis.auth_handoff import AuthHandoffDB

logger = logging.getLogger(__name__)

router = APIRouter()

_handoff = AuthHandoffDB()


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, description='The one-time code from the sign-in redirect')


class ExchangeResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'


@router.post(
    '/auth/exchange',
    response_model=ExchangeResponse,
    description='Exchange a one-time sign-in code for an access token. The code is consumed.',
)
async def exchange(body: ExchangeRequest) -> ExchangeResponse:
    token = await _handoff.redeem(body.code)

    if not token:
        # One message for expired, already-used and never-existed. The
        # difference matters to nobody except somebody probing, for whom
        # "this code was valid a moment ago" is a useful signal.
        logger.info('Sign-in code exchange refused')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='This sign-in code is not valid. It may have expired or already been used — sign in again.',
        )

    return ExchangeResponse(access_token=token)
