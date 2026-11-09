"""Manage the fixed Assistant Compose service without deleting business storage.

Run with the existing service virtualenv: scripts/manage_backend.py
start|stop|restart|status|logs|build [--env-file /absolute/path/to/.env].
Routine restart reuses the container. Start/update replaces only this project's
changed/orphaned containers; no command removes volumes or prunes other projects.
Docker Desktop must be running. The original SQLite stays under this service's
data directory; source/config/key paths are mounted at the same absolute paths.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    """Dispatch one whole-backend lifecycle action with stable Compose identity and original config paths."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','stop','restart','status','logs','build'])
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    args=parser.parse_args()
    config=args.env_file.resolve(strict=True)
    docker=shutil.which('docker')
    if not docker:
        raise SystemExit('Docker CLI is unavailable; existing services and data were not changed.')
    sys.path.insert(0,str(ROOT/'src'))
    from assistant_api.core.config import Settings
    from sqlalchemy.engine import make_url
    # The selected file, rather than a stale shell export, defines this instance.
    for name in list(os.environ):
        if name.startswith('ASSISTANT_'):
            del os.environ[name]
    settings=Settings(_env_file=config)
    database=Path(make_url(settings.database_url).database or '').resolve()
    if not database.is_file() or not database.is_relative_to(ROOT/'data'):
        raise SystemExit('An existing SQLite under this service data directory is required; no storage was created.')
    key=settings.memory_encryption_key_path
    if not key or not key.is_file():
        raise SystemExit('The original memory key is required; no key was generated or replaced.')
    if args.action in {'start','restart'} and (settings.commute_auto_ai_enabled or settings.allow_schema_create):
        raise SystemExit('Automatic AI or schema creation is enabled; this management entry does not change those settings.')
    env=dict(os.environ,ASSISTANT_SERVICE_ROOT=str(ROOT),ASSISTANT_ENV_FILE=str(config),
        ASSISTANT_MEMORY_KEY_FILE=str(key.resolve()),ASSISTANT_UID=str(os.getuid()),ASSISTANT_GID=str(os.getgid()))
    command=[docker,'compose','--project-name','superapp-assistant','--file',str(ROOT/'compose.yaml')]
    actions={'start':['up','--detach','--remove-orphans','--wait','--wait-timeout','90'],
             'stop':['stop'],'restart':['restart'],'status':['ps','--all'],
             'logs':['logs','--tail','100'],'build':['build']}
    return subprocess.call([*command,*actions[args.action]],cwd=ROOT,env=env)


if __name__=='__main__':
    raise SystemExit(main())
