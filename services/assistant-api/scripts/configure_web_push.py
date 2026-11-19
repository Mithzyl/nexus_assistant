"""Create a persistent Web Push key pair without replacing keys or editing existing env files.

Run: .venv/bin/python scripts/configure_web_push.py --directory /private/config/browser-push
The private PEM stays on disk with mode 0600; output contains only environment
names, the public key, and the private file path. Reuse these keys on restarts.
"""
import argparse
import base64
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption


def main():
    """Create once or reuse an existing private key, printing no private material."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = args.directory.resolve() / 'vapid-private.pem'
    if path.exists():
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        key = load_pem_private_key(path.read_bytes(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as stream:
            stream.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).decode().rstrip('=')
    print('ASSISTANT_WEB_PUSH_ENABLED=true')
    print('ASSISTANT_WEB_PUSH_PRIVATE_KEY_PATH=' + str(path))
    print('ASSISTANT_WEB_PUSH_PUBLIC_KEY=' + public)
    print('ASSISTANT_WEB_PUSH_CONTACT=mailto:notifications@example.invalid')


if __name__ == '__main__':
    main()
