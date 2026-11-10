"""The development bypass identity, named in one place.

`portal_auth` creates these rows when `portal_auth_bypass` is on, and
`database_checks` refuses to boot if it finds them in a production
database. The two held **separate copies** of the ids:

    portal_auth.py:    _BYPASS_ORG_ID = 'demo-org-001'
    startup_checks.py: _BYPASS_USER_ID = 'demo-user-001'
                       _BYPASS_ORG_ID  = 'demo-org-001'

So the guard that exists to catch this identity keyed on a second
transcription of what creates it. Change one and the guard keeps
passing while the account it was written for is still there — which
is the one failure mode a refuse-to-start check cannot afford, since
nothing else would report it.

Here so both can import it without either importing the other.
"""

from __future__ import annotations

BYPASS_USER_ID = 'demo-user-001'
BYPASS_ORG_ID = 'demo-org-001'

#: An RFC 2606 `.invalid` domain, deliberately: no mailbox can exist
#: there, so the one-time-code path cannot deliver to this identity
#: even if the rows reach a real deployment. It used to be
#: `alex@igni.sh` — a routable address at a domain the customer does
#: not control, where a sign-in code *is* an admin session.
#:
#: This only protects rows created from now on. `ensure_exists`
#: returns early when the user already exists, so databases carrying
#: the old address keep it; that is what
#: `DatabaseChecks.check_no_bypass_identity_in_database` is for, and why it keys on
#: the user id rather than the email.
BYPASS_EMAIL = 'alex@demo.invalid'
BYPASS_FULL_NAME = 'Alex Chen'
