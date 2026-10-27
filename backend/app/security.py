from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()
_DUMMY = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    """Всегда тратит одинаковое время, даже если пользователя нет (защита от перебора email)."""
    try:
        return _hasher.verify(hashed or _DUMMY, password) and hashed is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(secret: str, *, user_id: str, role: str, org_id: str | None, ttl_minutes: int) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "role": role, "org": org_id, "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    return jwt.encode(payload, secret, algorithm="HS256"), ttl_minutes * 60


def decode_access_token(secret: str, token: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
