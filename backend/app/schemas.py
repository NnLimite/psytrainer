"""Схемы запросов и ответов API (соответствуют openapi.yaml)."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .engine.schemas import CaseModel, PatientState

Role = Literal["student", "teacher", "org_admin", "platform_admin"]
Mode = Literal["training", "preview", "exam"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None


# ----- auth / профиль
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    locale: str = "ru"
    invitation_token: str | None = None  # привязка к организации и роли по приглашению


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PasswordForgotIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    org_id: uuid.UUID | None = None
    locale: str
    created_at: datetime


class UserPatch(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    locale: str | None = Field(None, max_length=8)


class ConsentOut(BaseModel):
    kind: Literal["transcript_processing", "analytics", "research_use"]
    granted: bool
    granted_at: datetime | None = None
    revoked_at: datetime | None = None


class ConsentIn(BaseModel):
    granted: bool


class SkillStat(BaseModel):
    code: str
    title: str
    avg_score: float
    delta_30d: float | None = None


class ProgressOut(BaseModel):
    sessions_completed: int
    avg_total_score: float | None = None
    skills: list[SkillStat]


class SubscriptionOut(BaseModel):
    owner_type: Literal["user", "organization"]
    plan: str
    status: Literal["active", "past_due", "canceled", "trial"]
    valid_until: date | None = None


class JobOut(BaseModel):
    id: uuid.UUID
    type: Literal["assessment", "data_export", "erasure", "report"]
    status: Literal["queued", "running", "succeeded", "failed"]
    result_url: str | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


# ----- организации и группы
class OrganizationOut(ORM):
    id: uuid.UUID
    name: str
    org_type: str
    data_region: str
    created_at: datetime


class OrganizationPatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    role: Role
    joined_at: datetime | None = None


class MemberPatch(BaseModel):
    role: Literal["student", "teacher", "org_admin"]


class InvitationIn(BaseModel):
    email: EmailStr
    role: Literal["student", "teacher"]
    group_id: uuid.UUID | None = None


class InvitationOut(ORM):
    id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime


class GroupOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    teacher_id: uuid.UUID
    name: str
    members_count: int = 0


class GroupIn(BaseModel):
    org_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    teacher_id: uuid.UUID | None = None


class GroupPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    teacher_id: uuid.UUID | None = None


class DisorderOut(ORM):
    id: uuid.UUID
    classifier: str
    code: str
    name: str


# ----- кейсы
Visibility = Literal["private", "org", "public"]


class CaseSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    visibility: Visibility
    author_id: uuid.UUID
    org_id: uuid.UUID | None = None
    latest_version_no: int
    published_version_no: int | None = None
    archived: bool
    disorders: list[DisorderOut] = []
    updated_at: datetime | None = None


class VersionInfo(BaseModel):
    version_no: int
    status: Literal["draft", "published"]
    created_at: datetime
    published_at: datetime | None = None


class CaseDetailOut(CaseSummaryOut):
    versions: list[VersionInfo] = []


class CaseVersionOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    version_no: int
    status: Literal["draft", "published"]
    case_model: dict[str, Any]
    language: str
    created_at: datetime
    published_at: datetime | None = None


class CaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    visibility: Visibility = "private"
    case_model: CaseModel


class CasePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    visibility: Visibility | None = None
    archived: bool | None = None


class VersionCreateIn(BaseModel):
    based_on_version_no: int | None = None


class ValidationIssue(BaseModel):
    path: str
    message: str
    code: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]


class SuggestionIn(BaseModel):
    section: Literal["background", "core_beliefs", "rules", "triggers", "defenses", "communication_style",
                     "hidden_disclosure"]
    context: dict[str, Any]
    language: str = "ru"


class SuggestionItem(BaseModel):
    value: Any
    rationale: str | None = None


class SuggestionOut(BaseModel):
    section: str
    suggestions: list[SuggestionItem]


# ----- сессии
class SessionIn(BaseModel):
    case_version_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    prev_session_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    mode: Mode = "training"


class SessionOut(BaseModel):
    id: uuid.UUID
    case_version_id: uuid.UUID
    user_id: uuid.UUID
    group_id: uuid.UUID | None = None
    prev_session_id: uuid.UUID | None = None
    session_no: int
    mode: Mode
    status: Literal["active", "ended"]
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    state: PatientState | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: uuid.UUID | None = None


class MessageOut(BaseModel):
    type: Literal["done"] = "done"
    reply: str
    state: PatientState | None = None
    delta: dict[str, float] | None = None
    triggers: list[str] | None = None
    features_source: Literal["llm", "heuristic"] | None = None
    notice: dict[str, str] | None = None  # например: подсказка студенту при признаках кризиса


class TurnOut(BaseModel):
    id: uuid.UUID
    turn_no: int
    speaker: Literal["student", "patient"]
    content: str
    flagged: bool = False
    created_at: datetime


class StateOut(BaseModel):
    state: PatientState
    disclosed: bool
    turn_no: int


class SnapshotOut(BaseModel):
    turn_no: int
    state: PatientState
    delta: dict[str, Any] = {}
    triggers: list[str] = []
    features: dict[str, Any] = {}
    features_source: str | None = None
    disclosed: bool = False


class FormulationIn(BaseModel):
    content: dict[str, Any]


class FormulationOut(BaseModel):
    content: dict[str, Any]
    match_score: float | None = None
    submitted_at: datetime


class SafetyEventOut(ORM):
    id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID | None = None
    event_type: str
    severity: str
    status: str
    handled_by: uuid.UUID | None = None
    note: str | None = None
    created_at: datetime


class SafetyEventPatch(BaseModel):
    status: Literal["acknowledged", "resolved"]
    note: str | None = Field(None, max_length=2000)


# ----- оценка
class CriterionIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    weight: float = Field(1.0, gt=0, le=100)


class RubricIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    criteria: list[CriterionIn] = Field(min_length=1, max_length=30)


class CriterionOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str
    weight: float


class RubricOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID | None = None
    name: str
    version: int
    criteria: list[CriterionOut]


class AssessmentIn(BaseModel):
    rubric_id: uuid.UUID
    assessor: Literal["llm"] = "llm"
    model: str | None = None


class CriterionScoreOut(BaseModel):
    criterion_code: str
    score: int = Field(ge=0, le=4)
    evidence_turn_id: uuid.UUID | None = None
    comment: str | None = None


class AssessmentOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    rubric_id: uuid.UUID
    assessor_type: Literal["llm", "teacher"]
    assessor_id: uuid.UUID | None = None
    total_score: float
    summary: str
    teacher_confirmed: bool
    scores: list[CriterionScoreOut]
    created_at: datetime


class AssessmentPatch(BaseModel):
    summary: str | None = None
    scores: list[CriterionScoreOut] | None = None


# ----- аналитика, биллинг, админка
class GroupSummaryOut(BaseModel):
    group_id: uuid.UUID
    sessions_count: int
    active_students: int = 0
    avg_total_score: float | None = None
    open_safety_events: int = 0
    skills: list[dict[str, Any]] = []


class CaseUsageOut(BaseModel):
    case_id: uuid.UUID
    sessions_count: int
    avg_duration_min: float = 0.0
    avg_final_trust: float = 0.0
    disclosure_rate: float = 0.0


class ReportIn(BaseModel):
    type: Literal["group_summary", "student_progress", "case_usage"]
    params: dict[str, Any]
    format: Literal["pdf", "xlsx", "csv"]


class CheckoutIn(BaseModel):
    plan: str
    success_url: str
    cancel_url: str


class CheckoutOut(BaseModel):
    checkout_url: str


class PromptTemplateIn(BaseModel):
    kind: Literal["patient", "classifier", "judge"]
    body: str = Field(min_length=10, max_length=20000)


class PromptTemplateOut(ORM):
    id: uuid.UUID
    kind: str
    version: int
    body: str
    is_active: bool
    created_at: datetime | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checks: dict[str, str] = {}
