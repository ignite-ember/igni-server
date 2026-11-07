"""Database layer for user credentials."""

from datetime import datetime, timezone
import logging
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.sql import SQLAlchemySessionManager
from app.db.sql.admission import Admission
from app.db.sql.models.org_membership import OrgMembership
from app.db.sql.models.organization import Organization
from app.db.sql.models.user import User
from app.db.sql.schemas.account_errors import AccountNotFound, AddressTaken, AlreadyInAnOrganisation
from app.db.sql.schemas.org_domain import NotACompanyAddress
from app.db.sql.super_admin_roster import SuperAdminRoster
from app.security.account_domains import refuse_public_mail
from app.utils.singleton import SingletonMeta

logger = logging.getLogger(__name__)


class UserDB(SQLAlchemySessionManager, metaclass=SingletonMeta):
    """Database operations for local user credentials."""

    async def create(
        self,
        email: str,
        *,
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

        Raises :class:`AddressTaken` for an address that already has an
        account — from the pre-check, or from the unique index when two
        submissions of one address race past it, with the same message
        so a caller cannot tell which one answered.
        """
        refuse_public_mail(email)
        async with self.session_scope() as session:
            existing = (await session.execute(select(User).where(User.email == email))).scalars().first()
            if existing:
                raise AddressTaken()

            # Who may have an account, and where they belong, decided
            # under the lock: two first sign-ups must not both be the
            # bootstrap account. See `app.db.sql.admission`.
            roster = SuperAdminRoster(session)
            await roster.hold()
            first = await roster.nobody()
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
                if not AddressTaken.raised_by(exc):
                    raise
                raise AddressTaken() from exc
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
        """Update a user's password (expects pre-hashed value). :class:`AccountNotFound` for no such account."""
        async with self.session_scope() as session:
            row = await self._held(session, user_id)
            row.hashed_password = hashed_password
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()

    async def update_full_name(self, user_id: str, full_name: str | None) -> None:
        """Update a user's display name. :class:`AccountNotFound` for no such account."""
        async with self.session_scope() as session:
            row = await self._held(session, user_id)
            row.full_name = full_name
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()

    async def _held(self, session: AsyncSession, user_id: str) -> User:
        """The account with this id, in *session*; :class:`AccountNotFound` when there is none."""
        row = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
        if row is None:
            raise AccountNotFound()
        return row

    async def note_sign_in(self, user_id: str) -> None:
        """Record that this user authenticated successfully, just now.

        Called wherever a token is minted for a resolved user. Kept as one
        method rather than four inline `UPDATE`s so that "what counts as a
        sign-in" has a single definition, and so
        `test_access_review.py::test_every_token_mint_records_a_sign_in`
        can count call sites against `create_token` call sites — a rule
        rather than four places somebody has to remember, which is the
        lesson F80, F88 and F90 all record.

        A failed database write is logged, not raised. A failed
        bookkeeping write must not turn a successful sign-in into an
        error the user sees, and the cost of losing one is a slightly
        stale access review rather than a lost audit record —
        `audit_events` has the authentication attempt either way.
        """
        try:
            async with self.session_scope() as session:
                await session.execute(
                    update(User).where(User.user_id == user_id).values(last_sign_in_at=datetime.now(timezone.utc))
                )
        except SQLAlchemyError:
            logger.exception('Could not record the sign-in for %s', user_id)

    async def ensure_exists(
        self,
        user_id: str,
        *,
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

        :class:`AccountNotFound` for no such account;
        :class:`AlreadyInAnOrganisation` when the account is already in
        one (its address decides which, so it is not changed here);
        :class:`AddressTaken` when the address has an account;
        `NotACompanyAddress` when no organisation owns the address's domain.
        """
        async with self.session_scope() as session:
            await SuperAdminRoster(session).hold()
            user = (await session.execute(select(User).where(User.user_id == user_id))).scalars().first()
            if user is None:
                raise AccountNotFound('No such account.')
            member = (
                await session.execute(
                    select(OrgMembership.id).where(OrgMembership.user_id == user_id, OrgMembership.status == 'active')
                )
            ).first()
            if member is not None:
                raise AlreadyInAnOrganisation()
            taken = (
                await session.execute(select(User.user_id).where(func.lower(User.email) == email.casefold()))
            ).first()
            if taken is not None:
                raise AddressTaken('That address already has an account.')
            admission = Admission(session)
            joins = await admission.owner_of(email)
            if joins is None:
                raise NotACompanyAddress()
            user.email = email
            user.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await admission.admit(org=joins, user_id=user_id, email=email)
            return joins

    async def delete(self, user_id: str) -> None:
        """Permanently delete a user record."""
        async with self.session_scope() as session:
            await session.execute(User.__table__.delete().where(User.user_id == user_id))
            await session.flush()
