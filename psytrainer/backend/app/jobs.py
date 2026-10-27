"""Фоновые задачи. Сейчас выполняются в процессе API (asyncio); интерфейс submit() рассчитан на замену очередью (ARQ/Celery)."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import delete, select

from .models import Job

if TYPE_CHECKING:
    from .container import Container

log = logging.getLogger("psytrainer.jobs")


@dataclass
class JobResult:
    ref: str | None = None  # путь API с результатом, например /api/v1/assessments/{id}
    file: str | None = None  # файл в хранилище (для отчётов и выгрузок)


class JobRunner:
    def __init__(self, container: "Container") -> None:
        self.c = container
        self._tasks: set[asyncio.Task] = set()

    def submit(self, job_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._run(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: uuid.UUID) -> None:
        from .services.job_handlers import HANDLERS

        async with self.c.session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            await db.commit()
            try:
                result = await HANDLERS[job.type](self.c, db, job)
                job = await db.get(Job, job_id, populate_existing=True)
                job.status, job.result_ref, job.result_file = "succeeded", result.ref, result.file
            except Exception as exc:
                log.exception("job %s failed", job_id)
                await db.rollback()
                job = await db.get(Job, job_id, populate_existing=True)
                job.status, job.error = "failed", str(exc)[:500]
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()

    async def drain(self) -> None:
        """Ждёт завершения всех задач (для тестов и корректной остановки)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def recover(self) -> None:
        """После перезапуска помечает зависшие задачи как неудачные: очередь в памяти не переживает рестарт."""
        async with self.c.session_factory() as db:
            rows = (await db.execute(select(Job).where(Job.status.in_(["queued", "running"])))).scalars().all()
            for j in rows:
                j.status, j.error = "failed", "interrupted by restart"
                j.finished_at = datetime.now(timezone.utc)
            await db.commit()

    async def purge(self, days: int = 7) -> int:
        """Удаляет завершённые задачи старше `days` суток вместе с файлами результата (отчёты, выгрузки)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with self.c.session_factory() as db:
            old = (await db.execute(select(Job).where(Job.finished_at.is_not(None), Job.finished_at < cutoff))).scalars().all()
            for j in old:
                if j.result_file:
                    try:
                        os.remove(j.result_file)
                    except OSError:
                        pass
            if old:
                await db.execute(delete(Job).where(Job.id.in_([j.id for j in old])))
                await db.commit()
            return len(old)
