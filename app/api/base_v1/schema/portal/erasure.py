"""Schemas for erasing one person's data on request (GDPR Art. 17)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ErasureRequest(BaseModel):
    email: EmailStr = Field(description='The address of the person to erase.')


class ErasureResult(BaseModel):
    email: str = Field(description='Echoed masked, so the response is not itself a disclosure.')
    services_told: list[str] = Field(
        default_factory=list,
        description='The registered services that confirmed the erasure, by name.',
    )


class ErasureDoneAudit(BaseModel):
    """What the audit trail records about an erasure that went ahead.

    Masked: F90 retains these rows, so the trail must not become the
    place the erased address survives.
    """

    subject: str
    services: list[str]


class ErasureRefusedAudit(BaseModel):
    """What the audit trail records about an erasure a legal hold refused, and why."""

    subject: str
    refused: str
