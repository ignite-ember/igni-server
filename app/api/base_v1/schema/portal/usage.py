"""Schemas for portal usage analytics."""

from pydantic import BaseModel


class UsageDataPoint(BaseModel):
    date: str
    input_tokens: int
    #: Everything the model generated, its thinking included.
    output_tokens: int
    total_tokens: int
    #: The part of `output_tokens` that was thinking; null when no request that day reported it.
    reasoning_tokens: int | None = None
    request_count: int


class UsageResponse(BaseModel):
    data: list[UsageDataPoint]
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    #: The part of `total_output_tokens` that was thinking; null when no request reported it.
    total_reasoning_tokens: int | None = None
    total_requests: int
