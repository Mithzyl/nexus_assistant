"""Measure the authenticated history endpoint against twenty isolated tasks."""
from statistics import median
from time import perf_counter

import pytest
from sqlalchemy import event

from assistant_api.models import NexusRun
from assistant_api.services import matter_plan
from conftest import auth


@pytest.mark.asyncio
async def test_twenty_history_rows_use_bounded_queries(harness):
    """Count every SQL statement, including authentication, for mixed-generation history."""
    async with harness.app.state.session_factory() as db:
        engine = db.bind.sync_engine
        for i in range(20):
            context = {'text': str(i)}
            if i % 2:
                context.update(caller_processed=True, api_flow={})
                context['api_flow']['entry_mode'] = 'sync_chat'
            db.add(NexusRun(user_id=harness.user_a, purpose=matter_plan.PURPOSE,
                            request_text=str(i), idempotency_key=f'query-count-{i}',
                            status='pending', context_json=context))
        await db.commit()
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        """Collect statement kinds without recording parameter values."""
        statements.append(statement.split()[0])
    event.listen(engine, 'before_cursor_execute', count)
    try:
        timings = []
        counts = []
        for _ in range(5):
            statements.clear()
            start = perf_counter()
            response = await harness.client.get('/api/v1/matters/intake?limit=20', headers=auth(harness.token_a))
            timings.append((perf_counter() - start) * 1000)
            counts.append(len(statements))
            assert response.status_code == 200 and len(response.json()) == 20
    finally:
        event.remove(engine, 'before_cursor_execute', count)
    print(f'history endpoint: {counts} SQL, median {median(timings):.2f} ms')
    assert max(counts) <= 9
