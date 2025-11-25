from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator


class IBAnalyticsRequest(BaseModel):
    """Request payload for IB wallet analytics."""

    ib_ids: List[str] = Field(..., min_length=1, description="List of IB IDs to aggregate")
    start: datetime = Field(..., description="Inclusive start time (YYYY-MM-DD HH:MM:SS)")
    end: datetime = Field(..., description="Inclusive end time (YYYY-MM-DD HH:MM:SS)")

    @field_validator("ib_ids", mode="before")
    @classmethod
    def _normalize_ids(cls, value: List[str]) -> List[str]:
        """Clean blank inputs early so downstream SQL stays safe."""
        if not value:
            raise ValueError("ib_ids cannot be empty")
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        if not cleaned:
            raise ValueError("ib_ids cannot be empty")
        return cleaned

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse_datetime(cls, value: str | datetime) -> datetime:
        """Parse datetime from string format 'YYYY-MM-DD HH:MM:SS' or ISO format."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            # Try ISO format first
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
            # Try SQL format 'YYYY-MM-DD HH:MM:SS'
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ValueError(f"Invalid datetime format: {value}. Expected 'YYYY-MM-DD HH:MM:SS' or ISO format")
        raise ValueError(f"Invalid datetime type: {type(value)}")

    @model_validator(mode="after")
    def _validate_range(self) -> "IBAnalyticsRequest":
        """Validate that end >= start."""
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class IBAnalyticsMetrics(BaseModel):
    deposit_usd: float = 0.0
    total_withdrawal_usd: float = 0.0
    ib_withdrawal_usd: float = 0.0
    ib_wallet_balance: float = 0.0
    net_deposit_usd: float = 0.0


class IBAnalyticsRow(IBAnalyticsMetrics):
    ibid: str


class IBAnalyticsResponse(BaseModel):
    rows: List[IBAnalyticsRow]
    totals: IBAnalyticsMetrics
    last_query_time: datetime | None = None


class LastQueryResponse(BaseModel):
    last_query_time: datetime | None = None


