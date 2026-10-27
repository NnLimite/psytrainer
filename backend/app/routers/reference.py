from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from ..deps import DB, current_user, default_rate_limit
from ..models import DisorderRef
from ..pagination import paginate
from ..schemas import DisorderOut, Page

router = APIRouter(prefix="/disorders", tags=["Reference"], dependencies=[Depends(current_user), default_rate_limit])


@router.get("", response_model=Page)
async def list_disorders(db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: str | None = None,
                         classifier: str | None = None, q: str | None = None):
    stmt = select(DisorderRef).order_by(DisorderRef.classifier, DisorderRef.code)
    if classifier:
        stmt = stmt.where(DisorderRef.classifier == classifier)
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func
        stmt = stmt.where(or_(func.lower(DisorderRef.name).like(like), func.lower(DisorderRef.code).like(like)))
    return await paginate(db, stmt, limit, cursor, lambda d: DisorderOut.model_validate(d))
