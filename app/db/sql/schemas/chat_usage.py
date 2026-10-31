"""What the chat-usage aggregates return.

Both were plain dicts with known key sets, built in `ChatUsageDB` and
splatted into response models one layer up (`UsageDataPoint(**row)`).
A column renamed on either side failed at runtime with a `TypeError`
rather than anywhere a type checker would see it.

The field names match `app/api/base_v1/schema/portal/usage.py`
deliberately — these are the same numbers, and the service's job is to
pick which of them the response carries, not to rename them on the way
through.
"""

from __future__ import annotations

from pydantic import BaseModel


class DailyUsage(BaseModel):
    """One day's totals.

    `date` is a string because that is what the response carries and
    what the query already formatted it to; keeping a `date` object
    here would mean converting it twice.
    """

    date: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class UsageTotals(BaseModel):
    """The whole window, summed.

    Every field is a count with a `coalesce(..., 0)` behind it, so an
    org with no usage reports zeros rather than nulls — which is why
    none of these is optional.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
