"""Which social sign-ins this deployment can actually perform.

``settings.oauth_providers`` is the operator saying which buttons they
*want*. It is not the same question as whether a button can work, and
treating it as the same shipped the failure ``auth_methods.py`` was
written to prevent, one step further along: a fresh install that
enabled a provider with no credentials for it advertised the button
anyway, and sent anyone who clicked to the provider with
``client_id=`` — an error page on somebody else's domain, for a button
this server put there.

So availability is two things, and both have to hold:

* the operator listed the provider in ``oauth_providers``, and
* credentials for it exist.

"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _google_ready() -> bool:
    """Environment only; Google has no runtime registration screen."""
    return bool(settings.oauth_google_client_id and settings.oauth_google_client_secret)


async def is_usable(provider: str) -> bool:
    """Whether a sign-in through ``provider`` could succeed right now.

    Unknown names answer ``False``. A provider nobody has taught this
    function about is one nobody can have configured, and guessing
    ``True`` would put the empty-``client_id`` redirect back.

    Never raises. Reading runtime configuration touches the database,
    and a sign-in page that fails to render because a provider lookup
    threw is a worse outcome than a provider temporarily missing from
    it — the operator still has password and SSO, and the next request
    tries again.
    """
    if provider not in settings.oauth_providers:
        return False
    try:
        match provider:
            case 'google':
                return _google_ready()
            case _:
                return False
    except Exception:
        logger.warning('could not determine whether %s is configured', provider, exc_info=True)
        return False


async def usable_providers() -> list[str]:
    """The providers to offer, in the order the operator listed them.

    Order is preserved rather than sorted: ``oauth_providers`` is a
    sequence an operator wrote, and a sign-in page that reshuffles it
    is ignoring the one thing that list says beyond membership.
    """
    out: list[str] = []
    for provider in settings.oauth_providers:
        if await is_usable(provider):
            out.append(provider)
    return out
