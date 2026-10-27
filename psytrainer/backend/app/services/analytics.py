"""Аналитика: прогресс студента, сводка по группе, использование кейса. Берутся только завершённые не-preview сессии;
в групповую и кейсовую аналитику попадают лишь сессии пользователей с согласием analytics."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Assessment, CaseVersion, Consent, CriterionScore, PatientSession, RubricCriterion, SafetyEvent, StateSnapshot, Turn,
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


async def consented_users(db: AsyncSession) -> set[uuid.UUID]:
    rows = await db.execute(select(Consent.user_id).where(Consent.kind == "analytics", Consent.granted.is_(True)))
    return set(rows.scalars().all())


async def _latest_assessments(db: AsyncSession, session_ids: list[uuid.UUID]) -> list[Assessment]:
    if not session_ids:
        return []
    rows = (await db.execute(select(Assessment).where(Assessment.session_id.in_(session_ids)).order_by(
        Assessment.created_at))).scalars().all()
    latest: dict[uuid.UUID, Assessment] = {}
    for a in rows:  # последняя по времени выигрывает
        latest[a.session_id] = a
    return list(latest.values())


async def skill_summary(db: AsyncSession, session_ids: list[uuid.UUID], *, with_delta: bool = False,
                        now: datetime | None = None) -> tuple[float | None, list[dict[str, Any]]]:
    assessments = await _latest_assessments(db, session_ids)
    if not assessments:
        return None, []
    now = now or datetime.now(timezone.utc)
    by_id = {a.id: a for a in assessments}
    scores = (await db.execute(select(CriterionScore).where(CriterionScore.assessment_id.in_(list(by_id))))).scalars().all()
    titles: dict[str, str] = {}
    rubric_ids = {a.rubric_id for a in assessments}
    for rc in (await db.execute(select(RubricCriterion).where(RubricCriterion.rubric_id.in_(rubric_ids)))).scalars().all():
        titles.setdefault(rc.code, rc.title)
    per_code: dict[str, list[tuple[float, datetime]]] = {}
    for s in scores:
        per_code.setdefault(s.criterion_code, []).append((s.score, _aware(by_id[s.assessment_id].created_at)))
    skills = []
    for code, vals in sorted(per_code.items()):
        item: dict[str, Any] = {"code": code, "title": titles.get(code, code), "avg_score": _avg([v for v, _ in vals])}
        if with_delta:
            recent = [v for v, t in vals if t >= now - timedelta(days=30)]
            prior = [v for v, t in vals if now - timedelta(days=60) <= t < now - timedelta(days=30)]
            item["delta_30d"] = round(sum(recent) / len(recent) - sum(prior) / len(prior), 2) if recent and prior else None
        skills.append(item)
    return _avg([a.total_score for a in assessments]), skills


async def student_progress(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    sessions = (await db.execute(select(PatientSession).where(
        PatientSession.user_id == user_id, PatientSession.status == "ended", PatientSession.mode != "preview"))).scalars().all()
    avg_total, skills = await skill_summary(db, [s.id for s in sessions], with_delta=True)
    return {"sessions_completed": len(sessions), "avg_total_score": avg_total, "skills": skills}


async def group_summary(db: AsyncSession, group_id: uuid.UUID, start: date | None, end: date | None) -> dict[str, Any]:
    stmt = select(PatientSession).where(PatientSession.group_id == group_id, PatientSession.mode != "preview")
    all_sessions = (await db.execute(stmt)).scalars().all()
    ok_users = await consented_users(db)

    def in_period(s: PatientSession) -> bool:
        d = _aware(s.started_at).date()
        return (start is None or d >= start) and (end is None or d <= end)

    scope = [s for s in all_sessions if s.user_id in ok_users and in_period(s)]
    avg_total, skills = await skill_summary(db, [s.id for s in scope if s.status == "ended"])
    open_events = 0
    if all_sessions:
        open_events = len((await db.execute(select(SafetyEvent.id).where(
            SafetyEvent.session_id.in_([s.id for s in all_sessions]), SafetyEvent.status == "open"))).scalars().all())
    return {"group_id": group_id, "sessions_count": len(scope), "active_students": len({s.user_id for s in scope}),
            "avg_total_score": avg_total, "open_safety_events": open_events,
            "skills": [{k: v for k, v in sk.items() if k != "delta_30d"} for sk in skills]}


async def case_usage(db: AsyncSession, case_id: uuid.UUID) -> dict[str, Any]:
    version_ids = (await db.execute(select(CaseVersion.id).where(CaseVersion.case_id == case_id))).scalars().all()
    ok_users = await consented_users(db)
    sessions = [s for s in (await db.execute(select(PatientSession).where(
        PatientSession.case_version_id.in_(version_ids), PatientSession.mode != "preview"))).scalars().all()
        if s.user_id in ok_users] if version_ids else []
    durations = [(_aware(s.ended_at) - _aware(s.started_at)).total_seconds() / 60 for s in sessions if s.ended_at]
    finals: list[float] = []
    disclosed: list[bool] = []
    for s in sessions:
        row = (await db.execute(select(StateSnapshot).join(Turn, Turn.id == StateSnapshot.turn_id).where(
            Turn.session_id == s.id).order_by(Turn.turn_no.desc()).limit(1))).scalar_one_or_none()
        if row is not None:
            finals.append(row.trust)
            disclosed.append(bool(row.extra.get("disclosed", False)))
    return {"case_id": case_id, "sessions_count": len(sessions),
            "avg_duration_min": _avg(durations) or 0.0, "avg_final_trust": _avg(finals) or 0.0,
            "disclosure_rate": round(sum(disclosed) / len(disclosed), 3) if disclosed else 0.0}
