"""Run the current API and unified background processes with an explicit configuration file.

Usage from this service directory:
  .venv/bin/python scripts/run_backend.py --env-file /absolute/path/to/.env
The runner does not migrate or initialize storage. It retains configured provider
and automation flags. Core process exits stop the group; the monitor restarts independently.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Supervise API, scheduler, worker and read-only monitoring under one lifecycle."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--bind-host',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1')
    parser.add_argument('--nexus-loopback-host',default=None,
        help='Route a host-loopback Nexus URL through this container-accessible host; do not edit the env file.')
    args=parser.parse_args()
    config=args.env_file.resolve(strict=True)
    from dotenv import dotenv_values
    values={key:value for key,value in dotenv_values(config).items() if value is not None}
    relative=[key for key,value in values.items() if key.endswith('_PATH') and value and not Path(value).is_absolute()]
    for key in relative:
        values[key]=str((config.parent / values[key]).resolve())
    os.environ.update(values)
    if args.nexus_loopback_host:
        nexus=urlsplit(os.environ.get('ASSISTANT_NEXUS_BASE_URL','http://127.0.0.1:8000'))
        if nexus.hostname in {'127.0.0.1','localhost','::1'}:
            if nexus.username or nexus.password:
                raise ValueError('Nexus URL with embedded credentials cannot be translated; original config preserved')
            host=args.nexus_loopback_host+(f':{nexus.port}' if nexus.port else '')
            os.environ['ASSISTANT_NEXUS_BASE_URL']=urlunsplit((nexus.scheme,host,nexus.path,nexus.query,nexus.fragment))
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT/'src'))
    from assistant_api.core.config import get_settings
    from assistant_api.commute.runtime import validate_commute_configuration
    from assistant_api.core.observability import emit_event
    validate_commute_configuration(get_settings())
    with socket.socket() as probe:
        probe.bind((args.bind_host,8010))
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'))
    commands={
        'api':['uvicorn','assistant_api.main:app','--host',args.bind_host,'--port','8010','--no-access-log'],
        'scheduler':['assistant_api.background','scheduler'],
        'worker':['assistant_api.background','worker'],
    }
    children={};stopping=False
    monitor = None
    monitor_retry_at = 0.0
    def stop(*_args):
        """Request graceful termination of this runner's own children only."""
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    code=0
    try:
        for name,args in commands.items():
            children[name]=subprocess.Popen([sys.executable,'-m',*args],cwd=ROOT,env=env)
            print(f'{name} started pid={children[name].pid}',flush=True)
        print(f'backend source={ROOT} config={config}',flush=True)
        while not stopping:
            if monitor is not None and monitor.poll() is not None:
                emit_event('monitor_process_exit',level='error',category='monitor_failure',exit_code=monitor.returncode)
                children.pop('monitor',None)
                monitor = None
                monitor_retry_at = time.monotonic()+15
            if monitor is None and time.monotonic()>=monitor_retry_at:
                try:
                    monitor = subprocess.Popen([sys.executable,'-m','assistant_api.operations_monitor'],cwd=ROOT,env=env)
                    children['monitor'] = monitor
                except OSError:
                    emit_event('monitor_process_exit',level='error',category='monitor_failure',error_code='spawn_failed')
                    monitor_retry_at = time.monotonic()+15
            for name,child in children.items():
                if name != 'monitor' and child.poll() is not None:
                    print(f'{name} exited; stopping backend group',flush=True)
                    code=1;stopping=True;break
            time.sleep(.5)
    finally:
        for child in children.values():
            if child.poll() is None:child.terminate()
        for child in children.values():
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill();child.wait()
    return code


if __name__=='__main__':
    raise SystemExit(main())
