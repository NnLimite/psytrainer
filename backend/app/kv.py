"""Мини-абстракция над Redis: ключ-значение с TTL, счётчики, SET NX. MemoryKV нужен для тестов и dev без Redis."""
from __future__ import annotations

import time
from typing import Protocol


class KV(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ex: int | None = None) -> None: ...
    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool: ...
    async def incr(self, key: str, ex: int | None = None) -> int: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...


class MemoryKV:
    def __init__(self) -> None:
        self._d: dict[str, tuple[str, float | None]] = {}

    def _alive(self, key: str) -> str | None:
        item = self._d.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and exp < time.monotonic():
            del self._d[key]
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._alive(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._d[key] = (value, time.monotonic() + ex if ex else None)

    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool:
        if self._alive(key) is not None:
            return False
        await self.set(key, value, ex)
        return True

    async def incr(self, key: str, ex: int | None = None) -> int:
        cur = self._alive(key)
        n = int(cur) + 1 if cur is not None else 1
        exp = self._d[key][1] if cur is not None else (time.monotonic() + ex if ex else None)
        self._d[key] = (str(n), exp)
        return n

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)

    async def ping(self) -> bool:
        return True


class RedisKV:
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._r = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self._r.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._r.set(key, value, ex=ex)

    async def set_nx(self, key: str, value: str, ex: int | None = None) -> bool:
        return bool(await self._r.set(key, value, ex=ex, nx=True))

    async def incr(self, key: str, ex: int | None = None) -> int:
        n = await self._r.incr(key)
        if n == 1 and ex:
            await self._r.expire(key, ex)
        return int(n)

    async def delete(self, key: str) -> None:
        await self._r.delete(key)

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def aclose(self) -> None:
        await self._r.aclose()
