from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select

from ..deps import DB, CurrentUser, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import GroupMember, PatientSession, StudyGroup, User
from ..pagination import paginate
from ..schemas import GroupIn, GroupOut, GroupPatch, MemberOut, Page
from ..services import access

router = APIRouter(prefix="/groups", tags=["Organizations"], dependencies=[Depends(current_user), default_rate_limit])


async def _counts(db, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(select(GroupMember.group_id, func.count()).where(GroupMember.group_id.in_(ids)).group_by(GroupMember.group_id))
    return {gid: n for gid, n in rows.all()}


def _out(g: StudyGroup, n: int) -> GroupOut:
    return GroupOut(id=g.id, org_id=g.org_id, teacher_id=g.teacher_id, name=g.name, members_count=n)


async def _validate_teacher(db, org_id: uuid.UUID, teacher_id: uuid.UUID) -> None:
    t = await db.get(User, teacher_id)
    if t is None or t.org_id != org_id or t.role not in ("teacher", "org_admin"):
        raise ApiError(422, "validation_failed", "teacher_id должен быть преподавателем этой организации")


@router.get("", response_model=Page)
async def list_groups(db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                      cursor: str | None = None, org_id: uuid.UUID | None = None):
    stmt = select(StudyGroup).order_by(StudyGroup.name, StudyGroup.id)
    if user.role == "teacher":
        stmt = stmt.where(StudyGroup.teacher_id == user.id)
    elif user.role == "student":
        stmt = stmt.where(StudyGroup.id.in_(select(GroupMember.group_id).where(GroupMember.user_id == user.id)))
    elif user.role == "org_admin":
        stmt = stmt.where(StudyGroup.org_id == user.org_id)
    if org_id:
        stmt = stmt.where(StudyGroup.org_id == org_id)
    page = await paginate(db, stmt, limit, cursor)
    counts = await _counts(db, [g.id for g in page["items"]])
    page["items"] = [_out(g, counts.get(g.id, 0)) for g in page["items"]]
    return page


@router.post("", response_model=GroupOut, status_code=201,
             dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def create_group(body: GroupIn, db: DB, user: CurrentUser):
    if user.role != "platform_admin" and body.org_id != user.org_id:
        raise access.not_found("Организация")
    teacher_id = user.id if user.role == "teacher" else (body.teacher_id or user.id)
    await _validate_teacher(db, body.org_id, teacher_id)
    g = StudyGroup(org_id=body.org_id, teacher_id=teacher_id, name=body.name)
    db.add(g)
    await db.commit()
    return _out(g, 0)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(group_id: uuid.UUID, db: DB, user: CurrentUser):
    g = await access.group_for(db, user, group_id)
    return _out(g, (await _counts(db, [g.id])).get(g.id, 0))


@router.patch("/{group_id}", response_model=GroupOut, dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def patch_group(group_id: uuid.UUID, body: GroupPatch, db: DB, user: CurrentUser):
    g = await access.group_for(db, user, group_id, manage=True)
    if body.name is not None:
        g.name = body.name
    if body.teacher_id is not None and body.teacher_id != g.teacher_id:
        if user.role == "teacher":
            raise ApiError(403, "forbidden", "Передать группу может только администратор организации")
        await _validate_teacher(db, g.org_id, body.teacher_id)
        g.teacher_id = body.teacher_id
    await db.commit()
    return _out(g, (await _counts(db, [g.id])).get(g.id, 0))


@router.delete("/{group_id}", status_code=204, dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def delete_group(group_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    g = await access.group_for(db, user, group_id, manage=True)
    if (await db.execute(select(func.count(PatientSession.id)).where(PatientSession.group_id == g.id))).scalar_one():
        raise ApiError(409, "group_has_sessions", "У группы есть сессии, удалить её нельзя")
    for gm in (await db.execute(select(GroupMember).where(GroupMember.group_id == g.id))).scalars().all():
        await db.delete(gm)
    await db.delete(g)
    await db.commit()
    return Response(status_code=204)


@router.get("/{group_id}/members", response_model=Page, dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def list_group_members(group_id: uuid.UUID, db: DB, user: CurrentUser,
                             limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: str | None = None):
    g = await access.group_for(db, user, group_id, manage=True)
    stmt = select(User, GroupMember.joined_at).join(GroupMember, GroupMember.user_id == User.id).where(
        GroupMember.group_id == g.id).order_by(User.full_name, User.id)
    return await paginate(db, stmt, limit, cursor, lambda r: MemberOut(
        user_id=r[0].id, full_name=r[0].full_name, email=r[0].email, role=r[0].role, joined_at=r[1]), scalars=False)


@router.put("/{group_id}/members/{user_id}", response_model=MemberOut,
            dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def add_member(group_id: uuid.UUID, user_id: uuid.UUID, db: DB, user: CurrentUser):
    g = await access.group_for(db, user, group_id, manage=True)
    target = await db.get(User, user_id)
    if target is None or target.org_id != g.org_id or target.role != "student":
        raise ApiError(422, "validation_failed", "В группу можно добавить студента этой организации")
    gm = await db.get(GroupMember, (g.id, target.id))
    if gm is None:
        gm = GroupMember(group_id=g.id, user_id=target.id)
        db.add(gm)
        await db.commit()
        await db.refresh(gm)
    return MemberOut(user_id=target.id, full_name=target.full_name, email=target.email, role=target.role, joined_at=gm.joined_at)


@router.delete("/{group_id}/members/{user_id}", status_code=204,
               dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def remove_group_member(group_id: uuid.UUID, user_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    g = await access.group_for(db, user, group_id, manage=True)
    gm = await db.get(GroupMember, (g.id, user_id))
    if gm is not None:
        await db.delete(gm)
        await db.commit()
    return Response(status_code=204)
