"""Install the current user's backend supervisor. Run with --install explicitly."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess

root = Path(__file__).resolve().parents[1]
label = 'local.superapp.assistant-backend'
parser = argparse.ArgumentParser()
parser.add_argument('--install', action='store_true')
args = parser.parse_args()
log = root / 'data/prod/backend.log'
config = {
    'Label': label,
    'ProgramArguments': [str(root / '.venv/bin/python'), str(root / 'scripts/run_backend.py')],
    'WorkingDirectory': str(root),
    'RunAtLoad': True,
    'KeepAlive': True,
    'ThrottleInterval': 30,
    'ExitTimeOut': 45,
    'StandardOutPath': str(log),
    'StandardErrorPath': str(log),
}
if args.install:
    destination = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SystemExit('Existing launch agent found; inspect it before replacing.')
    log.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(plistlib.dumps(config))
    destination.chmod(0o600)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(destination)], check=True)
    print('Installed', label)
else:
    print(plistlib.dumps(config).decode())
