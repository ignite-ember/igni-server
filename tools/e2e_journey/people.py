"""Who the journey signs in as, and with what.

Throwaway credentials: every account lives in a database and an IdP that
exist for one run.
"""

from pydantic import BaseModel

#: The password the email sign-ups set, and the one the password sign-in uses.
LOCAL_PASSWORD = 'E2e-Password-123!'


class SsoPerson(BaseModel):
    """Somebody Dex knows. The address is ``local_part@<the run's domain>``."""

    local_part: str
    full_name: str
    password: str


#: Max is invited by the organisation's admin and never sets an igni
#: password: SSO is how they get in, to the portal and to the CLI.
MAX = SsoPerson(local_part='max', full_name='Max Planck', password='E2e-Sso-Password-456!')

SSO_PEOPLE = [MAX]
