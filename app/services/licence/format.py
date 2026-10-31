"""What a licence file says, and the rules any licence must keep.

The authoritative definition is igni server's `docs/licence-file.md`.
This file is copied byte for byte into every paid module
(`igni__code-index/app/licence/`, `igni__artifacts/app/licence/`); the
Rust gateway in `igni__cloudvm/crates/licence` reads the same format.
A change here is a change to that document and to every copy, and the
golden file in `tests/fixtures/licence/` pins all of them.

A licence file is a small JSON envelope around a signed payload:

    {"igni_licence": 1, "key_id": "...", "payload": "<base64url>", "signature": "<base64url>"}

The signature is Ed25519 over the payload's decoded bytes exactly as
they are, so no implementation has to agree with another on how to
re-serialise JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Licensed bands above 1,000 go up in steps of this many people.
BAND_STEP_ABOVE_1000 = 1000
#: The bands up to 1,000 people, as in the price list.
BANDS_UP_TO_1000 = (50, 100, 200, 500, 1000)
#: An evaluation licence is for at most this many people (product catalogue).
EVALUATION_MAX_USERS = 25


class Module(StrEnum):
    """A paid module, as a licence names it."""

    CODEINDEX = 'codeindex'
    ARTIFACTS = 'artifacts'
    FLEET = 'fleet'
    FACTORY = 'factory'
    AUTOPILOT = 'autopilot'

    @property
    def display_name(self) -> str:
        return _TITLES[self]

    @property
    def banded(self) -> bool:
        """Every module but the factory is sold in bands of people; the factory is flat."""
        return self is not Module.FACTORY


_TITLES = {
    Module.CODEINDEX: 'CodeIndex',
    Module.ARTIFACTS: 'Artifacts',
    Module.FLEET: 'Fleet',
    Module.FACTORY: 'the Factory',
    Module.AUTOPILOT: 'Autopilot',
}


class LicenceKind(StrEnum):
    """Commercial licences lapse gently; evaluation licences stop.

    Pilot and partner (NFR) licences are commercial for this purpose:
    production use is allowed, so a lapse must not break anything.
    """

    COMMERCIAL = 'commercial'
    EVALUATION = 'evaluation'


class Bands:
    """The bands a module is sold in: ≤50, 100, 200, 500, 1,000, then each further 1,000."""

    @staticmethod
    def is_band(users: int) -> bool:
        if users in BANDS_UP_TO_1000:
            return True
        return users > 1000 and users % BAND_STEP_ABOVE_1000 == 0


class Entitlement(BaseModel):
    """One module, as the licence grants it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    #: The band: the most people who may use the module. None for the factory.
    users: int | None = Field(default=None, ge=1)
    #: The moment this module's term ends (UTC). The lapse sequence starts here.
    expires: datetime

    @field_validator('expires')
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError('expires must carry a time zone (UTC)')
        return value.astimezone(timezone.utc)


class LicencePayload(BaseModel):
    """The signed part of a licence file."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    format: Literal[1]
    licence_id: str = Field(min_length=1, max_length=100)
    kind: LicenceKind
    customer: str = Field(min_length=1, max_length=200)
    issued: datetime
    modules: dict[Module, Entitlement] = Field(min_length=1)

    @field_validator('issued')
    @classmethod
    def _issued_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError('issued must carry a time zone (UTC)')
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def _bands(self) -> LicencePayload:
        for module, entitlement in self.modules.items():
            if not module.banded:
                if entitlement.users is not None:
                    raise ValueError(f'{module.value} is not sold by users, and names none')
                continue
            if entitlement.users is None:
                raise ValueError(f'{module.value} needs a band of users')
            if self.kind is LicenceKind.EVALUATION:
                if entitlement.users > EVALUATION_MAX_USERS:
                    raise ValueError(f'an evaluation licence is for at most {EVALUATION_MAX_USERS} users')
            elif not Bands.is_band(entitlement.users):
                raise ValueError(f'{entitlement.users} is not a band (50, 100, 200, 500, 1000, then thousands)')
        return self

    def entitlement(self, module: Module) -> Entitlement | None:
        return self.modules.get(module)


class LicenceEnvelope(BaseModel):
    """The file as it is installed: the payload and Ignite Ember's signature over it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    igni_licence: Literal[1]
    key_id: str = Field(min_length=1, max_length=100)
    payload: str = Field(min_length=1)
    signature: str = Field(min_length=1)
