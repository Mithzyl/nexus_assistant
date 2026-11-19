"""Import one account's owner-scoped Nexus memory without deleting the source.

Requires a configured independent key, an already backed-up SQLite database with
0028 applied, and that account's existing Nexus App mapping. Pause user writes
during transfer; source versions/settings are checked again before committing.
Run .venv/bin/python scripts/import_owned_memory.py --user-id UUID [--apply].
Without --apply only bounded source reads/validation occur. Never prints memory
text, credentials or keys. --apply additionally makes a SQLite backup first.
Only owner entries/history/settings are copied; Nexus operation audit stays in
the original platform and is not claimed as imported.
"""
import argparse
import asyncio
import json
import os
import sqlite3
from datetime import UTC,datetime
from pathlib import Path
from sqlalchemy.engine import make_url

from assistant_api.core.config import get_settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import User
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.services.memory_repository import MemoryStore


async def read_all(client,user_id,**kwargs):
    """Read complete owner pages with a finite bound; incomplete histories stop the transfer."""
    items=[];cursor=0
    while cursor<=10000:
        page=await client.list_application_memory(user_id,offset=cursor,limit=50,**kwargs)
        items.extend(page['items'])
        next_offset=page.get('next_offset')
        if next_offset is None:
            return items
        if not page['items'] or next_offset<=cursor:
            raise RuntimeError('Source pagination did not advance')
        cursor=next_offset
    raise RuntimeError('Source exceeds bounded import; no partial data imported')


async def run(args):
    """Check target ownership, snapshot source, back up SQLite, then atomically import unchanged histories."""
    os.umask(0o077)
    settings=get_settings()
    url=make_url(settings.database_url)
    if url.get_backend_name()!='sqlite' or not url.database or url.database==':memory:':
        raise RuntimeError('Only a persistent local SQLite target is supported')
    engine,factory=create_engine_and_factory(settings.database_url)
    store=MemoryStore(factory,settings.memory_encryption_key_path)
    store.cipher()
    try:
        async with factory() as db:
            if await db.get(User,args.user_id) is None:
                raise RuntimeError('Target Assistant account is absent')
        client=NexusPilotClient(base_url=settings.nexus_base_url,application_id=settings.nexus_application_id,
            application_token=settings.nexus_application_token.get_secret_value(),
            request_timeout_seconds=settings.nexus_request_timeout_seconds,
            stream_timeout_seconds=settings.nexus_stream_timeout_seconds)
        try:
            controls=await client.application_memory_settings(args.user_id)
            entries=[]
            for entry in await read_all(client,args.user_id):
                entries.append({'entry':entry,'versions':await read_all(client,args.user_id,history_entry_id=entry['entry_id'])})
            for exported in entries:
                current=await client.list_application_memory(args.user_id,history_entry_id=exported['entry']['entry_id'],limit=1)
                if current['current_version']!=exported['entry']['version']:
                    raise RuntimeError('Source changed during import; no target writes made')
            if (await client.application_memory_settings(args.user_id))['version']!=controls['version']:
                raise RuntimeError('Source settings changed during import; no target writes made')
        finally:
            await client.aclose()
        result={'entries':len(entries),'versions':sum(len(e['versions']) for e in entries),'applied':False}
        if args.apply:
            path=Path(url.database).resolve()
            backup=path.with_name(path.name+'.before-owned-memory-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+'.backup')
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            result.update(await store.import_account(args.user_id,entries,controls),applied=True,backup=str(backup))
        print(json.dumps(result))
    finally:
        await engine.dispose()


def main():
    """Select one explicit account; no all-user sweep or deletion is performed."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id',required=True)
    parser.add_argument('--apply',action='store_true')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
