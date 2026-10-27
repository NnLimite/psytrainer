from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from ..deps import C
from ..schemas import HealthOut

router = APIRouter(tags=["System"])


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="ok")


@router.get("/ready", response_model=HealthOut)
async def ready(c: C, response: Response) -> HealthOut:
    checks: dict[str, str] = {}
    try:
        async with c.session_factory() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        checks["redis"] = "ok" if await c.kv.ping() else "down"
    except Exception:
        checks["redis"] = "down"
    checks["llm"] = "ok" if await c.llm.ping() else "down"
    down = [k for k, v in checks.items() if v != "ok"]
    if down:
        response.status_code = 503
    return HealthOut(status="ok" if not down else ("degraded" if checks["database"] == "ok" else "down"), checks=checks)
