from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from ..deps import DB, CurrentUser, current_user, default_rate_limit, require_roles
from ..models import PatientSession, SafetyEvent, StudyGroup, User
from ..pagination import paginate
from ..schemas import Page, SafetyEventOut, SafetyEventPatch
from ..services import access

router = APIRouter(prefix="/safety-events", tags=["Safety"],
                   dependencies=[Depends(current_user), default_rate_limit, Depends(require_roles("teacher", "org_admin"))])


async def visible_session_ids_stmt(user: User):
    """Условие видимости сессий для преподавателя (его группы) и администратора организации (вся организация)."""
    if user.role == "teacher":
        return select(PatientSession.id).join(StudyGroup, StudyGroup.id == PatientSession.group_id).where(
            StudyGroup.teacher_id == user.id)
    return select(PatientSession.id).join(User, User.id == PatientSession.user_id).where(User.org_id == user.org_id)


@router.get("", response_model=Page)
async def list_events(db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                      cursor: str | None = None, status: str | None = None, severity: str | None = None,
                      group_id: uuid.UUID | None = None):
    stmt = select(SafetyEvent).where(SafetyEvent.session_id.in_(await visible_session_ids_stmt(user)))
    if status:
        stmt = stmt.where(SafetyEvent.status == status)
    if severity:
        stmt = stmt.where(SafetyEvent.severity == severity)
    if group_id:
        await access.group_for(db, user, group_id)
        stmt = stmt.where(SafetyEvent.session_id.in_(select(PatientSession.id).where(PatientSession.group_id == group_id)))
    return await paginate(db, stmt.order_by(SafetyEvent.created_at.desc()), limit, cursor,
                          lambda e: SafetyEventOut.model_validate(e))


@router.patch("/{event_id}", response_model=SafetyEventOut)
async def patch_event(event_id: uuid.UUID, body: SafetyEventPatch, db: DB, user: CurrentUser):
    ev = await db.get(SafetyEvent, event_id)
    visible = set((await db.execute(await visible_session_ids_stmt(user))).scalars().all())
    if ev is None or ev.session_id not in visible:
        raise access.not_found("Событие")
    ev.status, ev.handled_by = body.status, user.id
    if body.note is not None:
        ev.note = body.note
    await db.commit()
    return ev
