"""Create or validate Assistant's independent memory key without editing env files.

Run .venv/bin/python scripts/configure_memory.py --key-file /private/config/assistant-memory.key
The file is created once with mode 0600; an existing key is validated and kept.
Back up this key privately together with the database. Key rotation is not implemented.
"""
import argparse
import base64
import os
from pathlib import Path


def main():
    """Provision a stable random key with exclusive creation, printing only the configuration path."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file',type=Path,required=True)
    args=parser.parse_args()
    path=args.key_file.expanduser().resolve()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not path.exists():
        with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
            stream.write(base64.urlsafe_b64encode(os.urandom(32)))
    key=base64.b64decode(path.read_bytes().strip(),altchars=b'-_',validate=True)
    if len(key)!=32:
        raise SystemExit('Existing memory key is invalid; file preserved.')
    print('ASSISTANT_MEMORY_ENCRYPTION_KEY_PATH='+str(path))


if __name__=='__main__':
    main()
