"""Run API, commute worker and scheduler together using the service .env."""
from pathlib import Path
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / 'src'))
    from assistant_api.core.config import get_settings
    from assistant_api.commute.runtime import validate_commute_configuration
    validate_commute_configuration(get_settings())
    # Refuse a second API before starting consumers with external side effects.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8010))
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    commands = {
        'api': ['uvicorn', 'assistant_api.main:app', '--host', '127.0.0.1', '--port', '8010'],
        'worker': ['assistant_api.commute.worker'],
        'scheduler': ['assistant_api.commute.scheduler'],
    }
    children = {}
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code = 0
    try:
        for name, args in commands.items():
            children[name] = subprocess.Popen([sys.executable, '-m', *args], cwd=ROOT, env=env)
            print(f'{name} started pid={children[name].pid}', flush=True)
        while not stopping:
            for name, child in children.items():
                if child.poll() is not None:
                    print(f'{name} exited; stopping backend group', flush=True)
                    code = 1
                    stopping = True
                    break
            time.sleep(.5)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
