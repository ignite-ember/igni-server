"""A session minted just after "sign out everywhere" must not be born withdrawn.

The watermark is rounded up to the next whole second and a token's `iat`
is whole seconds, so for up to two seconds after a withdrawal every token
minted is at or before it — refused on its first request
(:meth:`~app.db.sql.account_sessions.AccountSessionsDB.withdraw_sessions`
explains why the margins are there). Signing in during that window
answered 200 with a token that could never work, so a script that signed
straight back in held a dead credential while INCIDENT_RESPONSE.md C1
promises "they sign in again and get a new one".

So every sign-in waits out the rest of that window before it mints:
at most about two seconds, and only right after a withdrawal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time

from app.db.sql.account_sessions import AccountSessionsDB

#: The longest a sign-in waits. The window is at most two seconds; a
#: watermark further ahead than this is clock skew between this replica
#: and the database (alerted on as `igni_clock_skew_seconds`), and waiting
#: would not help the person in front of it.
MAX_WAIT_SECONDS = 3.0


class FreshSessions:
    def __init__(
        self,
        *,
        sessions: AccountSessionsDB | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sessions = sessions or AccountSessionsDB()
        self._clock = clock
        self._sleep = sleep

    async def wait_until_honoured(self, user_id: str) -> None:
        """Return once a token minted now postdates the person's watermark."""
        watermark = await self._sessions.watermark(user_id)
        if watermark is None:
            return
        # `iat` is this clock truncated to the second, and it must be
        # strictly after the watermark: the first whole second past it.
        delay = watermark.timestamp() + 1 - self._clock()
        if 0 < delay <= MAX_WAIT_SECONDS:
            await self._sleep(delay)
