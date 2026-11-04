"""Token-free place queries shared by commute forms and schedule draft workflows."""

from assistant_api.commute.providers.base import ProviderError
from assistant_api.commute.runtime import CommuteRuntime

GCJ02_CHINA_BOUNDS = (73.0, 3.0, 136.0, 54.0)


async def search_places(
    runtime: CommuteRuntime,
    *,
    query: str,
    city: str | None,
    search_type: str,
    limit: int,
) -> dict:
    """Query normalized provider candidates without depending on a business draft."""

    if runtime.places is None or not getattr(runtime.places, "available", False):
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": "amap_not_configured",
        }
    try:
        rows = await runtime.places.search(
            query=query,
            city=city,
            limit=limit,
            search_type=search_type,
        )
    except ProviderError as exc:
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": exc.code,
        }
    except Exception:  # noqa: BLE001 - provider boundary
        return {
            "query": query,
            "city": city,
            "search_type": search_type,
            "ambiguous": False,
            "candidates": [],
            "reason_code": "place_provider_error",
        }
    candidates = [
        {
            "provider": row.provider,
            "provider_place_id": row.provider_place_id,
            "name": row.name,
            "address": row.address,
            "city": row.city,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "coordinate_system": row.coordinate_system,
            "source": row.source,
        }
        for row in rows
    ]
    return {
        "query": query,
        "city": city,
        "search_type": search_type,
        "ambiguous": len(candidates) > 1,
        "candidates": candidates,
        "reason_code": None if candidates else "place_not_found",
    }


async def map_pick_place(
    runtime: CommuteRuntime,
    *,
    latitude: float,
    longitude: float,
    coordinate_system: str,
) -> dict:
    """Reverse lookup one explicit point without saving records or claiming an exact POI."""

    if coordinate_system != "gcj02":
        return {
            "status": "unavailable",
            "reason_code": "map_pick_coordinate_system_unsupported",
            "location": None,
        }
    min_longitude, min_latitude, max_longitude, max_latitude = GCJ02_CHINA_BOUNDS
    if not (
        min_latitude <= latitude <= max_latitude
        and min_longitude <= longitude <= max_longitude
    ):
        return {
            "status": "unavailable",
            "reason_code": "map_pick_coordinate_out_of_bounds",
            "location": None,
        }
    if runtime.places is None or not getattr(runtime.places, "available", False):
        return {
            "status": "unavailable",
            "reason_code": "amap_not_configured",
            "location": None,
        }
    try:
        location = await runtime.places.reverse_geocode(
            latitude=latitude,
            longitude=longitude,
        )
    except ProviderError as exc:
        return {
            "status": "unavailable",
            "reason_code": exc.code,
            "location": None,
        }
    except Exception:  # noqa: BLE001 - provider boundary
        return {
            "status": "unavailable",
            "reason_code": "place_provider_error",
            "location": None,
        }
    candidate = {
        "provider": location.provider,
        "provider_place_id": None,
        "name": location.name,
        "address": location.address,
        "city": location.city,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "coordinate_system": location.coordinate_system,
        "source": location.source,
    }
    return {
        "status": "available",
        "reason_code": None,
        "address_notice": "地址为地图坐标附近的反查结果，请核对门牌或建筑。",
        "location": candidate,
    }
