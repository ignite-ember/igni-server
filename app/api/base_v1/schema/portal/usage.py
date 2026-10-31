"""Schemas for portal usage analytics."""

from pydantic import BaseModel


class UsageDataPoint(BaseModel):
    date: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int


class UsageResponse(BaseModel):
    data: list[UsageDataPoint]
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
