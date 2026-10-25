"""Product defaults for the commute module.

These are configurable defaults, not Apple platform constraints. A change here
must be reflected in the OpenAPI description and the App integration checklist.
"""

CHECK_INTERVAL_MINUTES = 10
MAX_AUTO_REMINDERS_PER_DAY = 2
MIN_REMINDER_GAP_MINUTES = 15
DEPARTURE_EARLIER_THRESHOLD_MINUTES = 10
ETA_FRESHNESS_SECONDS = 600
WEATHER_FRESHNESS_SECONDS = 3600
WEATHER_ATTRIBUTION_TEXT = "天气数据由 Apple Weather 提供"
WEATHER_ATTRIBUTION_URL = "https://developer.apple.com/weatherkit/data-source-attribution/"
WEATHER_LEGAL_URL = "https://developer.apple.com/weatherkit/"
ROUTE_ATTRIBUTION_TEXT = "路线与耗时数据由 Apple Maps Server API 提供"
ROUTE_ATTRIBUTION_URL = "https://developer.apple.com/documentation/applemapsserverapi"
ADVISORY_SCHEMA_VERSION = 1
DEPARTURE_NOW_WINDOW_MINUTES = 5
