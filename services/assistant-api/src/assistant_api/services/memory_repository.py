"""Own memory persistence and execution; Nexus receives only explicitly supplied task data.

Bodies, provenance and exact command requests use authenticated encryption in
Assistant's database. The stable private key is operator-provisioned, never
derived from an application credential. No method calls a model or Nexus API.
"""
from __future__ import annotations

import base64
import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from assistant_api.core.errors import AssistantError, ConflictError, ForbiddenError, NotFoundError
from assistant_api.models import MemoryEntry, MemoryOperation, MemoryScope, MemoryVersion, NexusRun
from assistant_api.models.base import new_id


def for_session(db):
    """Resolve the local store attached at app composition, never fall back to Nexus storage."""
    return db.info['memory_store']


class MemoryStore:
    """Expose the compatible entry API over Assistant-owned encrypted SQL rows."""

    def __init__(self, factory, key_path: Path | None):
        """Keep a separate session factory so business transactions are never committed by a memory read."""
        self.factory = factory
        self.key_path = key_path

    def cipher(self):
        """Load the stable key; missing or invalid keys fail explicitly without creating a replacement."""
        try:
            raw = self.key_path.read_bytes().strip() if self.key_path else b''
            key = base64.b64decode(raw, altchars=b'-_', validate=True)
            if len(key) != 32:
                raise ValueError('invalid length')
            return AESGCM(key)
        except (OSError, ValueError) as exc:
            raise AssistantError('memory_key_unavailable', 'Assistant 记忆加密密钥未配置或不可用。', 503) from exc

    def encrypt(self, user_id, value):
        """Encrypt complete JSON with account-bound authenticated data and a fresh random nonce."""
        nonce = os.urandom(12)
        return nonce + self.cipher().encrypt(nonce,
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), ('assistant-memory:'+user_id).encode())

    def decrypt(self, user_id, value):
        """Reject damaged content or a wrong key instead of representing it as an empty memory."""
        try:
            return json.loads(self.cipher().decrypt(value[:12], value[12:], ('assistant-memory:'+user_id).encode()))
        except (InvalidTag, ValueError, TypeError, UnicodeError) as exc:
            raise AssistantError('memory_content_unavailable', 'Assistant 记忆正文无法解密，原数据保持不变。', 503) from exc

    def project(self, user_id, row):
        """Provide Nexus-compatible names plus the explicit entry revision and original authorship."""
        body = self.decrypt(user_id, row.body)
        return {'entry_id':row.entry_id, 'entry_version':row.entry_version, 'version':row.entry_version,
            'status':row.status, 'sensitive':row.sensitive, 'memory_type':row.memory_type,
            **body, 'schema_version':1, 'created_at':row.created_at.replace(tzinfo=UTC).isoformat(),
            'updated_at':row.updated_at.replace(tzinfo=UTC).isoformat()}

    async def list_application_memory(self, user_id, *, offset=0, limit=20, history_entry_id=None):
        """Read only the trusted owner's entries or immutable versions; no prior Agent is necessary."""
        self.cipher()
        async with self.factory() as db:
            if history_entry_id:
                current = await db.scalar(select(MemoryEntry).where(MemoryEntry.entry_id==history_entry_id,
                    MemoryEntry.user_id==user_id))
                if current is None:
                    raise NotFoundError('memory_not_found', '未找到当前账号的记忆。')
                rows = list(await db.scalars(select(MemoryVersion).where(MemoryVersion.entry_id==history_entry_id)
                    .order_by(MemoryVersion.entry_version.desc()).offset(offset).limit(limit+1)))
                return {'entry_id':history_entry_id, 'current_version':current.entry_version,
                    'items':[self.project(user_id, row) for row in rows[:limit]],
                    'next_offset':offset+limit if len(rows)>limit else None}
            rows = list(await db.scalars(select(MemoryEntry).where(MemoryEntry.user_id==user_id)
                .order_by(MemoryEntry.created_at, MemoryEntry.entry_id).offset(offset).limit(limit+1)))
            return {'items':[self.project(user_id, row) for row in rows[:limit]],
                'next_offset':offset+limit if len(rows)>limit else None}

    async def application_memory_settings(self, user_id):
        """Read automatic-use settings without creating rows or incrementing revisions."""
        async with self.factory() as db:
            scope = await db.get(MemoryScope, user_id)
            return {'enabled':scope.enabled if scope else True, 'version':scope.scope_revision if scope else 0,
                'scope_revision':scope.scope_revision if scope else 0, 'mode':'entry', 'storage':'assistant'}

    async def lock_scope(self, db, user_id):
        """Serialize per-account writes in SQLite, including competing workers and first-time writers."""
        await db.execute(insert(MemoryScope).values(user_id=user_id, enabled=True, scope_revision=0)
            .on_conflict_do_nothing(index_elements=['user_id']))
        await db.execute(update(MemoryScope).where(MemoryScope.user_id==user_id)
            .values(scope_revision=MemoryScope.scope_revision))
        return await db.get(MemoryScope, user_id, populate_existing=True)

    async def apply(self, db, user_id, command):
        """Apply one validated command inside the caller's locked transaction; revisions only increase."""
        scope = await db.get(MemoryScope, user_id, populate_existing=True)
        if (command.get('_source') or {}).get('proposed_by')=='ai' and not scope.enabled:
            raise ConflictError('business_memory_disabled','自动记忆已关闭，未执行模型提出的修改。')
        action = command['action']
        expected = command.get('expected_entry_version', command.get('expected_version'))
        if action == 'settings':
            if scope.scope_revision != command['expected_version']:
                raise ConflictError('memory_version_conflict', '记忆设置已变化，请重新读取。')
            scope.enabled = command['enabled']
            scope.scope_revision += 1
            return {'enabled':scope.enabled, 'version':scope.scope_revision, 'scope_revision':scope.scope_revision}
        if action == 'disclose':
            raise ConflictError('memory_disclosure_not_required', 'Assistant 自有记忆无需授权 Nexus 存储；不会修改平台记忆。')
        body = {}
        if action in {'save','propose'}:
            entry = MemoryEntry(entry_id=new_id(), user_id=user_id, entry_version=1,
                status='candidate' if action=='propose' else 'active', sensitive=command.get('sensitive',False))
            db.add(entry)
        else:
            entry = await db.scalar(select(MemoryEntry).where(MemoryEntry.entry_id==command.get('entry_id'),
                MemoryEntry.user_id==user_id).execution_options(populate_existing=True))
            if entry is None:
                raise NotFoundError('memory_not_found', '未找到当前账号的记忆。')
            if entry.entry_version != expected:
                raise ConflictError('memory_version_conflict', '记忆已变化，请重新读取，不覆盖新内容。')
            body = self.decrypt(user_id, entry.body)
            if action == 'undo':
                prior = await db.scalar(select(MemoryVersion).where(MemoryVersion.entry_id==entry.entry_id,
                    MemoryVersion.entry_version<entry.entry_version).order_by(MemoryVersion.entry_version.desc()).limit(1))
                if prior is None:
                    raise ConflictError('memory_undo_unavailable', '没有可恢复的上一版本。')
                body = self.decrypt(user_id, prior.body)
                entry.status, entry.sensitive, entry.memory_type = prior.status, prior.sensitive, prior.memory_type
            elif action == 'forget':
                entry.status = 'forgotten'
            elif action == 'confirm':
                entry.status = 'active'
            elif action != 'correct':
                raise ConflictError('memory_action_invalid', '不支持该记忆操作。')
            entry.entry_version += 1
        if action in {'save','propose','correct'}:
            body['text'] = command['text']
            for name in ('app_data','typed_data'):
                if command.get(name) is not None:
                    body[name] = command[name]
            entry.memory_type = (body.get('typed_data') or {}).get('memory_type','free_text')
        source = command.get('_source') or {'kind':'owner_command', 'text_origin':'user_quote' if action in {'save','propose','correct'} else 'retained_content',
            'proposed_by':'user', 'executed_by':'assistant', 'source_quote':command.get('text')}
        # Keep previous versions immutable; a correction keeps the original source
        # and adds the new user quote, rather than relabelling model text as user text.
        if 'source' in body:
            source = {**source, 'original_source':body['source'].get('original_source',body['source'])}
        body['source'] = source
        body.setdefault('app_data',None)
        body.setdefault('typed_data',None)
        entry.body = self.encrypt(user_id, body)
        entry.schema_version = 1
        await db.flush()
        db.add(MemoryVersion(entry_id=entry.entry_id, entry_version=entry.entry_version, status=entry.status,
            sensitive=entry.sensitive, memory_type=entry.memory_type, body=entry.body))
        scope.scope_revision += 1
        return {'entry_id':entry.entry_id, 'entry_version':entry.entry_version, 'version':entry.entry_version,
            'status':entry.status, 'applied_version':entry.entry_version}

    async def mutate_application_memory(self, user_id, command):
        """Commit the change and its receipt together, returning the operation ID for chat writes."""
        local_run_id = (command.get('_source') or {}).get('local_run_id')
        async with self.factory() as db, db.begin():
            await self.lock_scope(db,user_id)
            existing = await db.scalar(select(MemoryOperation).where(MemoryOperation.user_id==user_id,
                MemoryOperation.key==command['key']))
            if existing:
                if self.decrypt(user_id,existing.request_body) != command:
                    raise ConflictError('memory_request_conflict','该请求标识已用于不同的记忆操作。')
                result = copy.deepcopy(existing.result_json)
                if result and result.get('entry_id') and existing.status=='succeeded':
                    entry = await db.get(MemoryEntry,result['entry_id'])
                    result.update(version=entry.entry_version,entry_version=entry.entry_version,status=entry.status)
                    if local_run_id:
                        result['operation_id'] = existing.operation_id
                return result
            result = await self.apply(db,user_id,command)
            operation = MemoryOperation(user_id=user_id,key=command['key'],status='succeeded',
                local_run_id=local_run_id, request_body=self.encrypt(user_id,command),
                result_json=result,completed_at=datetime.now(UTC))
            db.add(operation)
            if local_run_id:
                await db.flush()
                return {**result, 'operation_id': operation.operation_id}
            return result


