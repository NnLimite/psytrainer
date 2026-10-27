from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from ..deps import C, DB, CurrentUser, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import Consent, Subscription
from ..schemas import ConsentIn, ConsentOut, JobOut, ProgressOut, SubscriptionOut, UserOut, UserPatch
from ..services import analytics
from ..services.jobs_api import enqueue, job_out

router = APIRouter(prefix="/me", tags=["Profile"], dependencies=[Depends(current_user), default_rate_limit])
KINDS = ("transcript_processing", "analytics", "research_use")


@router.get("", response_model=UserOut)
async def get_me(user: CurrentUser):
    return user


@router.patch("", response_model=UserOut)
async def patch_me(body: UserPatch, db: DB, user: CurrentUser):
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(user, k, v)
    await db.commit()
    return user


@router.get("/consents", response_model=list[ConsentOut])
async def list_consents(db: DB, user: CurrentUser):
    rows = {x.kind: x for x in (await db.execute(select(Consent).where(Consent.user_id == user.id))).scalars().all()}
    return [ConsentOut(kind=k, granted=rows[k].granted if k in rows else False,
                       granted_at=rows[k].granted_at if k in rows else None,
                       revoked_at=rows[k].revoked_at if k in rows else None) for k in KINDS]


@router.put("/consents/{kind}", response_model=ConsentOut)
async def put_consent(kind: str, body: ConsentIn, db: DB, user: CurrentUser):
    if kind not in KINDS:
        raise ApiError(404, "not_found", "Неизвестный вид согласия")
    row = (await db.execute(select(Consent).where(Consent.user_id == user.id, Consent.kind == kind))).scalar_one_or_none()
    if row is None:
        row = Consent(user_id=user.id, kind=kind, granted=False)
        db.add(row)
    now = datetime.now(timezone.utc)
    if body.granted and not row.granted:
        row.granted, row.granted_at, row.revoked_at = True, now, None
    elif not body.granted and row.granted:
        row.granted, row.revoked_at = False, now
    await db.commit()
    return ConsentOut(kind=kind, granted=row.granted, granted_at=row.granted_at, revoked_at=row.revoked_at)


@router.get("/progress", response_model=ProgressOut, dependencies=[Depends(require_roles("student"))])
async def my_progress(db: DB, user: CurrentUser):
    return await analytics.student_progress(db, user.id)


@router.get("/subscription", response_model=SubscriptionOut)
async def my_subscription(db: DB, user: CurrentUser):
    today = date.today()
    stmt = select(Subscription).where(Subscription.status.in_(["active", "trial"])).order_by(Subscription.created_at.desc())
    own = (await db.execute(stmt.where(Subscription.user_id == user.id))).scalars().first()
    org = (await db.execute(stmt.where(Subscription.org_id == user.org_id))).scalars().first() if user.org_id else None
    for sub in (own, org):
        if sub is not None and (sub.valid_until is None or sub.valid_until >= today):
            return SubscriptionOut(owner_type=sub.owner_type, plan=sub.plan, status=sub.status, valid_until=sub.valid_until)
    return SubscriptionOut(owner_type="user", plan="free", status="active")


@router.post("/data-export", response_model=JobOut, status_code=202)
async def data_export(response: Response, c: C, db: DB, user: CurrentUser):
    job = await enqueue(c, db, user, "data_export", {})
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_out(job)


@router.delete("", response_model=JobOut, status_code=202)
async def delete_me(response: Response, c: C, db: DB, user: CurrentUser):
    job = await enqueue(c, db, user, "erasure", {})
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_out(job)
