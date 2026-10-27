"""Контейнер зависимостей: всё внешнее (БД, Redis, LLM, почта, SSO, платежи) собрано в одном месте и подменяется в тестах."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .jobs import JobRunner
from .kv import KV
from .llm import LLMClient
from .mail import Mailer
from .payments import PaymentProvider
from .sso import OidcClient


@dataclass
class Container:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    kv: KV
    llm: LLMClient
    judge_llm: LLMClient
    mailer: Mailer
    sso: OidcClient
    payments: PaymentProvider
    engine: AsyncEngine | None = None
    jobs: JobRunner = field(init=False)

    def __post_init__(self) -> None:
        self.jobs = JobRunner(self)

    @property
    def storage(self) -> Path:
        p = Path(self.settings.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def aclose(self) -> None:
        for obj in (self.llm, self.judge_llm, self.kv):
            closer: Any = getattr(obj, "aclose", None)
            if closer:
                try:
                    await closer()
                except Exception:
                    pass
        if self.engine is not None:
            await self.engine.dispose()
