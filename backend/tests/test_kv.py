"""RedisKV на fakeredis и MemoryKV: одинаковое поведение, на которое опирается код (NX, счётчики, TTL)."""
from __future__ import annotations

import asyncio

import fakeredis
import pytest

from app.kv import MemoryKV, RedisKV


def redis_kv() -> RedisKV:
    kv = RedisKV.__new__(RedisKV)
    kv._r = fakeredis.FakeAsyncRedis(decode_responses=True)
    return kv


@pytest.fixture(params=["memory", "redis"])
def kv(request):
    return MemoryKV() if request.param == "memory" else redis_kv()


async def test_get_set_delete_and_nx(kv):
    assert await kv.get("a") is None
    await kv.set("a", "1")
    assert await kv.get("a") == "1"
    assert await kv.set_nx("a", "2") is False and await kv.get("a") == "1"
    await kv.delete("a")
    assert await kv.set_nx("a", "3", ex=60) is True and await kv.get("a") == "3"
    assert await kv.ping() is True


async def test_incr_counts_and_expires(kv):
    assert [await kv.incr("c", ex=1) for _ in range(3)] == [1, 2, 3]
    await asyncio.sleep(1.2)
    assert await kv.incr("c", ex=1) == 1  # окно лимита истекло


async def test_ttl_expiry(kv):
    await kv.set("t", "x", ex=1)
    assert await kv.get("t") == "x"
    await asyncio.sleep(1.2)
    assert await kv.get("t") is None
    assert await kv.set_nx("t", "y", ex=5) is True  # после истечения блокировку можно взять снова
