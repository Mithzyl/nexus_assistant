from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
import httpx
import pytest
from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.commute.providers.base import ProviderTimeout, ProviderInvalidResponse
from assistant_api.commute.providers.weather_fallback import AmapWeatherProvider, FallbackWeatherProvider
NOW = datetime(2026,10,30,tzinfo=UTC)
ARGS=dict(origin=(24.282092,109.430627),destination=(24.316047,109.411967),window_start=NOW,window_end=NOW+timedelta(hours=1),timezone='Asia/Shanghai')
@pytest.mark.asyncio
async def test_fallback_and_daily_forecast():
    paths=[]
    def respond(request):
        paths.append(request.url.path)
        if 'regeo' in request.url.path:
            return httpx.Response(200,json={'status':'1','regeocode':{'addressComponent':{'adcode':'450202'}}})
        assert request.url.params['extensions']=='all'
        return httpx.Response(200,json={'status':'1','forecasts':[{'adcode':'450202','casts':[{'date':'2026-10-30','dayweather':'小雨','nightweather':'阴'}]}]})
    amap=AmapProvider(api_key='test',now=lambda:NOW,transport=httpx.MockTransport(respond))
    provider=AmapWeatherProvider(amap,lambda:NOW)
    result=await provider.fetch(**ARGS)
    assert result.provider=='amap'
    assert paths.count('/v3/weather/weatherInfo')==1
    assert result.origin.periods[0].precipitation_probability is None
    assert result.origin.periods[0].temperature_c is None
    assert '小雨' in result.origin.periods[0].condition_code
    with pytest.raises(ProviderInvalidResponse,match='cover'):
        await provider.fetch(**{**ARGS,'window_end':NOW+timedelta(days=5)})
    primary=AsyncMock(); primary.fetch.return_value=result
    fallback=AsyncMock(); fallback.fetch.return_value=result
    chain=FallbackWeatherProvider(primary,fallback,1)
    assert await chain.fetch(**ARGS) is result
    fallback.fetch.assert_not_called()
    primary.fetch.return_value=None
    assert await chain.fetch(**ARGS) is result
    primary.fetch.return_value=replace(result, origin=replace(result.origin, periods=()))
    assert await chain.fetch(**ARGS) is result
    primary.fetch.side_effect=ProviderTimeout('timeout')
    assert await chain.fetch(**ARGS) is result
    fallback.fetch.side_effect=ProviderInvalidResponse('empty',code='amap_weather_invalid_response')
    with pytest.raises(ProviderInvalidResponse) as error:
        await chain.fetch(**ARGS)
    assert error.value.code=='amap_weather_invalid_response'
    await amap.aclose()
