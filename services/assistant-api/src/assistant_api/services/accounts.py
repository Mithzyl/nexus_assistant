"""Shared creation of a local, empty account. Never seeds business data."""
from asyncio import to_thread
from assistant_api.core.security import hash_password
from assistant_api.models import User

async def create_empty_account(db, *, email: str, display_name: str, password: str) -> User:
    user = User(email=email.strip().lower(), display_name=display_name.strip(),
                password_verifier=await to_thread(hash_password, password), status="active")
    db.add(user)
    await db.flush()
    return user
