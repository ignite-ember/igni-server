"""Who may open an account, and why the line is where it is.

``domain`` visibility means "share this with my colleagues", and it
works out who those are by comparing the part of an address after the
``@``. That is a useful question exactly while a domain names an
employer. The moment two strangers can both sign up at ``gmail.com``,
"my domain" is a group containing both of them, and a document shared
with a company is shared with the internet.

The line is drawn once, at ``UserDB.create``, because that is the
single place every way of making an account passes through — the
sign-up form, and the OAuth providers, which arrive with an address
chosen at Google rather than typed into anything of ours. The test
at the end of :class:`TestEveryDoor` is the one that matters: it
asserts that property of the code rather than any particular
endpoint, because an endpoint added next year will not come and read
this file.
"""

import uuid

import pytest

from app.config import settings
from app.db.sql.schemas.account_errors import PersonalAddress
from app.db.sql.user import UserDB
from app.security.account_domains import is_company_address, is_public_mail_domain
from tests.conftest import create_user


class TestWhichDomainsAreAProvider:
    @pytest.mark.parametrize(
        'domain',
        ['gmail.com', 'GMAIL.COM', 'googlemail.com', 'outlook.com', 'yahoo.co.uk', 'proton.me'],
    )
    def test_the_big_providers_are_not_companies(self, domain):
        assert is_public_mail_domain(domain) is True

    @pytest.mark.parametrize('domain', ['acme.test', 'anthropic.com', 'example.com'])
    def test_a_company_is(self, domain):
        assert is_public_mail_domain(domain) is False

    def test_a_provider_subdomain_is_still_the_provider(self):
        assert is_public_mail_domain('mail.yahoo.com') is True

    def test_a_company_whose_name_ends_in_one_is_not(self):
        # Suffix matching on a label boundary, not a bare ``endswith``.
        # Refusing "notgmail.com" would turn away a real company for
        # spelling reasons.
        assert is_public_mail_domain('notgmail.com') is False
        assert is_public_mail_domain('mygmail.com') is False

    def test_nothing_at_all_is_not_a_provider(self):
        # An address with no domain is somebody else's problem — it
        # fails validation as an address. Answering True here would
        # report the wrong reason for it.
        assert is_public_mail_domain('') is False

    def test_an_address_with_no_domain_names_no_company_either(self):
        assert is_company_address('nonsense') is False


class TestEveryDoor:
    async def test_a_personal_address_is_turned_away(self, db):
        with pytest.raises(PersonalAddress):
            await UserDB().create(email='someone@gmail.com', hashed_password='x')

    async def test_it_says_why(self, db):
        # "Invalid email" on a perfectly valid gmail address reads as a
        # bug in the form, and the person retypes it twice before
        # giving up.
        with pytest.raises(PersonalAddress) as refused:
            await UserDB().create(email='someone@gmail.com', hashed_password='x')

        assert 'work address' in str(refused.value)

    async def test_a_work_address_is_let_in(self, db):
        user = await UserDB().create(email='ada@acme.test', hashed_password='x')

        assert str(user.email) == 'ada@acme.test'

    async def test_an_address_from_a_provider_refuses_whatever_signed_it_up(self, db):
        # ``auth_provider='google'`` is the OAuth path, where the
        # address was chosen at Google and never touched our form. The
        # check has to be somewhere both paths go through, and this is
        # the test that says so.
        with pytest.raises(PersonalAddress):
            await UserDB().create(email='someone@gmail.com', hashed_password='x', auth_provider='google')

    async def test_it_is_refused_before_the_duplicate_check(self, db):
        # Order matters for what the caller is told. An existing gmail
        # account — one made before this rule — must not turn "we do
        # not take personal addresses" into "that address is taken",
        # which reads as somebody else having their account.
        await create_user(db, email='early@gmail.com')

        with pytest.raises(PersonalAddress):
            await UserDB().create(email='early@gmail.com', hashed_password='x')


class TestAnOperatorCanDisagree:
    async def test_turning_it_off_lets_them_in(self, db, monkeypatch):
        # Legitimate for a single-person install, and it means
        # accepting that ``domain`` visibility groups nobody usefully.
        monkeypatch.setattr(settings, 'block_public_mail_domains', False)

        user = await UserDB().create(email='solo@gmail.com', hashed_password='x')

        assert str(user.email) == 'solo@gmail.com'

    async def test_it_is_on_unless_somebody_turns_it_off(self):
        assert settings.block_public_mail_domains is True


class TestNoCodeIsSentToAnAddressWeWillRefuse:
    """The refusal has to come before the email, not after it.

    ``UserDB.create`` is the backstop and covers the OAuth paths, but
    reaching only that one means sending somebody a code, waiting for
    them to find it, and telling them at the end that the address was
    never going to work.
    """

    async def test_a_personal_address_is_refused_at_the_first_step(self, db):
        from app.db.sql.schemas.account_errors import PersonalAddress
        from app.services.auth.auth_service import AuthService

        with pytest.raises(PersonalAddress):
            await AuthService().send_sign_up_code('someone@gmail.com')

    async def test_it_still_says_nothing_about_who_has_an_account(self, db):
        # The oracle F112 closed must stay closed. Whether an address
        # is at gmail.com is visible to anyone who can read it;
        # whether it has an account here is not. A work address with
        # an account and one without must still answer identically.
        from app.services.auth.auth_service import AuthService

        # Fresh addresses every run. The throttle behind this keeps
        # its cooldowns in Redis, which outlives the test database —
        # so a fixed address carries a cooldown over from the previous
        # run and the assertion measures the throttle instead of the
        # oracle. It did, and this test failed only in a second run.
        stamp = uuid.uuid4().hex[:8]
        await create_user(db, email=f'taken-{stamp}@acme.test')

        taken = await AuthService().send_sign_up_code(f'taken-{stamp}@acme.test')
        free = await AuthService().send_sign_up_code(f'free-{stamp}@acme.test')

        assert taken.suppress_delivery is True
        assert free.suppress_delivery is False
