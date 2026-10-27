from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from ..deps import C, DB, CurrentUser, Idem, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import (
    Assessment, AuditLog, CriterionScore, Job, PatientSession, Rubric, RubricCriterion, User,
)
from ..pagination import paginate
from ..schemas import (
    AssessmentIn, AssessmentOut, AssessmentPatch, CriterionOut, CriterionScoreOut, JobOut, Page, RubricIn, RubricOut,
)
from ..services import access
from ..services.assessment import total_score
from ..services.jobs_api import enqueue, job_out

router = APIRouter(tags=["Assessment"], dependencies=[Depends(current_user), default_rate_limit])
STAFF = Depends(require_roles("teacher", "org_admin"))


# ---------- рубрики ----------
async def _rubric_out(db, r: Rubric) -> RubricOut:
    crit = (await db.execute(select(RubricCriterion).where(RubricCriterion.rubric_id == r.id).order_by(
        RubricCriterion.position))).scalars().all()
    return RubricOut(id=r.id, org_id=r.org_id, name=r.name, version=r.version, criteria=[
        CriterionOut(id=c.id, code=c.code, title=c.title, description=c.description, weight=c.weight) for c in crit])


def _rubric_visible(user: User, r: Rubric) -> bool:
    return user.role == "platform_admin" or r.org_id is None or r.org_id == user.org_id


async def rubric_for_view(db, user: User, rubric_id: uuid.UUID) -> Rubric:
    r = await db.get(Rubric, rubric_id)
    if r is None or not _rubric_visible(user, r):
        raise access.not_found("Рубрика")
    return r


async def _create_rubric(db, user: User, body: RubricIn, family_id: uuid.UUID, version: int, org_id) -> Rubric:
    codes = [c.code for c in body.criteria]
    if len(set(codes)) != len(codes):
        raise ApiError(422, "validation_failed", "Коды критериев должны быть уникальны")
    r = Rubric(id=uuid.uuid4(), family_id=family_id, org_id=org_id, created_by=user.id, name=body.name, version=version)
    db.add(r)
    await db.flush()
    for i, c in enumerate(body.criteria):
        db.add(RubricCriterion(rubric_id=r.id, code=c.code, title=c.title, description=c.description, weight=c.weight,
                               position=i))
    await db.commit()
    return r


@router.get("/rubrics", response_model=Page)
async def list_rubrics(db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                       cursor: str | None = None):
    newest = select(Rubric.family_id, func.max(Rubric.version).label("v")).group_by(Rubric.family_id).subquery()
    stmt = select(Rubric).join(newest, (Rubric.family_id == newest.c.family_id) & (Rubric.version == newest.c.v)).order_by(
        Rubric.name, Rubric.id)
    if user.role != "platform_admin":
        stmt = stmt.where(or_(Rubric.org_id.is_(None), Rubric.org_id == user.org_id))
    page = await paginate(db, stmt, limit, cursor)
    page["items"] = [await _rubric_out(db, r) for r in page["items"]]
    return page


@router.post("/rubrics", response_model=RubricOut, status_code=201, dependencies=[STAFF])
async def create_rubric(body: RubricIn, db: DB, user: CurrentUser):
    if user.org_id is None:
        raise ApiError(422, "validation_failed", "Рубрику может создать только пользователь организации")
    r = await _create_rubric(db, user, body, uuid.uuid4(), 1, user.org_id)
    return await _rubric_out(db, r)


@router.get("/rubrics/{rubric_id}", response_model=RubricOut)
async def get_rubric(rubric_id: uuid.UUID, db: DB, user: CurrentUser):
    return await _rubric_out(db, await rubric_for_view(db, user, rubric_id))


@router.post("/rubrics/{rubric_id}/versions", response_model=RubricOut, status_code=201, dependencies=[STAFF])
async def new_rubric_version(rubric_id: uuid.UUID, body: RubricIn, db: DB, user: CurrentUser):
    r = await rubric_for_view(db, user, rubric_id)
    if r.org_id is None or not (r.created_by == user.id or (user.role == "org_admin" and r.org_id == user.org_id)):
        raise ApiError(403, "forbidden", "Менять рубрику может автор или администратор организации")
    last = (await db.execute(select(func.max(Rubric.version)).where(Rubric.family_id == r.family_id))).scalar_one()
    new = await _create_rubric(db, user, body, r.family_id, last + 1, r.org_id)
    return await _rubric_out(db, new)


# ---------- оценки ----------
async def _assessment_out(db, a: Assessment) -> AssessmentOut:
    scores = (await db.execute(select(CriterionScore).where(CriterionScore.assessment_id == a.id).order_by(
        CriterionScore.criterion_code))).scalars().all()
    return AssessmentOut(id=a.id, session_id=a.session_id, rubric_id=a.rubric_id, assessor_type=a.assessor_type,
                         assessor_id=a.assessor_id, total_score=a.total_score, summary=a.summary,
                         teacher_confirmed=a.teacher_confirmed, created_at=a.created_at, scores=[
                             CriterionScoreOut(criterion_code=s.criterion_code, score=s.score,
                                               evidence_turn_id=s.evidence_turn_id, comment=s.comment) for s in scores])


def _hidden_from(user: User, s: PatientSession, a: Assessment) -> bool:
    """В экзамене студент видит оценку только после подтверждения преподавателем."""
    return s.user_id == user.id and s.mode == "exam" and not a.teacher_confirmed


