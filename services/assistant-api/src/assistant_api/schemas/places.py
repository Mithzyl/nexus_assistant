"""Shared body-only place lookup contracts without business record identifiers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CoordinateSystem = Literal["gcj02", "wgs84", "unknown"]
PlaceSearchType = Literal["place", "address"]


class PlaceLookupRequest(BaseModel):
    """Search a name or address without creating a draft or saving a location."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=255)
    city: str | None = Field(default=None, max_length=64)
    search_type: PlaceSearchType = "place"
    limit: int = Field(default=5, ge=1, le=10)


class PlacePointLocation(BaseModel):
    """Describe an actual provider location without a draft selection token."""

    provider: str
    provider_place_id: str | None = None
    name: str
    address: str | None = None
    city: str | None = None
    latitude: float
    longitude: float
    coordinate_system: CoordinateSystem
    source: str


class PlaceLookupCandidate(PlacePointLocation):
    """Retain the provider identifier for a search candidate."""

    provider_place_id: str


class PlaceLookupResponse(BaseModel):
    """Distinguish actual candidates, no matches and unavailable place providers."""

    query: str
    city: str | None = None
    search_type: PlaceSearchType
    ambiguous: bool
    candidates: list[PlaceLookupCandidate] = Field(default_factory=list)
    reason_code: str | None = None


class PlacePointRequest(BaseModel):
    """Reverse lookup an explicitly chosen GCJ-02 point; coordinates stay in JSON."""

    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    coordinate_system: Literal["gcj02"] = "gcj02"


class PlacePointResponse(BaseModel):
    """Return a real nearby address or an explicit reason it could not be obtained."""

    status: Literal["available", "unavailable"]
    reason_code: str | None = None
    address_notice: str | None = None
    location: PlacePointLocation | None = None
