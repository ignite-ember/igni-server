"""Database layer for user credentials."""

from datetime import datetime, timezone
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.admission import Admission
from app.db.sql.group_person_erasure import GroupPersonErasureDB
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.organisation_deletion_notices import OrganisationDeletionNoticeDB
from app.db.sql.schemas.org_domain import NotACompanyAddress
from app.db.sql.tenant_registry import TenantRegistryDB
from app.security.account_domains import refuse_public_mail
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)

#: The unique index a duplicate violates.
_EMAIL_INDEX = 'users_email_key'

#: Serialises every change to who is a super admin. A transaction-scoped
#: advisory lock: two first sign-ups arriving together would otherwise
#: both see "no super admin yet" and both become one, and two removals
#: together could each see another super admin left and leave none.
_SUPER_ADMIN_LOCK = 0x5355504552


class LastSuperAdmin(Exception):
    """Removing this super admin would leave the deployment with none.

    Nobody could then create an organisation or make anybody else a
    super admin, and there is no way back from inside the product.
    """


class UserDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Database operations for local user credentials."""

    def __init__(self, *, group_erasure: GroupPersonErasureDB | None = None) -> None:
        self._group_erasure = group_erasure or GroupPersonErasureDB()

    async def create(
        self,
        email: str,
        hashed_password: str,
        full_name: str | None = None,
        auth_provider: str = 'email',
    ) -> User:
        """Create a new user with a pre-hashed password.

        Refuses a personal mail-provider address before anything else,
        including before the duplicate check. Here rather than in the
        sign-up endpoint because this is the single place every way of
        making an account goes through — password sign-up, and the
        OAuth providers, which arrive with an address chosen at
        Google or the identity provider rather than typed into our form. A check in
        the endpoint would guard one of those. See
        ``app/security/account_domains.py`` for why the line exists.
        """
        refuse_public_mail(email)
        async with self.session_scope() as session:
            existing = (await session.execute(select(User).where(User.email == email))).scalars().first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='User already registered',
                )

            # Who may have an account, and where they belong, decided
            # under the lock: two first sign-ups must not both be the
            # bootstrap account. See `app.db.sql.admission`.
            first = await self._nobody_is_super_admin(session)
            admission = Admission(session)
            joins = await admission.joining(email)

            now = datetime.now(timezone.utc)
            user = User(
                id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()),
                email=email,
                hashed_password=hashed_password,
                full_name=full_name,
                auth_provider=auth_provider,
                created_at=now,
                updated_at=now,
                # The first account a deployment creates sets it up.
                is_super_admin=first,
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError as exc:
                # The pre-check above is a check-then-act, and this is the
                # sign-up path: two submissions of the same address arriving
                # together both pass it, and the second insert hits
                # ``users.email``. The message matches the pre-check's so a
                # caller cannot tell which one answered.
                # Matched on the index name rather than a substring of the
                # driver's message — the latter was users.email is NOT NULL, so `null value in column "email"` also
                # contains the word and would have been reported as a
                # duplicate address.
                if _EMAIL_INDEX not in str(getattr(exc, 'orig', exc)):
                    raise
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='User already registered',
                ) from exc
            if joins is not None:
                await admission.admit(org=joins, user_id=str(user.user_id), email=email)
            return user

    async def get_by_email(self, email: str) -> User | None:
        """Look up user by email."""
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(User.email == email))
            return result.scalars().first()

    async def by_address(self, email: str) -> User | None:
        """The account with this address, however it is capitalised."""
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(func.lower(User.email) == email.strip().casefold()))
            return result.scalars().first()

    async def get_by_user_id(self, user_id: str) -> User | None:
        """Look up user by user_id."""
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(User.user_id == user_id))
            return result.scalars().first()

    async def by_user_ids(self, user_ids: list[str]) -> list[User]:
        """The users among *user_ids*, in one query; ids with no account are left out."""
        if not user_ids:
            return []
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(User.user_id.in_(user_ids)))
            return list(result.scalars().all())

    async def update_password(self, user_id: str, hashed_password: str) -> None:
        """Update a user's password (expects pre-hashed value)."""
        async with self.session_scope() as session:
            row = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='User not found',
                )
            row.hashed_password = hashed_password  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()

    async def update_full_name(self, user_id: str, full_name: str | None) -> None:
        """Update a user's display name."""
        async with self.session_scope() as session:
            row = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='User not found',
                )
            row.full_name = full_name  # type: ignore[assignment]
            row.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            await session.flush()

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
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail='User not found',
                )
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

        Answers the instant recorded, or ``None`` on reactivation.
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
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
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
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
            when: datetime = row[0]
        return when

    async def note_sign_in(self, user_id: str) -> None:
        """Record that this user authenticated successfully, just now.

        Called wherever a token is minted for a resolved user. Kept as one
        method rather than four inline `UPDATE`s so that "what counts as a
        sign-in" has a single definition, and so
        `test_access_review.py::test_every_token_mint_records_a_sign_in`
        can count call sites against `create_token` call sites — a rule
        rather than four places somebody has to remember, which is the
        lesson F80, F88 and F90 all record.

        Never raises. A failed bookkeeping write must not turn a
        successful sign-in into an error the user sees, and the cost of
        losing one is a slightly stale access review rather than a lost
        audit record — `audit_events` has the authentication attempt
        either way.
        """
        try:
            async with self.session_scope() as session:
                await session.execute(
                    update(User).where(User.user_id == user_id).values(last_sign_in_at=datetime.now(timezone.utc))
                )
        except Exception:
            logger.exception('Could not record the sign-in for %s', user_id)

    async def ensure_exists(
        self,
        user_id: str,
        email: str,
        full_name: str | None = None,
        auth_provider: str = 'email',
    ) -> None:
        """Idempotently insert a user row. Used to seed the local dev
        bypass user so downstream CRUD (profile update, etc.) can find it
        without forcing every dev to sign up through the OAuth flow.
        """
        async with self.session_scope() as session:
            existing = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if existing:
                return
            now = datetime.now(timezone.utc)
            session.add(
                User(
                    id=user_id,
                    user_id=user_id,
                    email=email,
                    hashed_password='',
                    full_name=full_name,
                    auth_provider=auth_provider,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()

    async def move_to_company_address(self, user_id: str, email: str) -> Organization:
        """Move an account in no organisation onto a company address, joining its organisation.

        For the bootstrap account: it was made under whatever address the
        installer had, before any organisation owned a domain. Once one
        does, it moves onto an address there — the old one is gone — and
        joins that organisation like anybody signing up with it would.

        409 when the account is already in an organisation (its address
        decides which, so it is not changed here) or the address is
        taken; `NotACompanyAddress` when no organisation owns the address's domain.
        """
        async with self.session_scope() as session:
            await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _SUPER_ADMIN_LOCK})
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if user is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such account.')
            member = (
                await session.execute(
                    select(OrgMembership.id).where(OrgMembership.user_id == user_id, OrgMembership.status == 'active')
                )
            ).first()
            if member is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='Your address decides your organisation, so it is not changed here.',
                )
            taken = (
                await session.execute(select(User.user_id).where(func.lower(User.email) == email.casefold()))
            ).first()
            if taken is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='That address already has an account.')
            admission = Admission(session)
            joins = await admission.owner_of(email)
            if joins is None:
                raise NotACompanyAddress()
            user.email = email
            user.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await admission.admit(org=joins, user_id=user_id, email=email)
            return joins

    @staticmethod
    async def _nobody_is_super_admin(session) -> bool:
        """Whether this deployment has no super admin yet, holding the lock until commit."""
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _SUPER_ADMIN_LOCK})
        found = await session.execute(select(User.user_id).where(User.is_super_admin.is_(True)).limit(1))
        return found.first() is None

    async def super_admins(self) -> list[User]:
        """Everyone who may create organisations, oldest account first."""
        async with self.session_scope() as session:
            result = await session.execute(select(User).where(User.is_super_admin.is_(True)).order_by(User.created_at))
            return list(result.scalars().all())

    async def set_super_admin(self, user_id: str, *, super_admin: bool) -> User | None:
        """Make somebody a super admin, or stop them being one.

        ``None`` when there is no such account. Raises
        :class:`LastSuperAdmin` rather than leave the deployment with
        none. Idempotent: setting what is already set changes nothing.
        """
        async with self.session_scope() as session:
            await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _SUPER_ADMIN_LOCK})
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if user is None:
                return None
            if not super_admin and user.is_super_admin and await self._count_super_admins(session) <= 1:
                raise LastSuperAdmin(user_id)
            user.is_super_admin = super_admin
            user.updated_at = datetime.now(timezone.utc)
            await session.flush()
            return user

    @staticmethod
    async def _count_super_admins(session) -> int:
        result = await session.execute(select(func.count()).select_from(User).where(User.is_super_admin.is_(True)))
        return int(result.scalar_one())

    async def delete(self, user_id: str) -> None:
        """Permanently delete a user record."""
        async with self.session_scope() as session:
            await session.execute(User.__table__.delete().where(User.user_id == user_id))
            await session.flush()

    async def delete_account(
        self,
        *,
        user_id: str,
        delete_org_ids: list[str],
        leave_org_ids: list[str],
    ) -> None:
        """Remove a person and everything that is only theirs, atomically.

        **One session, deliberately.** `UserService.delete_account`
        used to do this across three `*DB` classes — `OrganizationDB`
        for the orgs, `TeamDB` for the memberships, `UserDB` for the
        row — and each of those opens its own `session_scope`, so each
        committed on its own. A failure part-way through left an
        account half-removed: organisations gone, memberships gone,
        and the person still able to sign in, with nothing to say so.
        That is the worst outcome available on a deletion request,
        because it is the one a person will not discover until they
        try.

        The policy stays in the service — *which* orgs are deleted
        versus left, and the refusal when leaving would orphan one —
        because it needs the membership roles and this does not. What
        moves here is the writing, which is the part that has to be
        all-or-nothing.

        Ordered so the user row goes last: a failure at any earlier
        step rolls the whole transaction back, and leaving it last
        means the row that proves the request has not completed is
        the last thing to go.
        """
        async with self.session_scope() as session:
            # The same rule as `set_super_admin`: an account deleting
            # itself must not take the last super admin with it.
            await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': _SUPER_ADMIN_LOCK})
            leaving = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if leaving is not None and leaving.is_super_admin and await self._count_super_admins(session) <= 1:
                raise LastSuperAdmin(user_id)
            if delete_org_ids:
                # Memberships first: `organizations` has no cascade to
                # them, which is why `OrganizationDB.delete` deletes
                # both in that order too.
                await session.execute(OrgMembership.__table__.delete().where(OrgMembership.org_id.in_(delete_org_ids)))
                await session.execute(Organization.__table__.delete().where(Organization.id.in_(delete_org_ids)))
                # And every service owed the news, in this transaction:
                # see `OrganizationDB.delete`.
                await OrganisationDeletionNoticeDB.owe(session, delete_org_ids, now=datetime.now(timezone.utc))
            if leave_org_ids:
                await session.execute(
                    OrgMembership.__table__.delete().where(
                        OrgMembership.org_id.in_(leave_org_ids),
                        OrgMembership.user_id == user_id,
                    )
                )
            await session.execute(User.__table__.delete().where(User.user_id == user_id))
            await session.flush()

        # Then each organisation's own database, which the transaction
        # above cannot reach. The account is already gone, so a failure
        # here leaves a stale group row for nobody, never a person who can
        # still sign in — the outcome the single transaction exists to
        # rule out.
        for org_id in delete_org_ids:
            await TenantRegistryDB().detach(org_id)
        for org_id in leave_org_ids:
            try:
                await self._group_erasure.forget_person(org_id, user_id=user_id)
            except Exception:  # noqa: BLE001 — the account is deleted; this is cleanup
                logger.exception('Could not clear a deleted account from organisation %s', org_id)
