"""Local credential primitives: password verifiers, session tokens, tool tokens.

Only digests are persisted. Secrets are compared in constant time, and a missing
or malformed credential never raises a distinguishable error path.
"""

import hashlib
import hmac
import secrets

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Return a self-describing scrypt verifier; never store the original password."""

    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, verifier: str) -> bool:
    """Check a password against a stored verifier without leaking timing details."""

    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, hash_hex = verifier.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(bytes.fromhex(hash_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), hash_hex)


def new_session_token() -> str:
    """Create an opaque bearer token handed to one browser or device."""

    return f"pas_{secrets.token_urlsafe(TOKEN_BYTES)}"


def digest_token(token: str) -> str:
    """Reduce a bearer token to the only form this service is allowed to keep."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(presented: str, expected_digest: str) -> bool:
    """Constant-time comparison between a presented token and a stored digest."""

    if not presented:
        return False
    return hmac.compare_digest(digest_token(presented), expected_digest)
