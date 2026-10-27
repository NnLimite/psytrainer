from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update

from ..deps import DB, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import LlmCall, PromptTemplate
from ..pagination import paginate
from ..schemas import Page, PromptTemplateIn, PromptTemplateOut
from ..services import access

router = APIRouter(prefix="/admin", tags=["Admin"],
                   dependencies=[Depends(current_user), default_rate_limit, Depends(require_roles("platform_admin"))])
REQUIRED_PLACEHOLDER = {"classifier": "{keys}", "judge": "{rubric}"}


@router.get("/prompt-templates", response_model=Page)
async def list_templates(db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: str | None = None,
                         kind: str | None = None):
    stmt = select(PromptTemplate).order_by(PromptTemplate.kind, PromptTemplate.version.desc())
    if kind:
        stmt = stmt.where(PromptTemplate.kind == kind)
    return await paginate(db, stmt, limit, cursor, lambda t: PromptTemplateOut.model_validate(t))


@router.post("/prompt-templates", response_model=PromptTemplateOut, status_code=201)
async def create_template(body: PromptTemplateIn, db: DB):
    need = REQUIRED_PLACEHOLDER.get(body.kind)
    if need and need not in body.body:
        raise ApiError(422, "validation_failed", f"В шаблоне {body.kind} должен быть плейсхолдер {need}")
    last = (await db.execute(select(func.max(PromptTemplate.version)).where(PromptTemplate.kind == body.kind))).scalar() or 0
    t = PromptTemplate(kind=body.kind, version=last + 1, body=body.body, is_active=False)
    db.add(t)
    await db.commit()
    return t


@router.post("/prompt-templates/{template_id}/activate", response_model=PromptTemplateOut)
async def activate_template(template_id: uuid.UUID, db: DB):
    t = await db.get(PromptTemplate, template_id)
    if t is None:
        raise access.not_found("Шаблон")
    await db.execute(update(PromptTemplate).where(PromptTemplate.kind == t.kind).values(is_active=False))
    t.is_active = True
    await db.commit()
    return t


def _p95(values: list[int]) -> int:
    s = sorted(values)
    return s[max(0, -(-len(s) * 95 // 100) - 1)] if s else 0  # nearest-rank


@router.get("/llm-usage")
async def llm_usage(db: DB, from_: Annotated[datetime | None, Query(alias="from")] = None, to: datetime | None = None) -> dict[str, Any]:
    stmt = select(LlmCall)
    if from_:
        stmt = stmt.where(LlmCall.created_at >= from_)
    if to:
        stmt = stmt.where(LlmCall.created_at <= to)
    groups: dict[tuple[str, str], list[LlmCall]] = {}
    for call in (await db.execute(stmt)).scalars().all():
        groups.setdefault((call.model, call.purpose), []).append(call)
    items = [{"model": m, "purpose": p, "calls": len(rows), "tokens_in": sum(r.tokens_in for r in rows),
              "tokens_out": sum(r.tokens_out for r in rows), "p95_latency_ms": _p95([r.latency_ms for r in rows]),
              "cost": round(sum(r.cost for r in rows), 4)} for (m, p), rows in sorted(groups.items())]
    return {"from": from_, "to": to, "items": items}
