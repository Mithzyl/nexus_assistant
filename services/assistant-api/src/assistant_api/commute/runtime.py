"""Runtime composition for the commute module.

The API process never starts an in-process timer. It only owns provider clients;
the scheduler and worker are separate processes that build the same runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from assistant_api.core.config import Settings, commute_configuration_errors
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.commute.providers.apns import ApnsPushProvider
from assistant_api.commute.providers.base import (
    AdviceTextProvider,
    PlaceProvider,
    ProviderNotConfigured,
    PushProvider,
    RouteEtaProvider,
    WeatherProvider,
)
from assistant_api.commute.providers.http_advice import HttpAdviceTextProvider
from assistant_api.commute.providers.maps_eta import MapsEtaProvider
from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.commute.providers.mock import (
    MockEtaProvider,
    MockPlaceProvider,
    MockPushProvider,
    MockWeatherProvider,
    TemplateOnlyAdviceTextProvider,
)
from assistant_api.commute.providers.open_meteo import OpenMeteoWeatherProvider
from assistant_api.commute.providers.weatherkit import WeatherKitProvider


class CommuteConfigurationError(RuntimeError):
    """Fail startup rather than silently generating mock facts in production."""


class UnavailableWeatherProvider:
    """Explicitly unavailable; never fabricate weather when Open-Meteo is absent."""

    provider = "unavailable"

    async def fetch(self, **_kwargs):
        raise ProviderNotConfigured("weather_not_configured")

    async def aclose(self) -> None:
        return None


class UnavailableEtaProvider:
    provider = "unavailable"
    available = False

    async def fetch(self, **_kwargs):
        raise ProviderNotConfigured("eta_not_configured")

    async def aclose(self) -> None:
        return None


class UnavailablePlaceProvider:
    provider = "unavailable"
    available = False

    async def search(self, **_kwargs):
        raise ProviderNotConfigured("amap_not_configured")

    async def reverse_geocode(self, **_kwargs):
        raise ProviderNotConfigured("amap_not_configured")


class DisabledPushProvider:
    """APNs is unreachable in this Bark-only deployment."""

    provider = "disabled"

    async def send(self, _message):
        raise ProviderNotConfigured("apns_not_configured")

    async def aclose(self) -> None:
        return None


class SystemClock:
    def __call__(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class CommuteRuntime:
    settings: Settings
    now: Callable[[], datetime]
    weather: WeatherProvider
    eta: RouteEtaProvider
    advice_text: AdviceTextProvider
    push: PushProvider
    provider_mode: str
    places: PlaceProvider | None = None
    bark: BarkNotifier | None = None
    nexus: NexusPilotClient | None = None

    async def aclose(self) -> None:
        providers = (
            self.weather,
            self.eta,
            self.places,
            self.advice_text,
            self.push,
            self.bark,
            self.nexus,
        )
        closed: set[int] = set()
        for provider in providers:
            if provider is None or id(provider) in closed:
                continue
            closed.add(id(provider))
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()


def validate_commute_configuration(settings: Settings) -> None:
    errors = commute_configuration_errors(settings)
    if errors:
        raise CommuteConfigurationError(
            "Commute reminder configuration is incomplete: " + ", ".join(errors)
        )


def build_runtime(settings: Settings, *, clock: Callable[[], datetime] | None = None) -> CommuteRuntime:
    now = clock or SystemClock()
    if settings.commute_provider_mode == "mock":
        return CommuteRuntime(
            settings=settings,
            now=now,
            weather=MockWeatherProvider(
                now=now,
                rain_probability=settings.commute_mock_rain_probability,
                fail=settings.commute_mock_fail_weather,
            ),
            eta=MockEtaProvider(
                now=now,
                duration_seconds=settings.commute_mock_eta_seconds,
                fail=settings.commute_mock_fail_eta,
            ),
            advice_text=(
                HttpAdviceTextProvider(
                    url=settings.commute_advice_model_url,
                    token=(
                        settings.commute_advice_model_token.get_secret_value()
                        if settings.commute_advice_model_token
                        else None
                    ),
                    timeout_seconds=settings.commute_advice_model_timeout_seconds,
                )
                if settings.commute_advice_model_url
                else TemplateOnlyAdviceTextProvider()
            ),
            push=MockPushProvider(now=now, result=settings.commute_mock_push_result),
            provider_mode="mock",
            places=MockPlaceProvider(),
            nexus=NexusPilotClient(
                base_url=settings.nexus_base_url,
                application_id=settings.nexus_application_id,
                application_token=settings.nexus_application_token.get_secret_value(),
                request_timeout_seconds=settings.nexus_request_timeout_seconds,
                stream_timeout_seconds=settings.nexus_stream_timeout_seconds,
            ),
        )

    validate_commute_configuration(settings)
    weather = OpenMeteoWeatherProvider(
        timeout_seconds=settings.commute_http_timeout_seconds,
        now=now,
        base_url=settings.open_meteo_base_url,
    )
    amap = (
        AmapProvider(
            api_key=settings.commute_amap_api_key.get_secret_value(),
            base_url=settings.commute_amap_base_url,
            timeout_seconds=settings.commute_http_timeout_seconds,
            now=now,
        )
        if settings.commute_amap_api_key
        else None
    )
    eta = amap or (
        MapsEtaProvider(
            team_id=settings.commute_maps_team_id or "",
            key_id=settings.commute_maps_key_id or "",
            maps_id=settings.commute_maps_id or "",
            private_key_path=settings.commute_maps_private_key_path,
            base_url=settings.commute_maps_base_url,
            timeout_seconds=settings.commute_http_timeout_seconds,
            now=now,
        )
        if settings.commute_maps_private_key_path
        else UnavailableEtaProvider()
    )
    return CommuteRuntime(
        settings=settings,
        now=now,
        weather=weather,
        eta=eta,
        advice_text=(
            HttpAdviceTextProvider(
                url=settings.commute_advice_model_url,
                token=(
                    settings.commute_advice_model_token.get_secret_value()
                    if settings.commute_advice_model_token
                    else None
                ),
                timeout_seconds=settings.commute_advice_model_timeout_seconds,
            )
            if settings.commute_advice_model_url
            else TemplateOnlyAdviceTextProvider()
        ),
        push=(
            ApnsPushProvider(
                team_id=settings.commute_apns_team_id or "",
                key_id=settings.commute_apns_key_id or "",
                private_key_path=settings.commute_apns_private_key_path,
                topic=settings.commute_apns_topic or "",
                environment=settings.commute_apns_environment,
                now=now,
                timeout_seconds=settings.commute_apns_timeout_seconds,
                host=settings.commute_apns_host,
            )
            if settings.commute_apns_private_key_path
            else DisabledPushProvider()
        ),
        provider_mode="live",
        places=amap or UnavailablePlaceProvider(),
        nexus=NexusPilotClient(
            base_url=settings.nexus_base_url,
            application_id=settings.nexus_application_id,
            application_token=settings.nexus_application_token.get_secret_value(),
            request_timeout_seconds=settings.nexus_request_timeout_seconds,
            stream_timeout_seconds=settings.nexus_stream_timeout_seconds,
        ),
    )
