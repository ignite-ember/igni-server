"""Where a module stands, given a licence and the time: the lapse sequence.

    valid ──(expires)──▶ grace ──(+90 days)──▶ read_only   (commercial; never "off")
    valid ──(expires)──▶ read_only ──(+30 days)──▶ unlicensed   (evaluation)

| State | What the module does |
|---|---|
| `valid` | Everything. Warns in the last 30 days (7 for an evaluation) |
| `grace` | Everything, with a warning on every answer, for 90 days after expiry |
| `read_only` | Reads, searches and exports; refuses changes, new indexing and new agent runs |
| `unlicensed` | No good licence, or the module not in it, or an evaluation 30 days past its end: only `ALWAYS` |

An evaluation that ends goes read-only for 30 days, so its data can be
exported and the install decommissioned, and only then stops (a kill
switch inside an air-gapped system is a finding a CISO will not
accept). `read_only` is the end of the line for
a module that was ever paid for. Data is never deleted and exports never stop, so a customer can
always leave. Renewing is installing a new file; the module picks it up
without a restart.

Autopilot runs on the Factory, so Autopilot is never better off than
the Factory is: no Factory in the licence means no Autopilot.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
import math

from pydantic import BaseModel, ConfigDict

from .format import LicenceKind, LicencePayload, Module

#: How long a lapsed commercial licence keeps everything working (product catalogue, "Expiry").
GRACE = timedelta(days=90)
#: How long an ended evaluation stays read-only before it stops.
EVALUATION_READ_ONLY = timedelta(days=30)
#: How long before expiry the warnings start.
WARN_BEFORE = {LicenceKind.COMMERCIAL: timedelta(days=30), LicenceKind.EVALUATION: timedelta(days=7)}


class LicenceState(StrEnum):
    VALID = 'valid'
    GRACE = 'grace'
    READ_ONLY = 'read_only'
    UNLICENSED = 'unlicensed'

    @property
    def rank(self) -> int:
        """Worse states rank higher, so the weaker of two is `max` by rank."""
        return list(LicenceState).index(self)


class Operation(StrEnum):
    """What a request or a job does, for the purpose of the licence."""

    #: Health, GDPR erasure and subject access, signing in and out, disconnecting igni from
    #: the customer's own systems. Never refused: refusing them helps nobody and hurts the customer.
    ALWAYS = 'always'
    #: Reading, searching, downloading and exporting what is already there.
    READ = 'read'
    #: Changing anything, starting indexing, starting an agent run, starting a VM.
    WRITE = 'write'


_ALLOWED: dict[LicenceState, frozenset[Operation]] = {
    LicenceState.VALID: frozenset(Operation),
    LicenceState.GRACE: frozenset(Operation),
    LicenceState.READ_ONLY: frozenset({Operation.ALWAYS, Operation.READ}),
    LicenceState.UNLICENSED: frozenset({Operation.ALWAYS}),
}


class Reason(StrEnum):
    """Why a module is where it is."""

    NO_LICENCE = 'no_licence'
    MALFORMED = 'malformed'
    UNKNOWN_KEY = 'unknown_key'
    INVALID_SIGNATURE = 'invalid_signature'
    NOT_LICENSED = 'not_licensed'
    REQUIRES_FACTORY = 'requires_factory'
    EVALUATION_EXPIRED = 'evaluation_expired'
    EXPIRING = 'expiring'
    LAPSED = 'lapsed'
    READ_ONLY = 'read_only'


class LicenceReading(BaseModel):
    """What reading the installed file gave: a verified payload, or why there is none."""

    model_config = ConfigDict(frozen=True)

    payload: LicencePayload | None = None
    problem: Reason | None = None


class ModuleStatus(BaseModel):
    """One module's standing under the installed licence, at one moment."""

    model_config = ConfigDict(frozen=True)

    module: Module
    state: LicenceState
    reason: Reason | None = None
    licence_id: str | None = None
    customer: str | None = None
    kind: LicenceKind | None = None
    users: int | None = None
    expires: datetime | None = None
    read_only_from: datetime | None = None
    #: Whole days until the next change of state, rounded up; None when nothing is coming.
    days_left: int | None = None
    #: The sentence to show people. None when there is nothing to say.
    message: str | None = None

    def allows(self, operation: Operation) -> bool:
        return operation in _ALLOWED[self.state]