async def agent_command(db, store, user, local_run_id, value, *, key):
    """Validate the current trusted user quote and record model interpretation before local execution or queueing."""
    from assistant_api.schemas.memory import AgentMemoryCommand
    payload = AgentMemoryCommand.model_validate(value)
    root = await db.scalar(select(NexusRun).where(NexusRun.local_run_id==local_run_id,NexusRun.user_id==user.user_id))
    flow = (root.context_json or {}).get('api_flow',{}) if root else {}
    if (not root or root.status not in {'pending','running'} or flow.get('trigger_source','chat')!='chat'
            or payload.source_quote not in flow.get('text','')):
        raise ForbiddenError('memory_source_mismatch','记忆操作必须引用本次真实用户原文，不能使用历史指令或 AI 回答。')
    if (await store.application_memory_settings(user.user_id))['enabled'] is not True:
        raise ConflictError('business_memory_disabled','自动记忆已关闭；本人仍可从记忆管理页面操作。')
    if payload.intent=='incidental' and payload.action!='propose':
        raise ForbiddenError('memory_explicit_intent_required','非明确记忆请求只允许保存候选，不能修改或删除有效事实。')
    command = payload.model_dump(mode='json',exclude_none=True,exclude={'execution','intent','source_quote'})
    command['key'] = key
    command['_source'] = {'kind':'user_message','local_run_id':local_run_id,'nexus_run_id':root.nexus_run_id,
        'matter_id':flow.get('matter_id'), 'source_quote':payload.source_quote,
        'text_origin':'user_quote' if payload.text and payload.text in flow['text'] else 'model_rewrite',
        'proposed_by':'ai','executed_by':'assistant','intent':payload.intent}
    applied_sources=[]
    for receipt in flow.get('receipts',[]):
        data=receipt.get('data')
        if isinstance(data,dict):
            applied_sources.extend(((data.get('preference_application') or {}).get('fields') or {}).values())
    # Merely retrieving a candidate is not a business dependency. In particular,
    # a request to forget it must not be forced synchronous just because the
    # initial lookup correctly found that target.
    if payload.execution=='after_answer' and payload.entry_id and any(
            source.get('entry_id')==payload.entry_id and source.get('source')=='memory'
            and source.get('status')=='applied' for source in applied_sources):
        raise ConflictError('memory_deferred_dependency','本次任务已使用该记忆，修改需当场执行并重新核对后续引用。')
    if payload.execution=='before_answer':
        return await store.mutate_application_memory(user.user_id,command)
    # Queueing never changes the active entry. A future conflict is a failed job,
    # not permission to overwrite a newer revision or automatically retry a write.
    async with store.factory() as queue_db, queue_db.begin():
        await store.lock_scope(queue_db,user.user_id)
        prior = await queue_db.scalar(select(MemoryOperation).where(MemoryOperation.user_id==user.user_id,
            MemoryOperation.key==key))
        if prior:
            if store.decrypt(user.user_id,prior.request_body)!=command:
                raise ConflictError('memory_request_conflict','该请求标识已用于不同的记忆操作。')
            return prior.result_json
        if payload.entry_id:
            entry=await queue_db.scalar(select(MemoryEntry).where(MemoryEntry.entry_id==str(payload.entry_id),
                MemoryEntry.user_id==user.user_id))
            if entry is None:
                raise NotFoundError('memory_not_found','未找到当前账号的记忆。')
            if entry.entry_version!=payload.expected_entry_version:
                raise ConflictError('memory_version_conflict','记忆已变化，请重新读取。')
        operation_id=new_id()
        result={'operation_id':operation_id,'status':'queued','execution':'after_answer','applied':False}
        queue_db.add(MemoryOperation(operation_id=operation_id,user_id=user.user_id,key=key,local_run_id=local_run_id,
            status='awaiting_answer',request_body=store.encrypt(user.user_id,command),result_json=result))
        return result


