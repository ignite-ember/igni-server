"""An invitation is claimed by proving you own the address, and nothing else.

``invite_url_for`` is explicit that there is no token: *"the invite is
claimed by signing up with the invited address, so this URL is derivable
from the address alone"*. That is a sound design — but it puts the whole
weight of an invitation on the sign-up code, and the sign-up code had no
attempt limit.

The chain, before that was fixed:

1. an admin invites ``victim@corp.example``, possibly as ``admin``;
2. signing up claims the pending membership for an address
   (``Admission.admit``) for whoever signs up with it;
3. anybody could request a sign-up code for that address and brute-force
   it — six digits, ten minutes, no counter, 300 wrong guesses verified
   to have no consequence.

So it was not only "sign in as an existing user". It was **join somebody
else's organisation, at the role their admin chose, without ever
controlling the email**. This file pins the consequence rather than the
mechanism, because the mechanism is what will get refactored.
"""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.services.auth.otp_service import _MAX_ATTEMPTS, OTPService
from tests.conftest import add_member, auth_header, create_org, create_user, make_token

VICTIM = 'victim@corp.example'


@pytest.fixture
async def org_with_admin(db):
    await create_user(db, user_id='boss', email='boss@corp.example')
    org = await create_org(db, owner_user_id='boss', slug='corp', display_name='Corp', domains=('corp.example',))
    await add_member(db, org_id=str(org.id), user_id='boss', role='admin')
    return {'org': str(org.id), 'headers': auth_header(make_token('boss', org_id=str(org.id)))}


class TestGuessingASignupCodeCannotClaimAnInvite:
    async def test_the_allowance_runs_out_before_the_invite_can_be_taken(self, client, org_with_admin):
        """The whole chain, end to end: an admin invitation exists, an
        attacker requests the code that would claim it, and guessing
        destroys the code instead of finding it."""
        invited = await client.post(
            '/portal/team/invite',
            json={'email': VICTIM, 'role': 'admin'},
            headers=org_with_admin['headers'],
        )
        assert invited.status_code in (200, 201), invited.text

        # The attacker asks for the code. It goes to the victim's inbox;
        # the attacker has to guess it.
        real = await OTPService().generate_signup_code(email=VICTIM)

        for i in range(_MAX_ATTEMPTS):
            with pytest.raises(HTTPException):
                await OTPService().validate_signup_code(VICTIM, f'{i:06d}')

        # And now even the right code is gone, so the invitation cannot be
        # claimed with it.
        with pytest.raises(HTTPException):
            await OTPService().validate_signup_code(VICTIM, real.code)

    async def test_the_real_owner_of_the_address_can_still_claim_it(self, client, org_with_admin):
        """The control. Somebody who reads their own email — so gets the
        code right — must still be able to join, or the fix has broken
        onboarding."""
        await client.post(
            '/portal/team/invite',
            json={'email': VICTIM, 'role': 'member'},
            headers=org_with_admin['headers'],
        )

        real = await OTPService().generate_signup_code(email=VICTIM)

        # No exception: the code is accepted.
        await OTPService().validate_signup_code(VICTIM, real.code)

    async def test_a_typo_or_two_does_not_cost_the_invite(self, client, org_with_admin):
        """Between "guessable" and "unusable" there is a real person
        mistyping a code from their phone."""
        await client.post(
            '/portal/team/invite',
            json={'email': VICTIM, 'role': 'member'},
            headers=org_with_admin['headers'],
        )
        real = await OTPService().generate_signup_code(email=VICTIM)

        for i in range(_MAX_ATTEMPTS - 1):
            with pytest.raises(HTTPException):
                await OTPService().validate_signup_code(VICTIM, f'{i:06d}')

        await OTPService().validate_signup_code(VICTIM, real.code)


class TestTheInviteItselfCarriesNoSecret:
    def test_the_url_is_derivable_from_the_address(self):
        """Asserted so the design stays deliberate. If a token is added
        later, this fails and whoever adds it has to decide what its
        entropy, expiry and single-use story is — rather than a
        half-token quietly becoming the thing security rests on.
        """
        from app.services.email.delivery import invite_url_for

        url = invite_url_for(VICTIM)

        assert VICTIM.replace('@', '%40') in url or VICTIM in url
        # No opaque secret in it.
        assert 'token' not in url.lower()

    def test_the_dead_invitation_table_is_still_unused(self):
        """``org_invitations`` has a ``token`` column and nothing reads
        it. Recorded rather than removed: dropping a table is a migration
        and a decision, but a security-relevant column that nothing
        maintains is exactly what somebody later mistakes for the
        mechanism in use.
        """
        import pathlib

        app_root = pathlib.Path(__file__).resolve().parent.parent / 'app'
        users = [
            path
            for path in app_root.rglob('*.py')
            if 'OrgInvitation' in path.read_text() and path.name != 'org_invitation.py'
        ]

        assert not users, f'org_invitations is in use after all — check {users}'
