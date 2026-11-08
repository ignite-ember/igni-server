"""What finishing a command in a VM records on its row."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CommandOutcome(BaseModel):
    """How a command ended, as the node agent reported it."""

    exit_code: int | None
    output: str | None
    error: str | None
    finished_at: datetime
    #: When the node agent says it started; None keeps the time it acknowledged.
    started_at: datetime | None = None
