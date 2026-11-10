"""Database layer for whether a person's tokens are still honoured."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.schemas.account_errors import AccountNotFound
from app.utils.singleton import SingletonMeta


class AccountSessionsDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """The watermark every token's `iat` is compared against, and the deactivation mark that moves it."""

    async def withdraw_sessions(self, user_id: str) -> datetime:
        """Refuse every token this person already holds, from now on.

        DEC-10/B5. Sets the watermark `require_existing_user` compares
        each token's `iat` against, so revocation costs one column and no
        per-token state — there is no session table to sweep and nothing
        to keep in Redis.

        Returns the watermark rather than nothing, so the caller can tell
        the person *when* and so the response is a fact from the database
        instead of a second `now()` that disagrees with it by a
        millisecond.

        The one-second nudge is the whole subtlety. A token minted in the
        same second as the revocation carries an `iat` truncated to that
        second (JWT `iat` is integer seconds, RFC 7519 §4.1.6), so with a
        sub-second watermark `iat <= tokens_valid_after` compares
        `12:00:00` against `12:00:00.4` and the token *survives* the
        revocation that was meant to kill it. Rounding the watermark up to
        the next whole second closes that window; the cost is that a
        token minted in the second *after* the click is also refused,
        which is the safe direction and is what the caller asked for
        anyway.

        **The database's clock, not this process's** (B8). The comparison
        this feeds is between a token's `iat` — stamped by whichever API
        replica minted it — and this column. With the watermark also
        taken from an app clock, revocation depended on two arbitrary
        replica clocks, and a replica five seconds ahead minted tokens
        that *survived* a revocation issued from a replica in step:
        demonstrated, `iat` 1788509183 against a watermark of
        1788509179. Taking the watermark from Postgres leaves one gap
        instead of N — app clock against database clock — and that gap
        is measured and alerted on (`igni_clock_skew_seconds`).

        The rounding moves into SQL with it: `date_trunc` then plus one
        second, which is the same ceiling computed by the same clock that
        stamps the row.

        :class:`AccountNotFound` for no such account.
        """
        async with self.session_scope() as session:
            # One statement rather than select-then-assign, so a delete
            # racing this cannot produce a write against a row that is
            # gone. RETURNING gives back what was actually stored, which
            # is what the caller reports to the person.
            row = (
                await session.execute(
                    text(
                        'UPDATE users SET tokens_valid_after = '
                        "date_trunc('second', now()) + interval '1 second' "
                        'WHERE user_id = :u RETURNING tokens_valid_after'
                    ),
                    {'u': user_id},
                )
            ).first()
            if row is None:
                raise AccountNotFound()
            watermark: datetime = row[0]
        return watermark

    async def set_deactivated(self, user_id: str, *, deactivated: bool) -> datetime | None:
        """Mark this person as having left, or as having come back.

        Deprovisioning, recorded on the account rather than on a
        membership — see the column's note on
        :class:`~app.db.sql.models.user.User`.

        Deactivating **withdraws their sessions too**, in the same
        statement. The two are one act: an operator deprovisioning
        somebody does not mean "stop them eventually", and leaving a
        live token working until it expires is the gap this closes.

        Reactivating clears the mark and leaves the watermark where it
        is, deliberately. Coming back is not a reason to re-admit every
        token that existed before they left — including one on the
        laptop that was the reason they left.

        Answers the instant recorded, or ``None`` on reactivation;
        :class:`AccountNotFound` for no such account.
        """
        async with self.session_scope() as session:
            if not deactivated:
                row = (
                    await session.execute(
                        text('UPDATE users SET deactivated_at = NULL WHERE user_id = :u RETURNING user_id'),
                        {'u': user_id},
                    )
                ).first()
                if row is None:
                    raise AccountNotFound()
                return None

            # One statement, for the reason ``withdraw_sessions``
            # documents: a delete racing this cannot leave a write
            # against a row that is gone. The watermark is computed the
            # same way it is there — the database's clock, rounded up to
            # the next whole second so a token minted in the same second
            # does not survive.
            row = (
                await session.execute(
                    text(
                        'UPDATE users SET deactivated_at = now(), '
                        "tokens_valid_after = date_trunc('second', now()) + interval '1 second' "
                        'WHERE user_id = :u RETURNING deactivated_at'
                    ),
                    {'u': user_id},
                )
            ).first()
            if row is None:
                raise AccountNotFound()
            when: datetime = row[0]
        return when
