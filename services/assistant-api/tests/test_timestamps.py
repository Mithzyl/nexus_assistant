"""Ask timestamps are unambiguous UTC ISO-8601 in the JSON contract."""

import re

from conftest import auth, create_ask

UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


async def test_ask_read_publishes_utc_z_timestamps(harness):
    created = await create_ask(harness, token=harness.token_a, key="timestamp-key-1")
    local_run_id = created.json()["local_run_id"]
    for _ in range(50):
        response = await harness.client.get(
            f"/api/v1/asks/{local_run_id}", headers=auth(harness.token_a)
        )
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
            break
        await __import__("asyncio").sleep(0.01)
    assert UTC_ISO.match(body["created_at"]), body["created_at"]
    assert body["completed_at"] is None or UTC_ISO.match(body["completed_at"]), body
