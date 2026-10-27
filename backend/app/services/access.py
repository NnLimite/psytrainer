"""Проверки доступа к объектам. Чужие объекты отдают 404, а не 403, чтобы не раскрывать их существование."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import CaseTemplate, CaseVersion, GroupMember, PatientSession, StudyGroup, User

STAFF = ("teacher", "org_admin", "platform_admin")


def not_found(what: str = "Объект") -> ApiError:
    return ApiError(404, "not_found", f"{what} не найден")


async def is_group_member(db: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (await db.get(GroupMember, (group_id, user_id))) is not None


async def group_for(db: AsyncSession, user: User, group_id: uuid.UUID, *, manage: bool = False) -> StudyGroup:
    """Группа, к которой у пользователя есть доступ (manage=True: управление, а не просто чтение)."""
    g = await db.get(StudyGroup, group_id)
    if g is None:
        raise not_found("Группа")
    if user.role == "platform_admin":
        return g
    if user.role == "org_admin" and user.org_id == g.org_id:
        return g
    if user.role == "teacher" and g.teacher_id == user.id:
        return g
    if not manage and user.role == "student" and await is_group_member(db, g.id, user.id):
        return g
    raise not_found("Группа")


async def load_case(db: AsyncSession, case_id: uuid.UUID) -> CaseTemplate:
    case = await db.get(CaseTemplate, case_id)
    if case is None:
        raise not_found("Кейс")
    return case


def can_edit_case(user: User, case: CaseTemplate) -> bool:
    if user.role == "platform_admin" or case.author_id == user.id:
        return True
    return user.role == "org_admin" and case.org_id is not None and case.org_id == user.org_id


async def published_version_no(db: AsyncSession, case_id: uuid.UUID) -> int | None:
    return (await db.execute(select(func.max(CaseVersion.version_no)).where(
        CaseVersion.case_id == case_id, CaseVersion.status == "published"))).scalar()


async def can_view_case(db: AsyncSession, user: User, case: CaseTemplate) -> bool:
    if can_edit_case(user, case):
        return True
    if case.archived:
        return False
    org_ok = case.visibility == "public" or (case.visibility == "org" and case.org_id is not None
                                               and case.org_id == user.org_id)
    if not org_ok:
        return False
    if user.role == "student":
        return await published_version_no(db, case.id) is not None
    return True


async def case_for_view(db: AsyncSession, user: User, case_id: uuid.UUID) -> CaseTemplate:
    case = await load_case(db, case_id)
    if not await can_view_case(db, user, case):
        raise not_found("Кейс")
    return case


async def case_for_edit(db: AsyncSession, user: User, case_id: uuid.UUID) -> CaseTemplate:
    case = await load_case(db, case_id)
    if not can_edit_case(user, case):
        # видимый, но не редактируемый кейс: 403, невидимый: 404
        if await can_view_case(db, user, case):
            raise ApiError(403, "forbidden", "Редактировать кейс может только автор или администратор организации")
        raise not_found("Кейс")
    return case


async def load_session(db: AsyncSession, user: User, session_id: uuid.UUID, *, owner_only: bool = False) -> PatientSession:
    s = await db.get(PatientSession, session_id)
    if s is None:
        raise not_found("Сессия")
    if s.user_id == user.id:
        return s
    if owner_only:
        raise not_found("Сессия")
    if user.role == "teacher" and s.group_id is not None:
        g = await db.get(StudyGroup, s.group_id)
        if g is not None and g.teacher_id == user.id:
            return s
    if user.role == "org_admin" and user.org_id is not None:
        owner = await db.get(User, s.user_id)
        if owner is not None and owner.org_id == user.org_id:
            return s
    raise not_found("Сессия")


def state_visible(user: User, session: PatientSession) -> bool:
    """Состояние пациента скрыто от студента в экзаменационном режиме; наблюдателям видно всегда."""
    if session.user_id == user.id:
        return session.mode != "exam"
    return True


async def teacher_scope_user_ids(db: AsyncSession, user: User) -> set[uuid.UUID]:
    """Пользователи, чьи данные может читать преподаватель или администратор организации."""
    if user.role == "teacher":
        rows = await db.execute(select(GroupMember.user_id).join(StudyGroup, StudyGroup.id == GroupMember.group_id)
                                .where(StudyGroup.teacher_id == user.id))
        return set(rows.scalars().all())
    if user.role == "org_admin" and user.org_id is not None:
        rows = await db.execute(select(User.id).where(User.org_id == user.org_id))
        return set(rows.scalars().all())
    return set()
