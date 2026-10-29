"""Automatic real-model extraction after confirmation; saving requires a UI action."""
import json
from pydantic import BaseModel, ConfigDict, Field
from assistant_api.services import ai_tasks, run_service
from assistant_api.routers.memory import AppMemoryData
from assistant_api.core.errors import ConflictError

class ExtractedMemory(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str=Field(min_length=1,max_length=4000)
    app_data: AppMemoryData

def parse(text,context):
    value=(text or '').strip()
    if value.startswith('```'):
        value=value.split('\n',1)[1].rsplit('```',1)[0].strip()
    try:return ExtractedMemory.model_validate_json(value).model_dump(mode='json')
    except ValueError as exc:raise ai_tasks.AiTaskResultRejected('memory_extraction_invalid') from exc

ai_tasks.register(ai_tasks.AiTaskSpec(purpose='memory_extract',input_version=1,strict_input_match=True,prepare_input=lambda c:c['text'],validate_result=parse))

async def start(db,client,settings,factory,user,draft):
    state=(draft.payload_json or {}).get('commute')
    if not state or draft.status!='confirmed':return
    data={**state['quote_inputs'],'mode':state['selected_mode'],'timezone':state['timezone'],'arrival_local_time':state['intent']['arrival_local_time'],'suggested_departure_at':state['suggested_departure_at'],'route_observed_at':state['routes'][state['selected_mode']].get('fetched_at'),'estimate_notice':'出发时间来自本次测算，仅供参考，不保证以后每天相同或准点。'}
    # Token signatures are not facts for the model.
    for side in ['origin','destination']:
        data[side]={k:v for k,v in data[side].items() if k in ['name','address','city','latitude','longitude','coordinate_system']}
    prompt='从刚通过网页确认的真实通勤数据提取一条待用户确认的账号记忆。只输出JSON {"text":"正文","app_data":{"namespace":"personal_assistant","schema_version":1,"fields":{}}}。保留起终点、首次生效日、星期、方式、目标到达、余量和本次建议出发时间及其估算日期/局限。test_only为true时正文与fields明确是联调测试，不是真人永久偏好；false时只记录用户已确认的习惯。不得编造、不得含草稿未保存等过时状态，不调用任何保存/修改工具。fields不得含user_id、scope、status等权限字段。数据：'+json.dumps(data,ensure_ascii=False,default=str)
    row,replayed=await run_service.create_ai_task(db,user=user,purpose='memory_extract',trigger='manual',idempotency_key='commute-memory:'+draft.draft_id,context={'text':prompt},business_ref=draft.draft_id)
    draft.result_json={**(draft.result_json or {}),'memory_local_run_id':row.local_run_id};await db.commit()
    if row.status not in run_service.TERMINAL_STATUSES:
        if replayed:await run_service.resume_ai_task(db,client,settings,factory,user=user,row=row)
        else:await run_service.launch_ai_task(db,client,settings,factory,user=user,row=row)

async def candidate(db,client,user,draft):
    result=draft.result_json or {}
    if result.get('memory_saved'):return {**result['memory_saved'],'status':'saved','memory_status':result['memory_saved'].get('status')}
    if not result.get('memory_local_run_id'):return {'status':'unavailable','message':result.get('memory_error') or '尚未触发提取。'}
    row=await run_service.get_run(db,user=user,local_run_id=result['memory_local_run_id'])
    row=await run_service.refresh_run(db,client,user=user,row=row)
    if row.status!='succeeded':return {'status':row.status,'message':row.error_code,'local_run_id':row.local_run_id}
    try:value=parse(run_service.answer_text(row),{})
    except ai_tasks.AiTaskResultRejected:return {'status':'failed','message':'模型提取结果不是有效记忆，未保存。'}
    expected_test=bool(draft.payload_json['commute']['quote_inputs']['test_only'])
    if value['app_data']['fields'].get('test_only') is not expected_test:
        return {'status':'failed','message':'模型未正确保留测试数据性质，未保存。'}
    return {'status':'candidate','local_run_id':row.local_run_id,**value}

async def save(db,client,user,draft):
    value=await candidate(db,client,user,draft)
    if value['status']=='saved':return value
    if value['status']!='candidate':raise ConflictError('memory_not_ready','记忆提取未完成，不能保存。')
    row=await run_service.get_run(db,user=user,local_run_id=value['local_run_id'])
    saved=await client.mutate_product_memory(user.user_id,row.nexus_run_id,{'scope':'personal','action':'save','key':'commute-memory-save:'+draft.draft_id,'text':value['text'],'app_data':value['app_data'],'sensitive':True,'confirmed_sensitive':True})
    draft.result_json={**draft.result_json,'memory_saved':saved};await db.commit()
    return {**saved,'status':'saved','memory_status':saved.get('status')}