async def release_after_answer(app, local_run_id):
    """Make deferred work eligible only after a valid final answer is persisted; no model is called here."""
    from assistant_api.services.operation_intake import response_for
    async with app.state.session_factory() as db:
        root=await db.get(NexusRun,local_run_id)
        if root and root.status in {'failed','cancelled'}:
            await db.execute(update(MemoryOperation).where(MemoryOperation.local_run_id==local_run_id,
                MemoryOperation.status=='awaiting_answer').values(status='cancelled',error_code='memory_answer_not_completed'))
            await db.commit()
            return
        if not root or root.status!='succeeded' or response_for(root).get('outcome')!='answer':
            return
        await db.execute(update(MemoryOperation).where(MemoryOperation.local_run_id==local_run_id,
            MemoryOperation.status=='awaiting_answer').values(status='ready'))
        await db.commit()


async def process_pending(app, *, limit=10):
    """Execute bounded post-answer local work transactionally; restarts preserve jobs, errors remain inspectable."""
    store=app.state.memory_store
    async with store.factory() as db:
        pending=list(await db.scalars(select(MemoryOperation.local_run_id).where(
            MemoryOperation.status=='awaiting_answer').distinct().limit(limit)))
    for local_id in pending:
        await release_after_answer(app,local_id)
    async with store.factory() as db:
        ids=list(await db.scalars(select(MemoryOperation.operation_id).where(MemoryOperation.status=='ready')
            .order_by(MemoryOperation.created_at).limit(limit)))
    handled=0
    for identity in ids:
        async with store.factory() as db, db.begin():
            row=await db.get(MemoryOperation,identity)
            await store.lock_scope(db,row.user_id)
            await db.refresh(row)
            if row.status!='ready':
                continue
            try:
                async with db.begin_nested():
                    command=store.decrypt(row.user_id,row.request_body)
                    if not (await db.get(MemoryScope,row.user_id)).enabled:
                        raise ConflictError('business_memory_disabled','记忆已关闭，未执行待处理修改。')
                    result=await store.apply(db,row.user_id,command)
                    await db.flush()
                row.status='succeeded'
                row.result_json=result
            except AssistantError as exc:
                row.status='failed'
                row.error_code=exc.code
                row.result_json={'operation_id':identity,'status':'failed','applied':False,'error_code':exc.code}
            row.completed_at=datetime.now(UTC)
            handled+=1
    return handled
