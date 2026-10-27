"""Курсорная пагинация. Курсор непрозрачный (внутри смещение), клиенту его нужно только возвращать."""
from __future__ import annotations

import base64
import json
from typing import Any, Callable

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import ApiError


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"]))
    except Exception:
        raise ApiError(400, "bad_request", "Некорректный cursor")


async def paginate(db: AsyncSession, stmt: Select, limit: int, cursor: str | None,
                   convert: Callable[[Any], Any] = lambda x: x, *, scalars: bool = True) -> dict[str, Any]:
    offset = decode_cursor(cursor)
    result = await db.execute(stmt.limit(limit + 1).offset(offset))
    rows = list(result.scalars().all() if scalars else result.all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [convert(r) for r in rows], "next_cursor": encode_cursor(offset + limit) if has_more else None}
