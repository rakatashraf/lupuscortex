from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


class AnalysisRequest(BaseModel):
    lat: float
    lon: float
    name: str = "Selected location"
    requested_at: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    scenario: dict[str, Any] | None = None

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return value
