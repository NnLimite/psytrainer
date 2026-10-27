"""Модели БД. Соответствуют ERD (организации, кейсы, сессии, оценка) плюс служебные таблицы (токены, задачи, аудит)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Base(DeclarativeBase):
    pass


# ---------- организации и пользователи ----------
class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(200))
    org_type: Mapped[str] = mapped_column(String(30), default="university")
    data_region: Mapped[str] = mapped_column(String(20), default="ru")
    sso_domain: Mapped[str | None] = mapped_column(String(200), nullable=True)  # домен email для автопривязки при SSO
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "user_account"
    id: Mapped[uuid.UUID] = _id()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="student")
    locale: Mapped[str] = mapped_column(String(8), default="ru")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudyGroup(Base):
    __tablename__ = "study_group"
    id: Mapped[uuid.UUID] = _id()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    teacher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    name: Mapped[str] = mapped_column(String(200))


class GroupMember(Base):
    __tablename__ = "group_member"
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("study_group.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    joined_at: Mapped[datetime] = _now()


class Consent(Base):
    __tablename__ = "consent"
    __table_args__ = (UniqueConstraint("user_id", "kind"),)
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    granted: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base):
    __tablename__ = "subscription"
    id: Mapped[uuid.UUID] = _id()
    owner_type: Mapped[str] = mapped_column(String(20))  # user | organization
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization.id"), nullable=True, index=True)
    plan: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="active")
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = _now()


class Invitation(Base):
    __tablename__ = "invitation"
    id: Mapped[uuid.UUID] = _id()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("study_group.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = _now()


class RefreshToken(Base):
    __tablename__ = "refresh_token"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _now()


class OneTimeToken(Base):
    __tablename__ = "one_time_token"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------- кейсы ----------
class DisorderRef(Base):
    __tablename__ = "disorder_ref"
    __table_args__ = (UniqueConstraint("classifier", "code"),)
    id: Mapped[uuid.UUID] = _id()
    classifier: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(300))


class CaseTemplate(Base):
    __tablename__ = "case_template"
    id: Mapped[uuid.UUID] = _id()
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc, onupdate=_utc)  # на стороне Python: без повторного чтения из БД


class CaseVersion(Base):
    __tablename__ = "case_version"
    __table_args__ = (UniqueConstraint("case_id", "version_no"),)
    id: Mapped[uuid.UUID] = _id()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_template.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    case_model: Mapped[dict] = mapped_column(JsonType)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    revision: Mapped[int] = mapped_column(Integer, default=1)  # для ETag
    created_at: Mapped[datetime] = _now()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseDisorder(Base):
    __tablename__ = "case_disorder"
    case_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_version.id"), primary_key=True)
    disorder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disorder_ref.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="primary")


# ---------- сессии ----------
class PatientSession(Base):
    __tablename__ = "patient_session"
    id: Mapped[uuid.UUID] = _id()
    case_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_version.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("study_group.id"), nullable=True, index=True)
    prev_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patient_session.id"), nullable=True)
    session_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    mode: Mapped[str] = mapped_column(String(20), default="training")
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Turn(Base):
    __tablename__ = "turn"
    __table_args__ = (UniqueConstraint("session_id", "turn_no"),)
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_session.id"), index=True)
    turn_no: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class StateSnapshot(Base):
    __tablename__ = "state_snapshot"
    id: Mapped[uuid.UUID] = _id()
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turn.id"), unique=True)
    trust: Mapped[float] = mapped_column(Float)
    anxiety: Mapped[float] = mapped_column(Float)
    resistance: Mapped[float] = mapped_column(Float)
    openness: Mapped[float] = mapped_column(Float)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)


class SafetyEvent(Base):
    __tablename__ = "safety_event"
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_session.id"), index=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turn.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="open")
    handled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()


class StudentFormulation(Base):
    __tablename__ = "student_formulation"
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_session.id"), unique=True)
    content: Mapped[dict] = mapped_column(JsonType)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc, onupdate=_utc)


# ---------- LLM ----------
class PromptTemplate(Base):
    __tablename__ = "prompt_template"
    __table_args__ = (UniqueConstraint("kind", "version"),)
    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class LlmCall(Base):
    __tablename__ = "llm_call"
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patient_session.id"), nullable=True, index=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turn.id"), nullable=True)
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prompt_template.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(20))  # patient | classifier | judge | assist
    model: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = _now()


# ---------- оценка ----------
class Rubric(Base):
    __tablename__ = "rubric"
    __table_args__ = (UniqueConstraint("family_id", "version"),)
    id: Mapped[uuid.UUID] = _id()
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization.id"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _now()


class RubricCriterion(Base):
    __tablename__ = "rubric_criterion"
    id: Mapped[uuid.UUID] = _id()
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubric.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Assessment(Base):
    __tablename__ = "assessment"
    id: Mapped[uuid.UUID] = _id()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient_session.id"), index=True)
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubric.id"))
    assessor_type: Mapped[str] = mapped_column(String(10), default="llm")
    assessor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..100
    summary: Mapped[str] = mapped_column(Text, default="")
    teacher_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class CriterionScore(Base):
    __tablename__ = "criterion_score"
    id: Mapped[uuid.UUID] = _id()
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment.id"), index=True)
    criterion_code: Mapped[str] = mapped_column(String(50))
    score: Mapped[int] = mapped_column(Integer)  # 0..4
    evidence_turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turn.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------- служебные ----------
class Job(Base):
    __tablename__ = "job"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    params: Mapped[dict] = mapped_column(JsonType, default=dict)
    result_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)  # путь API
    result_file: Mapped[str | None] = mapped_column(String(500), nullable=True)  # файл в хранилище
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = _id()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    data: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = _now()


class PaymentEvent(Base):
    __tablename__ = "payment_event"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)
    id: Mapped[uuid.UUID] = _id()
    provider: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    processed_at: Mapped[datetime] = _now()
