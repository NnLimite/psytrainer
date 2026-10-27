from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.container import Container
from app.db import make_engine, make_session_factory
from app.kv import MemoryKV
from app.llm import LLMUsage
from app.mail import ConsoleMailer
from app.main import create_app
from app.models import Base, Organization, User
from app.payments import StubPayments
from app.security import hash_password
from app.services.seed import ensure_reference_data
from app.sso import OidcClient

from argon2 import PasswordHasher

from app import security

# в тестах хеширование паролей дешёвое: иначе каждый логин занимает десятки миллисекунд
security._hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
security._DUMMY = security._hasher.hash("dummy-password-for-timing")

PASSWORD = "correct-horse-battery"
EXAMPLE = json.loads((Path(__file__).resolve().parents[1] / "examples" / "dima.json").read_text(encoding="utf-8"))


class FakeLLM:
    """Подменяет Ollama: распознаёт назначение вызова по системному промпту."""

    model = "fake-qwen"

    def __init__(self) -> None:
        self.features: dict = {}
        self.fail = False
        self.fail_classifier = False
        self.bad_judge = False
        self.judge_score = 3
        self.tokens = ["Ну", " ", "ладно", "..."]
        self.delay = 0.0
        self.patient_prompts: list[str] = []

    def usage(self) -> LLMUsage:
        return LLMUsage(self.model, 10, 5, 7)

    async def chat(self, messages, *, temperature=0.8, json_mode=False):
        if self.fail:
            raise RuntimeError("llm down")
        system = messages[0]["content"]
        if "разметчик" in system or "Разметь" in system:
            if self.fail_classifier:
                return "not json", self.usage()
            return json.dumps(self.features), self.usage()
        if "супервизор" in system:
            if self.bad_judge:
                return "garbage", self.usage()
            codes = re.findall(r"^- (\w+):", system, re.M)
            scores = [{"criterion_code": c, "score": self.judge_score, "evidence_turn_no": 1, "comment": f"ok {c}"} for c in codes]
            return json.dumps({"scores": scores, "summary": "Хорошая работа."}), self.usage()
        if "помощник преподавателя" in system:
            return json.dumps({"suggestions": [{"value": ["идея 1"], "rationale": "потому что"}]}), self.usage()
        return "{}", self.usage()

    async def chat_stream(self, messages, *, temperature=0.8):
        if self.fail:
            raise RuntimeError("llm down")
        self.patient_prompts.append(messages[0]["content"])
        if self.delay:
            import asyncio
            await asyncio.sleep(self.delay)
        for t in self.tokens:
            yield t
        yield self.usage()

    async def ping(self) -> bool:
        return True


class FakeOidc(OidcClient):
    def __init__(self) -> None:
        self.userinfo = {"email": "stud@uni.ru", "email_verified": True, "name": "Студент Вуза"}

    async def fetch_userinfo(self, p, *, code, redirect_uri):
        return self.userinfo


@pytest.fixture
async def container(tmp_path):
    db_url = os.environ.get("PSY_TEST_DATABASE_URL", "sqlite+aiosqlite://")
    settings = Settings(database_url=db_url, redis_url=None, cookie_secure=False,
                        storage_dir=str(tmp_path / "storage"), secret_key="test-secret-test-secret-test-secret",
                        rate_limit_default_per_min=10_000, rate_limit_auth_per_min=1000,
                        rate_limit_messages_per_min=1000)
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # на настоящей БД каждый тест начинается с чистой схемы
        await conn.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    async with sf() as db:
        await ensure_reference_data(db)
    c = Container(settings=settings, session_factory=sf, kv=MemoryKV(), llm=FakeLLM(), judge_llm=None,  # type: ignore[arg-type]
                  mailer=ConsoleMailer(), sso=FakeOidc(), payments=StubPayments("http://test"), engine=engine)
    c.judge_llm = c.llm
    yield c
    await c.jobs.drain()
    await engine.dispose()


@pytest.fixture
def llm(container) -> FakeLLM:
    return container.llm


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
def make_client(app):
    clients: list[AsyncClient] = []

    def factory() -> AsyncClient:
        cl = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(cl)
        return cl

    yield factory


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def api():
    return "/api/v1"


# ---------- помощники для сидирования ----------
async def make_org(container, name="Вуз", sso_domain=None) -> Organization:
    async with container.session_factory() as db:
        org = Organization(id=uuid.uuid4(), name=name, sso_domain=sso_domain)
        db.add(org)
        await db.commit()
        return org


async def make_user(container, role="student", org=None, email=None, name="Пользователь") -> User:
    async with container.session_factory() as db:
        u = User(id=uuid.uuid4(), email=email or f"{role}-{uuid.uuid4().hex[:6]}@example.com", full_name=name, role=role,
                 org_id=org.id if org else None, password_hash=hash_password(PASSWORD))
        db.add(u)
        await db.commit()
        return u


async def login(client: AsyncClient, user: User) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def helpers(container, client):
    class H:
        pass

    h = H()
    h.org = lambda **kw: make_org(container, **kw)
    h.user = lambda **kw: make_user(container, **kw)

    async def auth(user):  # новый клиент без общих cookie
        return await login(client, user)
    h.auth = auth
    return h


def case_body(**over):
    m = json.loads(json.dumps(EXAMPLE))
    m.update(over)
    return {"title": m["title"], "visibility": "org", "case_model": m}