async def _load_assessment(db, user: User, assessment_id: uuid.UUID) -> tuple[Assessment, PatientSession]:
    a = await db.get(Assessment, assessment_id)
    if a is None:
        raise access.not_found("Оценка")
    s = await access.load_session(db, user, a.session_id)
    if _hidden_from(user, s, a):
        raise access.not_found("Оценка")
    return a, s


@router.post("/sessions/{session_id}/assessments", response_model=JobOut, status_code=202,
             dependencies=[Depends(require_roles("student", "teacher"))])
async def start_assessment(session_id: uuid.UUID, body: AssessmentIn, response: Response, c: C, db: DB,
                           user: CurrentUser, idem: Idem):
    if (replay := await idem.replay()) is not None:
        return replay
    s = await access.load_session(db, user, session_id)
    if user.role == "student" and s.mode != "training":
        raise ApiError(403, "forbidden", "В режиме exam оценку запускает преподаватель")
    if s.status != "ended":
        raise ApiError(409, "session_not_ended", "Оценить можно только завершённую сессию")
    rubric = await rubric_for_view(db, user, body.rubric_id)
    job = await enqueue(c, db, user, "assessment", {"session_id": str(s.id), "rubric_id": str(rubric.id)})
    out = job_out(job)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    await idem.store(202, jsonable_encoder(out))
    return out


@router.get("/sessions/{session_id}/assessments", response_model=list[AssessmentOut],
            dependencies=[Depends(require_roles("student", "teacher"))])
async def list_assessments(session_id: uuid.UUID, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    rows = (await db.execute(select(Assessment).where(Assessment.session_id == s.id).order_by(Assessment.created_at))).scalars().all()
    return [await _assessment_out(db, a) for a in rows if not _hidden_from(user, s, a)]


@router.get("/assessments/{assessment_id}", response_model=AssessmentOut, dependencies=[Depends(require_roles("student", "teacher"))])
async def get_assessment(assessment_id: uuid.UUID, db: DB, user: CurrentUser):
    a, _ = await _load_assessment(db, user, assessment_id)
    return await _assessment_out(db, a)


@router.patch("/assessments/{assessment_id}", response_model=AssessmentOut, dependencies=[Depends(require_roles("teacher"))])
async def patch_assessment(assessment_id: uuid.UUID, body: AssessmentPatch, db: DB, user: CurrentUser):
    a, _ = await _load_assessment(db, user, assessment_id)
    criteria = {c.code: c for c in (await db.execute(select(RubricCriterion).where(
        RubricCriterion.rubric_id == a.rubric_id))).scalars().all()}
    before = (await _assessment_out(db, a)).model_dump(mode="json")
    if body.summary is not None:
        a.summary = body.summary
    if body.scores is not None:
        existing = {s.criterion_code: s for s in (await db.execute(select(CriterionScore).where(
            CriterionScore.assessment_id == a.id))).scalars().all()}
        for sc in body.scores:
            if sc.criterion_code not in criteria:
                raise ApiError(422, "validation_failed", f"Критерия {sc.criterion_code} нет в рубрике")
            row = existing.get(sc.criterion_code)
            if row is None:
                row = CriterionScore(assessment_id=a.id, criterion_code=sc.criterion_code, score=sc.score)
                db.add(row)
                existing[sc.criterion_code] = row
            row.score, row.comment = sc.score, sc.comment
            if sc.evidence_turn_id is not None:
                row.evidence_turn_id = sc.evidence_turn_id
        a.total_score = total_score([(s.score, criteria[code].weight) for code, s in existing.items() if code in criteria])
    a.assessor_type, a.assessor_id, a.teacher_confirmed = "teacher", user.id, False  # после правки нужно подтвердить заново
    db.add(AuditLog(actor_id=user.id, action="assessment.patch", entity="assessment", entity_id=a.id, data={"before": before}))
    await db.commit()
    return await _assessment_out(db, a)


@router.post("/assessments/{assessment_id}/confirm", response_model=AssessmentOut, dependencies=[Depends(require_roles("teacher"))])
async def confirm_assessment(assessment_id: uuid.UUID, db: DB, user: CurrentUser):
    a, _ = await _load_assessment(db, user, assessment_id)
    a.teacher_confirmed = True
    db.add(AuditLog(actor_id=user.id, action="assessment.confirm", entity="assessment", entity_id=a.id, data={}))
    await db.commit()
    return await _assessment_out(db, a)


# ---------- задачи ----------
async def _my_job(db, user: User, job_id: uuid.UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise access.not_found("Задача")
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, response: Response, db: DB, user: CurrentUser):
    job = await _my_job(db, user, job_id)
    if job.status in ("queued", "running"):
        response.headers["Retry-After"] = "2"
    return job_out(job)


_MEDIA = {".csv": "text/csv", ".zip": "application/zip",
          ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: uuid.UUID, db: DB, user: CurrentUser):
    job = await _my_job(db, user, job_id)
    path = Path(job.result_file) if job.result_file else None
    if job.status != "succeeded" or path is None or not path.exists():
        raise access.not_found("Файл")
    return FileResponse(path, media_type=_MEDIA.get(path.suffix, "application/octet-stream"), filename=path.name)
