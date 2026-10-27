from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..models import Job, User
from ..schemas import JobOut


async def enqueue(c: Container, db: AsyncSession, user: User, job_type: str, params: dict[str, Any]) -> Job:
    job = Job(user_id=user.id, type=job_type, params=params)
    db.add(job)
    await db.commit()
    c.jobs.submit(job.id)
    return job


def job_out(job: Job) -> JobOut:
    return JobOut(id=job.id, type=job.type, status=job.status, result_url=job.result_ref if job.status == "succeeded" else None,
                  error=job.error, created_at=job.created_at, finished_at=job.finished_at)
