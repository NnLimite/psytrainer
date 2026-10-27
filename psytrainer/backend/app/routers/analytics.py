from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ..deps import C, DB, CurrentUser, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..schemas import CaseUsageOut, GroupSummaryOut, JobOut, ProgressOut, ReportIn
from ..services import access, analytics
from ..services.jobs_api import enqueue, job_out

router = APIRouter(tags=["Analytics"], dependencies=[Depends(current_user), default_rate_limit,
                                                      Depends(require_roles("teacher", "org_admin"))])


@router.get("/analytics/groups/{group_id}/summary", response_model=GroupSummaryOut)
async def group_summary(group_id: uuid.UUID, db: DB, user: CurrentUser,
                        from_: Annotated[date | None, Query(alias="from")] = None, to: date | None = None):
    await access.group_for(db, user, group_id, manage=True)
    return await analytics.group_summary(db, group_id, from_, to)


@router.get("/analytics/students/{user_id}/progress", response_model=ProgressOut)
async def student_progress(user_id: uuid.UUID, db: DB, user: CurrentUser):
    if user_id not in await access.teacher_scope_user_ids(db, user):
        raise access.not_found("Студент")
    return await analytics.student_progress(db, user_id)


@router.get("/analytics/cases/{case_id}/usage", response_model=CaseUsageOut)
async def case_usage(case_id: uuid.UUID, db: DB, user: CurrentUser):
    case = await access.case_for_edit(db, user, case_id)
    return await analytics.case_usage(db, case.id)


@router.post("/reports", response_model=JobOut, status_code=202)
async def create_report(body: ReportIn, response: Response, c: C, db: DB, user: CurrentUser):
    if body.format == "pdf":
        raise ApiError(422, "format_not_supported", "PDF пока не поддерживается: выберите xlsx или csv")
    key = {"group_summary": "group_id", "student_progress": "user_id", "case_usage": "case_id"}[body.type]
    try:
        target = uuid.UUID(str(body.params.get(key)))
    except ValueError:
        raise ApiError(422, "validation_failed", f"В params нужен {key}")
    for k in ("from", "to"):
        if body.params.get(k):
            try:
                date.fromisoformat(str(body.params[k]))
            except ValueError:
                raise ApiError(422, "validation_failed", f"params.{k} должен быть датой YYYY-MM-DD")
    if body.type == "group_summary":
        await access.group_for(db, user, target, manage=True)
    elif body.type == "student_progress":
        if target not in await access.teacher_scope_user_ids(db, user):
            raise access.not_found("Студент")
    else:
        await access.case_for_edit(db, user, target)
    job = await enqueue(c, db, user, "report", {"type": body.type, "format": body.format,
                                                "params": {**body.params, key: str(target)}})
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_out(job)
