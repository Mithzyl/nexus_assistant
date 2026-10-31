"""App request validation matching Nexus typed_memory at c4944fe; Nexus owns storage."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlaceMemory(BaseModel):
    """Describe a confirmed place with explicit coordinate reference system."""

    model_config = ConfigDict(extra="forbid")
    memory_type: Literal["place"] = "place"
    label: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=1000)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: Literal["WGS84", "GCJ02", "BD09"]

    @model_validator(mode="after")
    def coordinate_pair(self):
        """Require either both coordinates or neither, without guessing a missing value."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must be provided together")
        return self


class TimedMemory(BaseModel):
    """Require an actual IANA timezone for recurring time values."""

    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        """Reject unknown timezone names rather than interpreting server local time."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("An IANA timezone is required") from None
        return value


class CommuteMemory(TimedMemory):
    """Keep a confirmed recurring commute separate from a single future trip."""

    memory_type: Literal["commute_preference"] = "commute_preference"
    origin: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)
    travel_mode: Literal["walk", "bicycle", "transit", "drive"]
    preferred_departure: time | None = None
    weekdays: list[int] = Field(min_length=1, max_length=7)

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, value):
        """Use unique ISO weekdays, Monday=1 through Sunday=7."""
        if any(day < 1 or day > 7 for day in value) or len(set(value)) != len(value):
            raise ValueError("Weekdays must be unique values from 1 through 7")
        return value


class TimePreferenceMemory(TimedMemory):
    """Describe a recurring time preference, without creating calendar events."""

    memory_type: Literal["time_preference"] = "time_preference"
    activity: str = Field(min_length=1, max_length=500)
    preferred_time: time


class ExperienceMemory(BaseModel):
    """Record an explicitly described past experience and optional lesson."""

    model_config = ConfigDict(extra="forbid")
    memory_type: Literal["experience"] = "experience"
    occurred_on: date
    description: str = Field(min_length=1, max_length=2000)
    lesson: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("occurred_on")
    @classmethod
    def occurred_not_future(cls, value):
        """Reject future dates; scheduled plans are not past experiences."""
        if value > datetime.now(UTC).date():
            raise ValueError("Experience date cannot be in the future (UTC)")
        return value


TypedMemory = Annotated[
    PlaceMemory | CommuteMemory | TimePreferenceMemory | ExperienceMemory,
    Field(discriminator="memory_type"),
]
