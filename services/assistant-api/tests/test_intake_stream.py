from assistant_api.services.intake_stream import answer_prefix

def test_only_public_question_answer_is_streamed():
    assert answer_prefix('{"intent":"matter","answer":"不要显示"') == ''
    assert answer_prefix('{"intent":"question","answer":"你好') == '你好'
    assert answer_prefix('{"intent":"question","answer":"你好\\n世界","confidence":1}') == '你好\n世界'
    assert answer_prefix('{"intent":"question","answer":"你好\\u4e') == '你好'
    assert answer_prefix('{"intent":"question","answer":"你好\\u4e16') == '你好世'


def test_nested_nexus_final_text_stream():
    import json
    inner = '{"intent":"question","answer":"当前事项是开会"}'
    outer = json.dumps({"final_text": inner}, ensure_ascii=False)
    assert answer_prefix(outer) == "当前事项是开会"
    prefix = outer[:outer.index("开会")]
    assert answer_prefix(prefix) == "当前事项是"

import pytest
from conftest import auth

@pytest.mark.asyncio
async def test_sse_emits_public_partial_then_validated_result(harness, monkeypatch):
    from assistant_api.services import matters
    from assistant_api.services.intake_stream import sink
    async def fake_intake(*args, **kwargs):
        observe = sink.get()
        observe('{"intent":"question","answer":"当前')
        observe('事项"}')
        return {'outcome':'answer','intent':'question','answer':'当前事项','local_run_id':'test-run'}
    monkeypatch.setattr(matters, 'intake', fake_intake)
    response=await harness.client.post('/api/v1/matters/intake/stream',headers=auth(harness.token_a),json={'text':'查询事项','idempotency_key':'stream-unit-0001'})
    assert response.status_code==200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.text.count('event: answer')==2
    assert response.text.index('event: answer') < response.text.index('event: result')
    assert '当前事项' in response.text
