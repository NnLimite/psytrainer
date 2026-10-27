"""Точка входа: create_app() собирает приложение; контейнер зависимостей можно передать снаружи (тесты)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .container import Container
from .db import make_engine, make_session_factory
from .errors import install_error_handlers
from .kv import MemoryKV, RedisKV
from .llm import OllamaClient
from .mail import ConsoleMailer, SmtpMailer
from .payments import StubPayments
from .routers import (admin, analytics, assessment, auth, billing, cases, groups, me, orgs, reference, safety_events,
                      sessions, system)
from .services.seed import ensure_reference_data
from .sso import OidcClient

API_PREFIX = "/api/v1"


def build_container(settings: Settings) -> Container:
    engine = make_engine(settings.database_url)
    llm = OllamaClient(settings.ollama_url, settings.ollama_model, timeout=settings.ollama_timeout,
                       num_ctx=settings.ollama_num_ctx)
    judge = llm if not settings.judge_model else OllamaClient(
        settings.ollama_url, settings.judge_model, timeout=settings.ollama_timeout, num_ctx=settings.ollama_num_ctx)
    mailer = SmtpMailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.smtp_password,
                        settings.mail_from) if settings.smtp_host else ConsoleMailer()
    kv = RedisKV(settings.redis_url) if settings.redis_url else MemoryKV()
    return Container(settings=settings, session_factory=make_session_factory(engine), kv=kv, llm=llm,
                     judge_llm=judge, mailer=mailer, sso=OidcClient(), payments=StubPayments(settings.public_url),
                     engine=engine)


def create_app(container: Container | None = None) -> FastAPI:
    settings = container.settings if container else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or build_container(settings)
        app.state.container = c
        async with c.session_factory() as db:
            await ensure_reference_data(db)
        await c.jobs.recover()

        async def purge_loop() -> None:
            while True:
                try:
                    await c.jobs.purge()
                except Exception:
                    logging.getLogger("psytrainer").exception("job purge failed")
                await asyncio.sleep(6 * 3600)

        purge_task = asyncio.create_task(purge_loop())
        yield
        purge_task.cancel()
        await c.jobs.drain()
        await c.aclose()

    app = FastAPI(title="PsyTrainer API", version="1.0.0", lifespan=lifespan)
    if container is not None:  # тесты запускают приложение без lifespan
        app.state.container = container
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["ETag", "Location", "Retry-After", "RateLimit-Limit", "RateLimit-Remaining",
                                       "RateLimit-Reset", "X-Request-ID", "Idempotent-Replayed"])

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    for r in (system, auth, me, orgs, groups, reference, cases, sessions, assessment, analytics, safety_events,
              billing, admin):
        app.include_router(r.router, prefix=API_PREFIX)
    return app


def app_factory() -> FastAPI:  # uvicorn app.main:app_factory --factory
    logging.basicConfig(level=logging.INFO)
    return create_app()
