from __future__ import annotations

import json
import time
import uuid
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .container import Container
from .errors import ApiError
from .models import User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container


C = Annotated[Container, Depends(get_container)]


async def get_db(c: C):
    async with c.session_factory() as session:
        yield session


DB = Annotated[AsyncSession, Depends(get_db)]


async def current_user(request: Request, c: C, db: DB,
                       creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if creds is None:
        raise ApiError(401, "unauthorized", "Нужен заголовок Authorization: Bearer <token>")
    try:
        claims = decode_access_token(c.settings.secret_key, creds.credentials)
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Access-токен истёк, обновите его через /auth/refresh")
    except jwt.PyJWTError:
        raise ApiError(401, "unauthorized", "Недействительный токен")
    user = await db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise ApiError(401, "unauthorized", "Пользователь недоступен")
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: str):
    async def dep(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Для этой операции нужна другая роль")
        return user
    return dep


# ---------- лимиты ----------
async def check_rate(c: Container, name: str, ident: str, limit: int, response: Response | None = None) -> None:
    now = time.time()
    n = await c.kv.incr(f"rl:{name}:{ident}:{int(now // 60)}", ex=70)
    reset = 60 - int(now % 60)
    if response is not None:
        response.headers["RateLimit-Limit"] = str(limit)
        response.headers["RateLimit-Remaining"] = str(max(0, limit - n))
        response.headers["RateLimit-Reset"] = str(reset)
    if n > limit:
        raise ApiError(429, "rate_limited", "Слишком много запросов, повторите позже",
                       headers={"Retry-After": str(reset), "RateLimit-Limit": str(limit), "RateLimit-Remaining": "0",
                                "RateLimit-Reset": str(reset)})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, setting: str):
    async def dep(request: Request, response: Response, c: C) -> None:
        ident = getattr(request.state, "user_id", None) or client_ip(request)
        await check_rate(c, name, ident, getattr(c.settings, setting), response)
    return dep


default_rate_limit = Depends(rate_limit("default", "rate_limit_default_per_min"))
auth_rate_limit = Depends(rate_limit("auth_ip", "rate_limit_auth_per_min"))


# ---------- идемпотентность ----------
class Idempotency:
    def __init__(self, c: Container, request: Request, user_id: uuid.UUID | None) -> None:
        self.c = c
        raw = request.headers.get("Idempotency-Key")
        self.key = f"idem:{user_id}:{request.method}:{request.url.path}:{raw}" if raw else None

    async def replay(self) -> JSONResponse | None:
        if not self.key:
            return None
        cached = await self.c.kv.get(self.key)
        if cached is None:
            return None
        data = json.loads(cached)
        return JSONResponse(data["body"], status_code=data["status"], headers={"Idempotent-Replayed": "true"})

    async def store(self, status: int, body: Any) -> None:
        if self.key:
            await self.c.kv.set(self.key, json.dumps({"status": status, "body": body}, default=str), ex=86400)


async def idempotency(request: Request, c: C, user: CurrentUser) -> Idempotency:
    return Idempotency(c, request, user.id)


Idem = Annotated[Idempotency, Depends(idempotency)]
