"""Optional model wording may never delay or alter the deterministic reminder."""

from __future__ import annotations

import pytest

from assistant_api.commute.providers.base import AdviceText
from tests.commute_flow import create_plan, run_full_auto_cycle
from tests.test_commute_worker_flow import register_device


@pytest.mark.asyncio
async def test_valid_model_wording_is_used(commute_harness):
    h = commute_harness
    h.advice_text.response = AdviceText(
        title="08:20 出发更稳妥",
        summary="驾车约 30 分钟，基础预留 10 分钟，预计 09:00 前到达。",
    )
    await create_plan(h, h.token_a)
    check, _ = await run_full_auto_cycle(h)
    async with h.app.state.session_factory() as db:
        from assistant_api.models import CommuteAdvisory
        from sqlalchemy import select

        advisory = await db.scalar(select(CommuteAdvisory))
    assert check.status == "succeeded"
    assert advisory.explanation_source == "model"
    assert advisory.title == "08:20 出发更稳妥"
    # The facts sent to the model contain no coordinates or address labels.
    facts = h.advice_text.calls[0]
    serialized = str(vars(facts))
    assert "31.2304" not in serialized and "121.4737" not in serialized
    assert "家" not in serialized and "公司" not in serialized


@pytest.mark.asyncio
async def test_model_invented_number_falls_back_to_template(commute_harness):
    h = commute_harness
    h.advice_text.response = AdviceText(
        title="08:20 出发", summary="驾车约 20 分钟即可到达。"
    )
    await create_plan(h, h.token_a)
    check, _ = await run_full_auto_cycle(h)
    async with h.app.state.session_factory() as db:
        from assistant_api.models import CommuteAdvisory
        from sqlalchemy import select

        advisory = await db.scalar(select(CommuteAdvisory))
    assert check.status == "succeeded"
    assert advisory.explanation_source == "template"
    assert "20 分钟" not in advisory.summary


@pytest.mark.asyncio
async def test_model_timeout_does_not_block_the_reminder(commute_harness):
    h = commute_harness
    h.settings.commute_advice_model_timeout_seconds = 0.01
    h.advice_text.delay_seconds = 0.2
    h.advice_text.response = AdviceText(title="迟到的文案", summary="不应出现。")
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert check.status == "succeeded"
    assert outbox is not None and outbox.status == "accepted_by_bark"
    async with h.app.state.session_factory() as db:
        from assistant_api.models import CommuteAdvisory
        from sqlalchemy import select

        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory.explanation_source == "template"
    assert advisory.title != "迟到的文案"


@pytest.mark.asyncio
async def test_model_weather_alert_is_rejected(commute_harness):
    h = commute_harness
    h.advice_text.response = AdviceText(
        title="暴雨红色预警", summary="建议 08:20 出发，注意安全。"
    )
    await create_plan(h, h.token_a)
    await run_full_auto_cycle(h)
    async with h.app.state.session_factory() as db:
        from assistant_api.models import CommuteAdvisory
        from sqlalchemy import select

        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory.explanation_source == "template"
    assert "预警" not in advisory.title
