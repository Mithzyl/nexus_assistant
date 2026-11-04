"""One focused isolated verification of correction/answer storage, migration and AI input.

Run only this module with pytest. It uses synthetic accounts, a temporary SQLite
database and the existing controlled Nexus transport; no real model, application
database, frontend, map or notification service is contacted.
"""

import json
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant_api.core.config import Settings
from assistant_api.main import create_app
from assistant_api.models import Base, Matter, NexusRun, User
from assistant_api.models.supplement import MatterSupplement, SupplementText
from assistant_api.services import supplements
from assistant_api.services.execution_context import dispatch_local
from scripts.migrate_sqlite import upgrade
from conftest import FakeNexus


async def request_json(client, method, path, body=None, status=200):
    """Call the real owned routes and expose an explicit failure if the response differs."""
    response = await client.request(method, path, json=body)
    assert response.status_code == status, (path, response.status_code, response.text)
    return response.json()


@pytest.mark.asyncio
async def test_corrections_answers_and_ai_context_preserve_originals(tmp_path):
    """Verify one migration-to-context flow without replaying a real model or changing user data."""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'relations.db'}"
    settings = Settings(
        _env_file=None, environment="test", database_url=database_url, allow_schema_create=False,
        nexus_base_url="http://controlled-nexus", nexus_application_id="personal_assistant",
        nexus_application_token="controlled-application-token-32-characters",
        commute_provider_mode="mock", commute_auto_ai_enabled=False, commute_auto_weather_enabled=False,
    )
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as db:
        owner = User(user_id="owner-account", email="owner@relations.invalid", display_name="合成账号",
                     password_verifier="disabled-test-login", status="active")
        other = User(user_id="other-account", email="other@relations.invalid", display_name="另一合成账号",
                     password_verifier="disabled-test-login", status="active")
        db.add_all([owner, other]); await db.flush()
        matter = Matter(matter_id="owned-matter", user_id=owner.user_id, title="套餐说明", status="active")
        foreign_matter = Matter(matter_id="foreign-matter", user_id=other.user_id, title="其他事项", status="active")
        db.add_all([matter, foreign_matter]); await db.flush()
        root_request = {"text": "原始用户说明", "idempotency_key": "legacy-original-request", "parent_text_id": None}
        root = MatterSupplement(supplement_id="original-supplement", matter_id=matter.matter_id,
                                user_id=owner.user_id, idempotency_key=root_request["idempotency_key"],
                                request_json=root_request, ai_status="succeeded", classification="explanation",
                                created_at=now - timedelta(days=2))
        db.add(root); await db.flush()
        original = SupplementText(text_id="original-user-text", supplement_id=root.supplement_id,
                                  origin="user", purpose="original", text=root_request["text"],
                                  author_user_id=owner.user_id)
        summary = SupplementText(text_id="assistant-summary", supplement_id=root.supplement_id,
                                 origin="assistant", purpose="summary", text="旧摘要：两个独立产品。",
                                 parent_text_id=original.text_id, accepted_at=now,
                                 accepted_by_user_id=owner.user_id)
        question = SupplementText(text_id="assistant-question", supplement_id=root.supplement_id,
                                  origin="assistant", purpose="question", text="39元是合并后的每月费用吗？",
                                  parent_text_id=original.text_id)
        db.add(original); await db.flush(); db.add_all([summary, question]); await db.flush()
        legacy_request = {"text": "过去未标注类型的关联文字", "idempotency_key": "legacy-related-request",
                          "parent_text_id": question.text_id}
        legacy = MatterSupplement(supplement_id="legacy-linked", matter_id=matter.matter_id,
                                  user_id=owner.user_id, idempotency_key=legacy_request["idempotency_key"],
                                  request_json=legacy_request, ai_status="no_supplement", classification="unknown",
                                  created_at=now - timedelta(days=1))
        db.add(legacy); await db.flush()
        db.add(SupplementText(text_id="legacy-linked-text", supplement_id=legacy.supplement_id,
                              origin="user", purpose="user_revision", text=legacy_request["text"],
                              parent_text_id=question.text_id, author_user_id=owner.user_id))
        # More than eight newer records force context construction to retrieve the actual old parent.
        for index in range(9):
            row = MatterSupplement(supplement_id=f"recent-{index}", matter_id=matter.matter_id,
                                   user_id=owner.user_id, idempotency_key=f"recent-request-{index}",
                                   request_json={"text": "其他补充"}, ai_status="no_supplement",
                                   created_at=now + timedelta(microseconds=index))
            db.add(row); await db.flush()
            db.add(SupplementText(supplement_id=row.supplement_id, origin="user", purpose="original",
                                  text="其他补充", author_user_id=owner.user_id))
        await db.commit()
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX ix_supplement_text_relations"))
        await conn.execute(text("ALTER TABLE supplement_texts DROP COLUMN relation_type"))
        legacy_before = (await conn.execute(text(
            "SELECT text_id,origin,purpose,text,parent_text_id,accepted_at,accepted_by_user_id FROM supplement_texts ORDER BY text_id"
        ))).fetchall()
    await engine.dispose()
    await upgrade(database_url)
    async with engine.connect() as conn:
        legacy_after = (await conn.execute(text(
            "SELECT text_id,origin,purpose,text,parent_text_id,accepted_at,accepted_by_user_id FROM supplement_texts ORDER BY text_id"
        ))).fetchall()
        assert legacy_after == legacy_before
        assert (await conn.execute(text("SELECT count(*) FROM supplement_texts WHERE relation_type IS NOT NULL"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM schema_migrations WHERE version='0025_supplement_relations'"))).scalar() == 1
    await engine.dispose()

    fake = FakeNexus()
    app = create_app(settings, nexus_transport=httpx.MockTransport(fake.handler))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=partial(dispatch_local, app, user_id="owner-account"))
        async with httpx.AsyncClient(transport=transport, base_url="http://isolated-assistant") as client:
            path = "/api/v1/matters/owned-matter/supplements"
            old = await request_json(client, "GET", path + "/original-supplement")
            old_question = next(item for item in old["texts"] if item["text_id"] == "assistant-question")
            assert old_question["answer_count"] == 0 and not old_question["has_answers"]
            assert old_question["untyped_relation_count"] == 1
            assert old_question["untyped_relations"][0]["text_id"] == "legacy-linked-text"
            replay = await request_json(client, "POST", path, root_request, 201)
            assert replay["replayed"] and replay["supplement_id"] == "original-supplement"
            replay = await request_json(client, "POST", path, legacy_request, 201)
            assert replay["replayed"] and replay["supplement_id"] == "legacy-linked"
            answer_body = {"text": "39元是两个组成部分合并后的每月总费用。",
                           "idempotency_key": "answer-request-001", "parent_text_id": "assistant-question",
                           "relation_type": "answer"}
            answer = await request_json(client, "POST", path, answer_body, 201)
            answer_text = answer["texts"][0]
            assert answer_text["origin"] == "user" and answer_text["relation_type"] == "answer"
            correction_body = {"text": "纠正：不是两个独立产品，是同一套餐的两个组成部分。",
                               "idempotency_key": "correction-request-001", "parent_text_id": "assistant-summary",
                               "relation_type": "correction"}
            correction = await request_json(client, "POST", path, correction_body, 201)
            assert correction["texts"][0]["relation_type"] == "correction"
            again = await request_json(client, "POST", path, answer_body, 201)
            assert again["replayed"] and again["supplement_id"] == answer["supplement_id"]
            await request_json(client, "POST", path, {**answer_body, "relation_type": "correction"}, 409)
            await request_json(client, "POST", path, {**answer_body, "idempotency_key": "invalid-answer-target", "parent_text_id": "assistant-summary"}, 422)
            await request_json(client, "POST", path, {**answer_body, "parent_text_id": None}, 422)
            await request_json(client, "POST", path, {**answer_body, "origin": "assistant"}, 422)
            foreign = httpx.ASGITransport(app=partial(dispatch_local, app, user_id="other-account"))
            async with httpx.AsyncClient(transport=foreign, base_url="http://isolated-assistant") as denied:
                await request_json(denied, "GET", path + "/original-supplement", status=404)
                await request_json(denied, "POST", "/api/v1/matters/foreign-matter/supplements", answer_body, 404)
            await request_json(client, "POST", path, {
                **answer_body, "text": "补充回答：是否包含其他优惠还需要核对。", "idempotency_key": "answer-request-002",
            }, 201)
            root_read = await request_json(client, "GET", path + "/original-supplement")
            summary_read = next(item for item in root_read["texts"] if item["text_id"] == "assistant-summary")
            question_read = next(item for item in root_read["texts"] if item["text_id"] == "assistant-question")
            assert summary_read["has_corrections"] and summary_read["correction_count"] == 1
            assert summary_read["corrections"][0]["text"] == correction_body["text"]
            assert summary_read["origin"] == "assistant" and summary_read["accepted_at"]
            assert question_read["has_answers"] and question_read["answer_count"] == 2
            assert all(item["origin"] == "user" and item["relation_type"] == "answer" for item in question_read["answers"])
            relations_path = path + "/original-supplement/texts/assistant-question/relations"
            first_page = await request_json(client, "GET", relations_path + "?relation_type=answer&limit=1")
            assert first_page["has_more"] and len(first_page["records"]) == 1
            next_page = await request_json(client, "GET", relations_path + "?relation_type=answer&limit=1&before_id=" + first_page["next_before_id"])
            assert not next_page["has_more"]
            assert first_page["records"][0]["text_id"] != next_page["records"][0]["text_id"]
            await request_json(client, "GET", relations_path + "?before_id=assistant-summary", status=404)
            listing = await request_json(client, "GET", path + "?limit=50")
            assert next(row for row in listing if row["supplement_id"] == "original-supplement")["texts"] == root_read["texts"]

            fake.final_text = json.dumps({"classification": "explanation", "supplements": []}, ensure_ascii=False)
            assert await supplements.worker_tick(app, worker_id="relation-verification")
            analyzed = await request_json(client, "GET", path + "/" + answer["supplement_id"])
            assert analyzed["ai_status"] == "no_supplement"
            async with app.state.session_factory() as db:
                run = await db.get(NexusRun, analyzed["local_run_id"])
                assert run.context_json["relation_type"] == "answer"
                records = {item["text_id"]: item for item in run.context_json["supplement_context"]}
                assert records["assistant-question"]["purpose"] == "question"
                assert records[answer_text["text_id"]]["relation_type"] == "answer"
                assert records[correction["texts"][0]["text_id"]]["relation_type"] == "correction"
                assert records["assistant-summary"]["correction_count"] == 1
                assert records["assistant-question"]["answer_count"] == 2
            fake.final_text = json.dumps({"decision": "answer", "answer": "已结合你的纠正和回答理解，不改变安排。", "cards": []}, ensure_ascii=False)
            planned = await request_json(client, "POST", "/api/v1/matters/owned-matter/intake", {
                "text": "结合该事项的纠正和回答说明已有资料，不改变任何安排。", "idempotency_key": "relation-planning-request",
            })
            async with app.state.session_factory() as db:
                run = await db.get(NexusRun, planned["local_run_id"])
                assert run.context_json["actor"]["user_id"] == "owner-account"
                records = {item["text_id"]: item for item in run.context_json["supplement_context"]}
                assert records["assistant-question"]["answer_count"] == 2
                assert records["assistant-summary"]["origin"] == "assistant"
                assert records[correction["texts"][0]["text_id"]]["origin"] == "user"
                assert run.context_json["api_flow"]["receipts"] == []
                assert await db.scalar(select(Matter).where(Matter.matter_id == "owned-matter")) is not None
            prompts = "\n".join(item["body"] for item in fake.calls if item["path"] == "/api/v1/app/runs")
            assert "relation_type" in prompts and "不代表问题已经解决" in prompts
            assert fake.run_seq == 2, "Only one controlled analysis and one controlled planning call are expected"
