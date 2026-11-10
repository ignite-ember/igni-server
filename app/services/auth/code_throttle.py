"""Limits on how often an anonymous caller can make us send an email.

F112. Four endpoints send mail to an address chosen by an unauthenticated
caller: `/v1/send-sign-up-code`, `/v1/send-sign-in-code`,
`/v1/send-reset-password-code` and the welcome mail from `/v1/sign-up`.
Nothing limited them. Twenty-five consecutive requests for one address
were all accepted.

Two consequences, and the second is the one that costs a customer money:

* **the mailbox owner is griefed.** Every request replaces the pending
  code, so a person reading their code and typing it in gets it
  invalidated by an attacker's request. Repeated indefinitely, that is a
  denial of sign-in against one account;
* **the deployment becomes a mail cannon.** It sends, from the
  customer's own relay or SES identity, to any address anybody names, as
  often as they ask. The bill is theirs, the reputation damage is
  theirs, and an SES account gets suspended for exactly this.

## Not "rate limiting"

Worth separating, because rate limiting was deliberately removed from
this product: nothing here limits what a *paying, authenticated* user can
do with inference. This limits how often an **anonymous** caller can make
the server take an outbound action with somebody else's mailbox as the
target. Different thing, and the absence of the first is not an argument
for the absence of the second.

## The shape of the limits

Per address, always on:

* a **cooldown**, so a double-clicked button does not send twice and an
  attacker cannot rotate somebody's code continuously;
* an **hourly cap**, so a slow drip does not add up to a mailbox full of
  codes.

Globally, and configurable because it can hurt:

* an **hourly cap across all addresses**, which is the only thing that
  bounds spraying across many addresses. Set generously by default: an
  organisation onboarding three hundred people in an afternoon must not
  find sign-in switched off, and *that* failure is worse than the abuse
  it prevents. `0` disables it.

## Refusals are silent

A throttled request returns the same message a sent one does. A `429`
here would say "somebody asked for a code for this address recently",
which is an activity oracle on an endpoint whose whole design is to say
nothing about whether an address exists.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.config import settings
from app.monitoring import EMAIL_CODES_THROTTLED
from app.utils.email_mask import mask_email

logger = logging.getLogger(__name__)

_PREFIX = 'code_throttle:'

#: Seconds between two sends for the same purpose and address.
COOLDOWN_SECONDS = 60

#: Sends per address per hour, for one purpose.
#:
#: Ten rather than five. A *fresh* code is only issued when none is
#: pending, so this bounds a mailbox at ten emails an hour — well under
#: anything anybody would call abuse, and comfortably above the worst
#: legitimate pattern: sign in, sign out, resend, mistype, resend.
#: Five was tight enough that a person having a bad afternoon would hit
#: it, silently.
PER_ADDRESS_HOURLY = 10

_HOUR = 3600


class CodeThrottle:
    """Redis-backed counters. Fails **open**, deliberately.

    If Redis is unreachable the codes still go out. The alternative is
    that a Redis blip locks every user out of sign-in, which is a worse
    outage than the abuse this prevents — and the same trade the audit
    recorder makes in F90, for the same reason. The counter below is how
    an operator sees that it happened.
    """

    async def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        client: aioredis.Redis = aioredis.from_url(  # type: ignore[assignment]
            settings.redis_url, decode_responses=True
        )
        return client

    async def allow(self, *, purpose: str, email: str, code_pending: bool) -> bool:
        """Whether to send a code for *purpose* to *email* now.

        ``code_pending`` says whether an unused code is already stored,
        and it decides whether the cooldown applies. That distinction is
        the whole of the design, and the first version got it wrong.

        **A pending code means a resend.** The caller returns the code
        that is already stored rather than a new one, so the cooldown is
        only deciding whether to send a *second email* about a code the
        user already has — harmless to skip.

        **No pending code means the previous one is gone**: consumed by a
        successful sign-in, or destroyed by five wrong guesses, which is
        exactly the state `_check_code` creates on purpose so "the
        attacker has to make the real user request another one". Applying
        a cooldown there would leave that user unable to request another
        for a minute, and silently — the failure the cooldown was
        supposed to prevent, aimed at the same person.

        The hourly caps apply either way, because a flood of *resends* is
        still a flood of mail.
        """
        address = email.strip().lower()
        try:
            client = await self._redis()
        except Exception as exc:  # noqa: BLE001 — availability beats the limit
            logger.warning('Code throttle unavailable, allowing the send: %s', exc)
            return True

        try:
            cooldown_key = f'{_PREFIX}cool:{purpose}:{address}'
            hourly_key = f'{_PREFIX}hour:{purpose}:{address}'
            global_key = f'{_PREFIX}global'

            if code_pending and await client.get(cooldown_key):
                # A second email about a code they already have.
                return self._refuse(purpose, address, 'cooldown')

            hourly = int(await client.get(hourly_key) or 0)
            if hourly >= PER_ADDRESS_HOURLY:
                return self._refuse(purpose, address, 'per_address_hourly')

            limit = settings.email_codes_hourly_limit
            if limit > 0:
                total = int(await client.get(global_key) or 0)
                if total >= limit:
                    return self._refuse(purpose, address, 'global_hourly')

            # Claim the send. `incr` then `expire` on first write, so the
            # window slides from the first send of the hour rather than
            # being extended by every one after it — otherwise a steady
            # trickle keeps the window open forever and the cap becomes
            # a lifetime limit.
            await client.set(cooldown_key, '1', ex=COOLDOWN_SECONDS)
            if await client.incr(hourly_key) == 1:
                await client.expire(hourly_key, _HOUR)
            if limit > 0 and await client.incr(global_key) == 1:
                await client.expire(global_key, _HOUR)

            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning('Code throttle failed, allowing the send: %s', exc)
            return True
        finally:
            await client.aclose()

    @staticmethod
    def _refuse(purpose: str, address: str, reason: str) -> bool:
        EMAIL_CODES_THROTTLED.labels(purpose=purpose, reason=reason).inc()
        logger.info('Not sending a %s code to %s: %s', purpose, mask_email(address), reason)
        return False
