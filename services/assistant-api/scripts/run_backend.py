"""Run the current API and unified background processes with an explicit configuration file.

Usage from this service directory:
  .venv/bin/python scripts/run_backend.py --env-file /absolute/path/to/.env
The runner does not migrate or initialize storage. It retains configured provider
and automation flags and stops the group if any owned child exits.
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

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Load the selected existing environment and supervise API, scheduler and worker together."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    args=parser.parse_args()
    config=args.env_file.resolve(strict=True)
    from dotenv import dotenv_values
    values={key:value for key,value in dotenv_values(config).items() if value is not None}
    relative=[key for key,value in values.items() if key.endswith('_PATH') and value and not Path(value).is_absolute()]
    for key in relative:
        values[key]=str((config.parent / values[key]).resolve())
    os.environ.update(values)
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT/'src'))
    from assistant_api.core.config import get_settings
    from assistant_api.commute.runtime import validate_commute_configuration
    validate_commute_configuration(get_settings())
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',8010))
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'))
    commands={
        'api':['uvicorn','assistant_api.main:app','--host','127.0.0.1','--port','8010'],
        'scheduler':['assistant_api.background','scheduler'],
        'worker':['assistant_api.background','worker'],
    }
    children={};stopping=False
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
            for name,child in children.items():
                if child.poll() is not None:
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
