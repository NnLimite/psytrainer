from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi import Response
from sqlalchemy import func, select

from ..deps import C, DB, CurrentUser, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import GroupMember, Invitation, Organization, StudyGroup, Subscription, User
from ..pagination import paginate
from ..schemas import (
    InvitationIn, InvitationOut, MemberOut, MemberPatch, OrganizationOut, OrganizationPatch, Page, SubscriptionOut,
)
from ..security import new_token, token_hash
from ..services import access

router = APIRouter(prefix="/organizations", tags=["Organizations"],
                   dependencies=[Depends(current_user), default_rate_limit])


def _member(u: User) -> MemberOut:
    return MemberOut(user_id=u.id, full_name=u.full_name, email=u.email, role=u.role, joined_at=u.created_at)


async def org_for(db, user: User, org_id: uuid.UUID, *, admin: bool = False) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise access.not_found("Организация")
    if user.role == "platform_admin":
        return org
    allowed = ("org_admin",) if admin else ("teacher", "org_admin")
    if user.org_id != org_id or user.role not in allowed:
        raise access.not_found("Организация")
    return org


async def _admins_count(db, org_id: uuid.UUID) -> int:
    return (await db.execute(select(func.count(User.id)).where(User.org_id == org_id, User.role == "org_admin",
                                                               User.is_active.is_(True)))).scalar_one()


@router.get("/{org_id}", response_model=OrganizationOut, dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def get_org(org_id: uuid.UUID, db: DB, user: CurrentUser):
    return await org_for(db, user, org_id)


@router.patch("/{org_id}", response_model=OrganizationOut, dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def patch_org(org_id: uuid.UUID, body: OrganizationPatch, db: DB, user: CurrentUser):
    org = await org_for(db, user, org_id, admin=True)
    org.name = body.name
    await db.commit()
    return org


@router.get("/{org_id}/members", response_model=Page, dependencies=[Depends(require_roles("teacher", "org_admin", "platform_admin"))])
async def list_members(org_id: uuid.UUID, db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                       cursor: str | None = None, role: str | None = None):
    await org_for(db, user, org_id)
    stmt = select(User).where(User.org_id == org_id, User.deleted_at.is_(None)).order_by(User.full_name, User.id)
    if role:
        stmt = stmt.where(User.role == role)
    return await paginate(db, stmt, limit, cursor, _member)


@router.post("/{org_id}/invitations", response_model=InvitationOut, status_code=201,
             dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def invite(org_id: uuid.UUID, body: InvitationIn, c: C, db: DB, user: CurrentUser):
    org = await org_for(db, user, org_id, admin=True)
    email = body.email.lower()
    if body.group_id is not None:
        g = await db.get(StudyGroup, body.group_id)
        if g is None or g.org_id != org.id:
            raise ApiError(422, "validation_failed", "Группа не принадлежит организации")
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None and existing.org_id is not None:
        raise ApiError(409, "already_exists", "Пользователь уже состоит в организации")
    if (await db.execute(select(Invitation).where(Invitation.org_id == org.id, Invitation.email == email,
                                                  Invitation.status == "pending", Invitation.expires_at > datetime.now(timezone.utc)))).scalars().first():
        raise ApiError(409, "already_exists", "Приглашение уже отправлено")
    raw = new_token()
    inv = Invitation(org_id=org.id, email=email, role=body.role, group_id=body.group_id, token_hash=token_hash(raw),
                     expires_at=datetime.now(timezone.utc) + timedelta(days=7), invited_by=user.id)
    db.add(inv)
    if existing is not None:  # пользователь уже есть в системе, но без организации: привязываем сразу
        existing.org_id, existing.role = org.id, body.role
        inv.status = "accepted"
        if body.group_id is not None:
            db.add(GroupMember(group_id=body.group_id, user_id=existing.id))
    await db.commit()
    link = f"{c.settings.frontend_url}/register?invitation={raw}"
    await c.mailer.send(email, f"Приглашение в {org.name}",
                        f"Вас пригласили в «{org.name}» на PsyTrainer как {body.role}.\n" +
                        ("Войдите в аккаунт, чтобы начать работу." if existing is not None else f"Зарегистрируйтесь по ссылке: {link}"))
    return inv


@router.patch("/{org_id}/members/{user_id}", response_model=MemberOut,
              dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def patch_member(org_id: uuid.UUID, user_id: uuid.UUID, body: MemberPatch, db: DB, user: CurrentUser):
    await org_for(db, user, org_id, admin=True)
    target = await db.get(User, user_id)
    if target is None or target.org_id != org_id:
        raise access.not_found("Участник")
    if target.role == "org_admin" and body.role != "org_admin" and await _admins_count(db, org_id) <= 1:
        raise ApiError(409, "last_admin", "Нельзя снять роль с последнего администратора организации")
    target.role = body.role
    await db.commit()
    return _member(target)


@router.delete("/{org_id}/members/{user_id}", status_code=204,
               dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def remove_member(org_id: uuid.UUID, user_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    await org_for(db, user, org_id, admin=True)
    target = await db.get(User, user_id)
    if target is None or target.org_id != org_id:
        raise access.not_found("Участник")
    if target.role == "org_admin" and await _admins_count(db, org_id) <= 1:
        raise ApiError(409, "last_admin", "Нельзя исключить последнего администратора организации")
    taught = (await db.execute(select(func.count(StudyGroup.id)).where(StudyGroup.teacher_id == target.id))).scalar_one()
    if taught:
        raise ApiError(409, "teacher_has_groups", "Сначала передайте группы другому преподавателю")
    group_ids = select(StudyGroup.id).where(StudyGroup.org_id == org_id)
    for gm in (await db.execute(select(GroupMember).where(GroupMember.user_id == target.id,
                                                          GroupMember.group_id.in_(group_ids)))).scalars().all():
        await db.delete(gm)
    target.org_id, target.role = None, "student"
    await db.commit()
    return Response(status_code=204)


@router.get("/{org_id}/subscription", response_model=SubscriptionOut,
            dependencies=[Depends(require_roles("org_admin", "platform_admin"))])
async def org_subscription(org_id: uuid.UUID, db: DB, user: CurrentUser):
    await org_for(db, user, org_id, admin=True)
    sub = (await db.execute(select(Subscription).where(Subscription.org_id == org_id).order_by(
        Subscription.created_at.desc()))).scalars().first()
    if sub is None:
        return SubscriptionOut(owner_type="organization", plan="free", status="active")
    return SubscriptionOut(owner_type="organization", plan=sub.plan, status=sub.status, valid_until=sub.valid_until)
