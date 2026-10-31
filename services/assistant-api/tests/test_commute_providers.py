"""Provider adapters: JWT separation, parsing, errors and APNs headers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from assistant_api.commute.providers.apns import ApnsPushProvider
from assistant_api.commute.providers.base import (
    ProviderAuthError,
    ProviderRateLimited,
    PushMessage,
)
from assistant_api.commute.providers.maps_eta import MapsEtaProvider
from assistant_api.commute.providers.weatherkit import WeatherKitProvider
from assistant_api.core.config import Settings, commute_configuration_errors

NOW = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)


@pytest.fixture()
def keypair(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "key.p8"
    path.write_bytes(private_pem)
    return path, public_pem


@pytest.mark.asyncio
async def test_weatherkit_uses_own_es256_jwt_and_keeps_missing_probability_null(keypair):
    path, public_pem = keypair
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "forecastHourly": {
                    "hours": [
                        {
                            "forecastStart": "2026-10-23T00:00:00Z",
                            "forecastEnd": "2026-10-23T01:00:00Z",
                            "temperature": 21.5,
                            "precipitationChance": 0.55,
                        },
                        {
                            "forecastStart": "2026-10-23T01:00:00Z",
                            "forecastEnd": "2026-10-23T02:00:00Z",
                            "temperature": 22.0,
                        },
                    ]
                }
            },
        )

    provider = WeatherKitProvider(
        team_id="TEAM123",
        key_id="WEATHERKEY",
        service_id="com.example.weather",
        private_key_path=path,
        base_url="https://weatherkit.apple.com",
        language="zh-Hans",
        timeout_seconds=5,
        now=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.fetch(
        origin=(31.2, 121.4),
        destination=(31.3, 121.5),
        window_start=NOW,
        window_end=NOW + timedelta(hours=2),
        timezone="Asia/Shanghai",
    )
    assert len(seen) == 2
    assert all(request.headers["authorization"].startswith("Bearer ") for request in seen)
    assert all("timezone=Asia%2FShanghai" in str(request.url) for request in seen)
    token = seen[0].headers["authorization"].split(" ", 1)[1]
    decoded = jwt.decode(
        token, public_pem, algorithms=["ES256"], options={"verify_aud": False, "verify_exp": False}
    )
    assert decoded["iss"] == "TEAM123"
    assert decoded["sub"] == "com.example.weather"
    assert jwt.get_unverified_header(token)["kid"] == "WEATHERKEY"
    assert result.provider == "weatherkit"
    assert result.origin.periods[1].precipitation_probability is None
    assert result.origin.periods[0].precipitation_probability == 0.55
    await provider.aclose()


@pytest.mark.asyncio
async def test_weatherkit_rate_limit_and_auth_are_distinct(keypair):
    path, _ = keypair

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"})

    provider = WeatherKitProvider(
        team_id="T",
        key_id="K",
        service_id="S",
        private_key_path=path,
        base_url="https://weatherkit.apple.com",
        language="zh-Hans",
        timeout_seconds=5,
        now=lambda: NOW,
        transport=httpx.MockTransport(limited),
    )
    with pytest.raises(ProviderRateLimited) as limited_error:
        await provider.fetch(
            origin=(1, 2),
            destination=(3, 4),
            window_start=NOW,
            window_end=NOW,
            timezone="UTC",
        )
    assert limited_error.value.retry_after_seconds == 7
    await provider.aclose()

    provider = WeatherKitProvider(
        team_id="T",
        key_id="K",
        service_id="S",
        private_key_path=path,
        base_url="https://weatherkit.apple.com",
        language="zh-Hans",
        timeout_seconds=5,
        now=lambda: NOW,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    with pytest.raises(ProviderAuthError):
        await provider.fetch(
            origin=(1, 2),
            destination=(3, 4),
            window_start=NOW,
            window_end=NOW,
            timezone="UTC",
        )
    await provider.aclose()


@pytest.mark.asyncio
async def test_maps_exchanges_its_own_token_and_sends_future_departure(keypair):
    path, public_pem = keypair
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"accessToken": "maps-access", "expiresInSeconds": 1800})
        assert request.headers["authorization"] == "Bearer maps-access"
        assert request.url.params["transportType"] == "AUTOMOBILE"
        assert request.url.params["departAt"] == "2026-10-23T00:50:00Z"
        return httpx.Response(
            200,
            json={
                "etats": [
                    {
                        "distanceMeters": 12000,
                        "expectedTravelTimeSeconds": 1560,
                        "transportType": "AUTOMOBILE",
                    }
                ]
            },
        )

    provider = MapsEtaProvider(
        team_id="TEAM123",
        key_id="MAPSKEY",
        maps_id="com.example.maps",
        private_key_path=path,
        base_url="https://maps-api.apple.com",
        timeout_seconds=5,
        now=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.fetch(
        origin=(31.2, 121.4),
        destination=(31.3, 121.5),
        mode="driving",
        request_depart_at=NOW + timedelta(minutes=50),
        target_arrival_at=NOW + timedelta(hours=1),
    )
    assert result.duration_seconds == 1560
    assert result.provider == "apple_maps_server_api"
    assert calls[0].headers["authorization"].startswith("Bearer ")
    token = calls[0].headers["authorization"].split(" ", 1)[1]
    decoded = jwt.decode(token, public_pem, algorithms=["ES256"], options={"verify_aud": False, "verify_exp": False})
    assert decoded["sub"] == "com.example.maps"
    await provider.aclose()


@pytest.mark.asyncio
async def test_maps_refreshes_access_token_after_401(keypair):
    path, _ = keypair
    token_calls = 0
    eta_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, eta_calls
        if request.url.path == "/v1/token":
            token_calls += 1
            return httpx.Response(200, json={"accessToken": f"token-{token_calls}", "expiresInSeconds": 1800})
        eta_calls += 1
        if eta_calls == 1:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={"etats": [{"durationSeconds": 600, "transportType": "WALKING"}]},
        )

    provider = MapsEtaProvider(
        team_id="T",
        key_id="K",
        maps_id="M",
        private_key_path=path,
        base_url="https://maps-api.apple.com",
        timeout_seconds=5,
        now=lambda: NOW,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.fetch(
        origin=(1, 2),
        destination=(3, 4),
        mode="walking",
        request_depart_at=NOW,
        target_arrival_at=NOW + timedelta(hours=1),
    )
    assert result.duration_seconds == 600
    assert token_calls == 2
    await provider.aclose()


@pytest.mark.asyncio
async def test_apns_payload_headers_and_invalid_token_result(keypair):
    path, public_pem = keypair
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            410,
            json={"reason": "Unregistered", "timestamp": 1_790_000_000_000},
        )

    provider = ApnsPushProvider(
        team_id="TEAM123",
        key_id="APNSKEY",
        private_key_path=path,
        topic="com.example.assistant",
        environment="sandbox",
        now=lambda: NOW,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    result = await provider.send(
        PushMessage(
            apns_id="8d4e2c1a-0000-4000-8000-000000000001",
            device_token="a" * 64,
            topic="com.example.assistant",
            environment="sandbox",
            payload={"aps": {"alert": {"title": "t", "body": "b"}}},
            collapse_id="commute-1",
            expires_at=NOW + timedelta(hours=1),
        )
    )
    assert result.result == "rejected"
    assert result.error_code == "Unregistered"
    assert result.provider_timestamp is not None
    request = seen[0]
    assert request.url.path.endswith("/3/device/" + "a" * 64)
    assert request.headers["apns-topic"] == "com.example.assistant"
    assert request.headers["apns-push-type"] == "alert"
    assert request.headers["apns-collapse-id"] == "commute-1"
    assert request.headers["apns-id"] == "8d4e2c1a-0000-4000-8000-000000000001"
    token = request.headers["authorization"].split(" ", 1)[1]
    decoded = jwt.decode(token, public_pem, algorithms=["ES256"], options={"verify_aud": False, "verify_exp": False})
    assert decoded["iss"] == "TEAM123"
    assert jwt.get_unverified_header(token)["kid"] == "APNSKEY"
    await provider.aclose()


def test_production_refuses_mock_commute_providers():
    settings = Settings(
        environment="production",
        commute_provider_mode="mock",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    errors = commute_configuration_errors(settings)
    assert errors and "mock" in errors[0]
    from assistant_api.commute.runtime import CommuteConfigurationError, validate_commute_configuration

    with pytest.raises(CommuteConfigurationError):
        validate_commute_configuration(settings)


def test_app_startup_refuses_mock_in_production(monkeypatch):
    from assistant_api.commute.runtime import CommuteConfigurationError
    from assistant_api.main import create_app

    settings = Settings(
        environment="production",
        commute_provider_mode="mock",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    with pytest.raises(CommuteConfigurationError):
        create_app(settings)


def test_production_live_starts_without_apple_provider_credentials():
    from assistant_api.main import create_app

    settings = Settings(
        environment="production",
        commute_provider_mode="live",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    assert commute_configuration_errors(settings) == []
    app = create_app(settings)
    assert app is not None


def test_blank_optional_settings_become_none():
    settings = Settings(
        environment="development",
        commute_provider_mode="mock",
        commute_apns_host=" ",
        commute_advice_model_url="",
        commute_weather_team_id="",
    )
    assert settings.commute_apns_host is None
    assert settings.commute_advice_model_url is None
    assert settings.commute_weather_team_id is None


@pytest.mark.asyncio
async def test_open_meteo_parses_hourly_without_keys():
    from assistant_api.commute.providers.open_meteo import OpenMeteoWeatherProvider

    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "hourly": {
                "time": ["2026-10-24T08:00", "2026-10-24T09:00"],
                "temperature_2m": [21.5, 22.0],
                "precipitation_probability": [55, 20],
                "weather_code": [61, 3],
            }
        })
    provider = OpenMeteoWeatherProvider(
        timeout_seconds=1,
        now=lambda: datetime(2026, 10, 24, 0, 0, tzinfo=UTC),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.fetch(
        origin=(31.23, 121.47), destination=(31.22, 121.45),
        window_start=datetime(2026, 10, 24, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 10, 24, 2, 0, tzinfo=UTC),
        timezone="Asia/Shanghai",
    )
    assert result.provider == "open_meteo"
    assert result.origin.periods[0].precipitation_probability == 0.55
    assert result.destination.periods[1].temperature_c == 22.0
    assert all("latitude" in r.url.params for r in seen)
    await provider.fetch(
        origin=(31.23, 121.47), destination=(31.22, 121.45),
        window_start=datetime(2026, 10, 27, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 10, 27, 2, 0, tzinfo=UTC), timezone="Asia/Shanghai",
    )
    assert all(r.url.params["forecast_days"] == "4" for r in seen[-2:])
    await provider.aclose()
