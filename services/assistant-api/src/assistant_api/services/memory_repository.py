"""Own memory persistence and execution; Nexus receives only explicitly supplied task data.

Bodies, provenance and exact command requests use authenticated encryption in
Assistant's database. The stable private key is operator-provisioned, never
derived from an application credential. No method calls a model or Nexus API.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from assistant_api.core.errors import AssistantError, ConflictError, ForbiddenError, NotFoundError
from assistant_api.models import MemoryEntry, MemoryScope, MemoryVersion


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