class LicenceStatus:
    """Works out a module's `ModuleStatus`. Pure: the time is an argument."""

    @classmethod
    def of(cls, reading: LicenceReading, module: Module, *, now: datetime) -> ModuleStatus:
        status = cls._own(reading, module, now=now)
        if module is not Module.AUTOPILOT or status.state is LicenceState.UNLICENSED:
            return status
        payload = reading.payload
        assert payload is not None  # a licensed status always came from a payload
        if payload.entitlement(Module.FACTORY) is None:
            return cls._unlicensed(module, Reason.REQUIRES_FACTORY, payload)
        factory = cls._own(reading, Module.FACTORY, now=now)
        if factory.state.rank <= status.state.rank:
            return status
        return status.model_copy(
            update={
                'state': factory.state,
                'reason': Reason.REQUIRES_FACTORY if factory.state is LicenceState.UNLICENSED else factory.reason,
                'expires': factory.expires,
                'read_only_from': factory.read_only_from,
                'days_left': factory.days_left,
                'message': f'Autopilot runs on the Factory. {factory.message}',
            }
        )

    @classmethod
    def _own(cls, reading: LicenceReading, module: Module, *, now: datetime) -> ModuleStatus:
        payload = reading.payload
        if payload is None:
            return cls._unlicensed(module, reading.problem or Reason.NO_LICENCE, None)
        entitlement = payload.entitlement(module)
        if entitlement is None:
            return cls._unlicensed(module, Reason.NOT_LICENSED, payload)
        expires = entitlement.expires
        read_only_from = expires + GRACE
        base = ModuleStatus(
            module=module,
            state=LicenceState.VALID,
            licence_id=payload.licence_id,
            customer=payload.customer,
            kind=payload.kind,
            users=entitlement.users,
            expires=expires,
            read_only_from=read_only_from if payload.kind is LicenceKind.COMMERCIAL else expires,
        )
        name, until = module.display_name, Dates.long(expires)
        start = f'{name[0].upper()}{name[1:]}'
        if now < expires:
            days = Dates.days_until(expires, now=now)
            if expires - now > WARN_BEFORE[payload.kind]:
                return base.model_copy(update={'days_left': days})
            return base.model_copy(
                update={
                    'reason': Reason.EXPIRING,
                    'days_left': days,
                    'message': f'The licence for {name} expires on {until} ({Dates.in_days(days)}). '
                    'Install the renewed licence file before then.',
                }
            )
        if payload.kind is LicenceKind.EVALUATION:
            stops = expires + EVALUATION_READ_ONLY
            if now < stops:
                days = Dates.days_until(stops, now=now)
                return base.model_copy(
                    update={
                        'state': LicenceState.READ_ONLY,
                        'reason': Reason.EVALUATION_EXPIRED,
                        'days_left': days,
                        'message': f'The evaluation licence for {name} ended on {until}. {start} is read-only '
                        f'until {Dates.long(stops)} ({Dates.in_days(days)}): you can read and export everything, '
                        'then it stops. Install a licence file to continue.',
                    }
                )
            return base.model_copy(
                update={
                    'state': LicenceState.UNLICENSED,
                    'reason': Reason.EVALUATION_EXPIRED,
                    'message': f'The evaluation licence for {name} ended on {until}, and its 30 read-only days '
                    f'ended on {Dates.long(stops)}.',
                }
            )
        if now < read_only_from:
            days = Dates.days_until(read_only_from, now=now)
            return base.model_copy(
                update={
                    'state': LicenceState.GRACE,
                    'reason': Reason.LAPSED,
                    'days_left': days,
                    'message': f'The licence for {name} expired on {until}. {start} keeps working until '
                    f'{Dates.long(read_only_from)} ({Dates.in_days(days)}), then becomes read-only. '
                    'Install a renewed licence file to continue.',
                }
            )
        return base.model_copy(
            update={
                'state': LicenceState.READ_ONLY,
                'reason': Reason.READ_ONLY,
                'message': f'The licence for {name} expired on {until}. {start} is read-only: you can read '
                'and export everything, but not change anything or start new work. '
                'Install a renewed licence file to continue.',
            }
        )

    @staticmethod
    def _unlicensed(module: Module, reason: Reason, payload: LicencePayload | None) -> ModuleStatus:
        sentences = {
            Reason.NO_LICENCE: 'No licence is installed.',
            Reason.MALFORMED: 'The installed licence file is not a valid licence.',
            Reason.UNKNOWN_KEY: 'The installed licence was not issued by Ignite Ember for this release.',
            Reason.INVALID_SIGNATURE: 'The installed licence has been altered and is not valid.',
            Reason.NOT_LICENSED: 'The installed licence does not include it.',
            Reason.REQUIRES_FACTORY: 'Autopilot needs the Factory, and the installed licence does not include it.',
        }
        return ModuleStatus(
            module=module,
            state=LicenceState.UNLICENSED,
            reason=reason,
            licence_id=payload.licence_id if payload else None,
            customer=payload.customer if payload else None,
            kind=payload.kind if payload else None,
            message=f'{module.display_name[0].upper()}{module.display_name[1:]} is not licensed on this deployment. '
            f'{sentences.get(reason, "")}'.strip(),
        )


class Dates:
    """How dates and day counts read in a message."""

    @staticmethod
    def long(moment: datetime) -> str:
        return f'{moment.day} {moment:%B %Y}'

    @staticmethod
    def days_until(moment: datetime, *, now: datetime) -> int:
        return max(0, math.ceil((moment - now) / timedelta(days=1)))

    @staticmethod
    def in_days(days: int) -> str:
        return 'in 1 day' if days == 1 else f'in {days} days'
