"""Единый источник правды для API PsyTrainer: эндпоинты и схемы -> openapi.yaml + таблицы для документации."""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict

import yaml

# ---------- помощники для схем ----------
UUID = {"type": "string", "format": "uuid"}
DT = {"type": "string", "format": "date-time"}
STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
UNIT = {"type": "number", "minimum": 0, "maximum": 1}


def ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def enum(*vals: str) -> dict:
    return {"type": "string", "enum": list(vals)}


def nullable(s: dict) -> dict:
    return {"oneOf": [s, {"type": "null"}]}


def obj(props: dict, required: list[str] | None = None, desc: str | None = None) -> dict:
    o: dict = {"type": "object", "properties": props}
    if required:
        o["required"] = required
    if desc:
        o["description"] = desc
    return o


def S(**kw) -> dict:  # строка с ограничениями
    return {"type": "string", **kw}


SCHEMAS: dict[str, dict] = {}


def schema(name: str, s: dict) -> None:
    SCHEMAS[name] = s


# --- общие
schema("Problem", obj({
    "type": {"type": "string", "format": "uri-reference"},
    "title": STR, "status": INT, "detail": STR,
    "code": S(description=("Машиночитаемый код ошибки. 400 bad_request; 401 unauthorized, token_expired; 403 forbidden, "
                           "state_hidden; 404 not_found; 409 consent_required, session_not_active, session_not_ended, "
                           "session_busy, version_immutable, version_not_published, case_has_sessions, group_has_sessions, "
                           "previous_session_mismatch, duplicate_message, already_exists, last_admin, teacher_has_groups; "
                           "412 precondition_failed; 422 validation_failed, format_not_supported; 429 rate_limited; "
                           "502 llm_unavailable, sso_failed; 503 not_ready")),
    "errors": arr(obj({"field": STR, "message": STR})),
}, ["type", "title", "status", "code"]))
schema("PatientState", obj({"trust": UNIT, "anxiety": UNIT, "resistance": UNIT, "openness": UNIT},
                           ["trust", "anxiety", "resistance", "openness"]))
schema("Job", obj({
    "id": UUID, "type": enum("assessment", "data_export", "erasure", "report"),
    "status": enum("queued", "running", "succeeded", "failed"),
    "result_url": nullable(S(format="uri")), "error": nullable(STR),
    "created_at": DT, "finished_at": nullable(DT),
}, ["id", "type", "status", "created_at"]))

# --- аутентификация и профиль
schema("User", obj({
    "id": UUID, "email": S(format="email"), "full_name": STR,
    "role": enum("student", "teacher", "org_admin", "platform_admin"),
    "org_id": nullable(UUID), "locale": S(examples=["ru"]), "created_at": DT,
}, ["id", "email", "role", "created_at"]))
schema("TokenPair", obj({"access_token": STR, "token_type": enum("Bearer"), "expires_in": INT,
                         }, ["access_token", "token_type", "expires_in"],
                        "Refresh-токен выдаётся отдельно: httpOnly-cookie psy_refresh, ротируется при каждом /auth/refresh."))
schema("RegisterIn", obj({"email": S(format="email"), "password": S(minLength=10), "full_name": STR, "locale": STR,
                          "invitation_token": nullable(S(description="Токен из письма-приглашения: привязывает к организации и роли"))},
                         ["email", "password", "full_name"]))
schema("LoginIn", obj({"email": S(format="email"), "password": STR}, ["email", "password"]))
schema("PasswordForgotIn", obj({"email": S(format="email")}, ["email"]))
schema("PasswordResetIn", obj({"token": STR, "new_password": S(minLength=10)}, ["token", "new_password"]))
schema("UserPatch", obj({"full_name": STR, "locale": STR}))
schema("Consent", obj({
    "kind": enum("transcript_processing", "analytics", "research_use"),
    "granted": BOOL, "granted_at": nullable(DT), "revoked_at": nullable(DT),
}, ["kind", "granted"]))
schema("ConsentIn", obj({"granted": BOOL}, ["granted"]))
schema("Progress", obj({
    "sessions_completed": INT, "avg_total_score": nullable(NUM),
    "skills": arr(obj({"code": STR, "title": STR, "avg_score": NUM, "delta_30d": nullable(NUM)})),
}, ["sessions_completed", "skills"]))
schema("Subscription", obj({
    "owner_type": enum("user", "organization"), "plan": STR,
    "status": enum("active", "past_due", "canceled", "trial"), "valid_until": nullable(S(format="date")),
}, ["owner_type", "plan", "status"]))

# --- организации и группы
schema("Organization", obj({
    "id": UUID, "name": STR, "org_type": enum("university", "clinic", "training_center"),
    "data_region": STR, "created_at": DT,
}, ["id", "name", "org_type"]))
schema("OrganizationPatch", obj({"name": STR}))
schema("Member", obj({"user_id": UUID, "full_name": STR, "email": S(format="email"),
                      "role": enum("student", "teacher", "org_admin"), "joined_at": DT},
                     ["user_id", "role"]))
schema("MemberPatch", obj({"role": enum("student", "teacher", "org_admin")}, ["role"]))
schema("InvitationIn", obj({"email": S(format="email"), "role": enum("student", "teacher"),
                            "group_id": nullable(UUID)}, ["email", "role"]))
schema("Invitation", obj({"id": UUID, "email": S(format="email"), "role": STR,
                          "status": enum("pending", "accepted", "expired"), "expires_at": DT},
                         ["id", "email", "role", "status"]))
schema("Group", obj({"id": UUID, "org_id": UUID, "teacher_id": UUID, "name": STR, "members_count": INT},
                    ["id", "org_id", "teacher_id", "name"]))
schema("GroupIn", obj({"org_id": UUID, "teacher_id": UUID, "name": STR}, ["org_id", "name"]))
schema("GroupPatch", obj({"name": STR, "teacher_id": UUID}))
schema("Disorder", obj({"id": UUID, "classifier": enum("ICD-11", "DSM-5"), "code": STR, "name": STR},
                       ["id", "classifier", "code", "name"]))

# --- кейсы
schema("Trigger", obj({
    "id": STR, "keywords": arr(STR), "situation": STR, "automatic_thought": STR,
    "emotion": STR, "behavior": STR, "intensity": UNIT,
}, ["id", "keywords", "situation", "automatic_thought", "emotion", "behavior"]))
schema("CaseModel", obj({
    "title": STR, "name": STR, "age": INT, "gender": STR, "occupation": STR, "language": STR,
    "presenting_problem": STR, "background": arr(STR), "core_beliefs": arr(STR), "rules": arr(STR),
    "triggers": arr(ref("Trigger")), "defenses": arr(STR),
    "communication_style": obj({"verbosity": enum("terse", "moderate", "talkative"), "tone": STR, "quirks": arr(STR)}),
    "goals": arr(STR), "strengths": arr(STR),
    "disorders": arr(obj({"classifier": enum("ICD-11", "DSM-5"), "code": STR, "name": STR,
                          "role": enum("primary", "comorbid")}, ["classifier", "code", "name"])),
    "baseline": ref("PatientState"),
    "hidden_disclosure": nullable(obj({"text": STR, "trust_threshold": UNIT, "openness_threshold": UNIT},
                                      ["text"])),
}, ["title", "name", "age", "gender", "presenting_problem", "background", "core_beliefs", "rules",
    "triggers", "defenses", "communication_style", "goals", "strengths", "disorders", "baseline"],
    "Совпадает с pydantic-схемой CaseModel модуля case_model."))
schema("CaseModelPublic", obj({
    "title": STR, "name": STR, "age": INT, "gender": STR, "occupation": STR, "language": STR, "presenting_problem": STR,
}, desc="Что видит студент: только то, что видит человек на приёме. Структура случая скрыта."))
schema("CaseSummary", obj({
    "id": UUID, "title": STR, "visibility": enum("private", "org", "public"),
    "author_id": UUID, "org_id": nullable(UUID), "latest_version_no": INT,
    "published_version_no": nullable(INT), "archived": BOOL,
    "disorders": arr(ref("Disorder")), "updated_at": DT,
}, ["id", "title", "visibility", "author_id", "latest_version_no", "archived"]))
schema("VersionInfo", obj({"version_no": INT, "status": enum("draft", "published"),
                           "created_at": DT, "published_at": nullable(DT)},
                          ["version_no", "status"]))
schema("CaseDetail", {"allOf": [ref("CaseSummary"), obj({"versions": arr(ref("VersionInfo"))})]})
schema("CaseVersion", obj({
    "id": UUID, "case_id": UUID, "version_no": INT, "status": enum("draft", "published"),
    "case_model": {"oneOf": [ref("CaseModel"), ref("CaseModelPublic")]}, "language": STR, "created_at": DT,
    "published_at": nullable(DT),
}, ["id", "case_id", "version_no", "status", "case_model"]))
schema("CaseIn", obj({"title": STR, "visibility": enum("private", "org", "public"),
                      "case_model": ref("CaseModel")}, ["title", "case_model"]))
schema("CasePatch", obj({"title": STR, "visibility": enum("private", "org", "public"), "archived": BOOL}))
schema("VersionCreateIn", obj({"based_on_version_no": INT}, desc="Создаёт новый черновик на основе версии (по умолчанию последней)."))
schema("ValidationReport", obj({
    "valid": BOOL,
    "errors": arr(obj({"path": STR, "message": STR})),
    "warnings": arr(obj({"path": STR, "code": STR, "message": STR})),
}, ["valid", "errors", "warnings"],
    "errors блокируют публикацию, warnings нет (например: корень ключевого слова короче 4 символов, нет триггеров, "
    "порог тайны выше 0.9)."))
schema("SuggestionIn", obj({
    "section": enum("background", "core_beliefs", "rules", "triggers", "defenses",
                    "communication_style", "hidden_disclosure"),
    "context": obj({}, desc="Уже заполненные части CaseModel"), "language": STR,
}, ["section", "context"]))
schema("SuggestionOut", obj({
    "section": STR, "suggestions": arr(obj({"value": {}, "rationale": STR}, ["value"])),
}, ["section", "suggestions"]))

# --- сессии
schema("SessionIn", obj({
    "case_version_id": UUID, "case_id": S(format="uuid", description="Вместо case_version_id: берётся последняя опубликованная версия"),
    "prev_session_id": nullable(UUID), "group_id": nullable(UUID),
    "mode": enum("training", "preview", "exam"),
}, desc="Нужно указать case_version_id или case_id. mode=preview доступен только автору кейса и работает и на черновике."))
schema("Session", obj({
    "id": UUID, "case_version_id": UUID, "user_id": UUID, "group_id": nullable(UUID),
    "prev_session_id": nullable(UUID), "session_no": INT, "mode": enum("training", "preview", "exam"),
    "status": enum("active", "ended"), "started_at": DT, "ended_at": nullable(DT), "turn_count": INT,
    "state": nullable(ref("PatientState")),
}, ["id", "case_version_id", "user_id", "session_no", "mode", "status", "started_at"]))
schema("MessageIn", obj({"text": S(minLength=1, maxLength=4000), "client_message_id": UUID}, ["text"]))
schema("MessageOut", obj({
    "type": enum("done"), "reply": STR, "state": nullable(ref("PatientState")),
    "delta": obj({"trust": NUM, "anxiety": NUM, "resistance": NUM, "openness": NUM}),
    "triggers": arr(STR), "features_source": enum("llm", "heuristic"),
    "notice": nullable(obj({"code": STR, "text": STR}, ["code", "text"], "Подсказка студенту, например code=student_risk")),
}, ["type", "reply"], "state, delta и triggers возвращаются по правилам видимости (null в режиме exam)."))
schema("SimpleEvent", obj({"type": enum("ended", "out_of_role")}, ["type"],
                          "Ответ на слово «ВЫХОД» (ended) или срыв пациента с роли (out_of_role)."))
schema("Turn", obj({"id": UUID, "turn_no": INT, "speaker": enum("student", "patient"), "content": STR,
                    "flagged": BOOL, "created_at": DT}, ["id", "turn_no", "speaker", "content"]))
schema("StateResponse", obj({"state": ref("PatientState"), "disclosed": BOOL, "turn_no": INT},
                            ["state", "disclosed", "turn_no"]))
schema("StateSnapshot", obj({
    "turn_no": INT, "state": ref("PatientState"), "delta": obj({}), "triggers": arr(STR),
    "features": obj({}), "features_source": enum("llm", "heuristic"), "disclosed": BOOL,
}, ["turn_no", "state"]))
schema("Formulation", obj({"content": obj({}, desc="Модель случая, составленная студентом (структура как у CaseModel)"),
                           "match_score": nullable(NUM), "submitted_at": DT}, ["content"]))
schema("FormulationIn", obj({"content": obj({})}, ["content"]))
schema("SafetyEvent", obj({
    "id": UUID, "session_id": UUID, "turn_id": nullable(UUID),
    "event_type": enum("student_risk", "role_break", "harmful_content"),
    "severity": enum("low", "medium", "high"), "status": enum("open", "acknowledged", "resolved"),
    "handled_by": nullable(UUID), "note": nullable(STR), "created_at": DT,
}, ["id", "session_id", "event_type", "severity", "status"]))
schema("SafetyEventPatch", obj({"status": enum("acknowledged", "resolved"), "note": STR}, ["status"]))

# --- оценка
schema("Criterion", obj({"id": UUID, "code": STR, "title": STR, "description": STR, "weight": NUM},
                        ["id", "code", "title", "weight"]))
schema("Rubric", obj({"id": UUID, "org_id": nullable(UUID), "name": STR, "version": INT,
                      "criteria": arr(ref("Criterion"))}, ["id", "name", "version", "criteria"]))
schema("RubricIn", obj({
    "name": STR,
    "criteria": arr(obj({"code": STR, "title": STR, "description": STR, "weight": NUM}, ["code", "title", "weight"])),
}, ["name", "criteria"]))
schema("AssessmentIn", obj({"rubric_id": UUID, "assessor": enum("llm"), "model": STR}, ["rubric_id"]))
schema("CriterionScore", obj({"criterion_code": STR, "score": INT, "evidence_turn_id": nullable(UUID),
                              "comment": nullable(STR)}, ["criterion_code", "score"]))
schema("Assessment", obj({
    "id": UUID, "session_id": UUID, "rubric_id": UUID, "assessor_type": enum("llm", "teacher"),
    "assessor_id": nullable(UUID), "total_score": NUM, "summary": STR, "teacher_confirmed": BOOL,
    "scores": arr(ref("CriterionScore")), "created_at": DT,
}, ["id", "session_id", "rubric_id", "assessor_type", "total_score", "scores"]))
schema("AssessmentPatch", obj({"summary": STR, "scores": arr(ref("CriterionScore"))}))

# --- аналитика, отчёты, биллинг, администрирование
schema("GroupSummary", obj({
    "group_id": UUID, "sessions_count": INT, "active_students": INT, "avg_total_score": nullable(NUM),
    "open_safety_events": INT, "skills": arr(obj({"code": STR, "title": STR, "avg_score": NUM})),
}, ["group_id", "sessions_count"]))
schema("CaseUsage", obj({
    "case_id": UUID, "sessions_count": INT, "avg_duration_min": NUM,
    "avg_final_trust": NUM, "disclosure_rate": UNIT,
}, ["case_id", "sessions_count"]))
schema("ReportIn", obj({
    "type": enum("group_summary", "student_progress", "case_usage"),
    "params": obj({}, desc="group_id / user_id / case_id и период (from, to)"),
    "format": enum("pdf", "xlsx", "csv"),
}, ["type", "params", "format"]))
schema("CheckoutIn", obj({"plan": STR, "success_url": S(format="uri"), "cancel_url": S(format="uri")},
                         ["plan", "success_url", "cancel_url"]))
schema("CheckoutOut", obj({"checkout_url": S(format="uri")}, ["checkout_url"]))
schema("PromptTemplate", obj({"id": UUID, "kind": enum("patient", "classifier", "judge"), "version": INT,
                              "body": STR, "is_active": BOOL, "created_at": DT},
                             ["id", "kind", "version", "body", "is_active"]))
schema("PromptTemplateIn", obj({"kind": enum("patient", "classifier", "judge"), "body": STR}, ["kind", "body"]))
schema("LlmUsage", obj({
    "from": DT, "to": DT,
    "items": arr(obj({"model": STR, "purpose": enum("patient", "classifier", "judge", "assist"), "calls": INT,
                      "tokens_in": INT, "tokens_out": INT, "p95_latency_ms": INT, "cost": NUM})),
}, ["items"]))
schema("Health", obj({"status": enum("ok", "degraded", "down"),
                      "checks": obj({"database": STR, "redis": STR, "llm": STR})}, ["status"]))

# ---------- эндпоинты ----------
TAGS = OrderedDict([
    ("Auth", "Аутентификация и доступ"),
    ("Profile", "Профиль, согласия, прогресс, право на удаление"),
    ("Organizations", "Организации, участники, приглашения, группы"),
    ("Reference", "Справочники"),
    ("Cases", "Конструктор кейсов и версии модели случая"),
    ("Sessions", "Сессии, диалог, состояние пациента"),
    ("Assessment", "Рубрики и оценка сессий"),
    ("Analytics", "Аналитика и отчёты"),
    ("Safety", "События безопасности"),
    ("Billing", "Подписки и платежи"),
    ("Admin", "Администрирование платформы"),
    ("System", "Служебные"),
])
ENDPOINTS: list[dict] = []
PUBLIC, ANY = "public", "any"
T, OA, PA = "teacher", "org_admin", "platform_admin"
STU = "student"


def E(method, path, tag, summary, roles, *, body=None, resp=None, status=200, query=None, errors=(),
      impl=False, async_job=False, idem=False, etag=False, desc=None, sse=False, redirect=False, file=False):
    ENDPOINTS.append(dict(method=method, path=path, tag=tag, summary=summary, roles=roles, body=body, resp=resp,
                          status=status, query=query or [], errors=list(errors), impl=impl,
                          async_job=async_job, idem=idem, etag=etag, desc=desc, sse=sse, redirect=redirect, file=file))


def page(name):  # обёртка пагинации
    return ("page", name)


Q_PAGE = [("limit", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "Размер страницы"),
          ("cursor", STR, "Курсор из next_cursor предыдущей страницы")]

# Auth
E("POST", "/auth/register", "Auth", "Регистрация (B2C)", PUBLIC, body="RegisterIn", resp="TokenPair", status=201, errors=[409])
E("POST", "/auth/login", "Auth", "Вход по email и паролю", PUBLIC, body="LoginIn", resp="TokenPair")
E("POST", "/auth/refresh", "Auth", "Обновить access-токен по refresh-cookie", PUBLIC, resp="TokenPair")
E("POST", "/auth/logout", "Auth", "Выход, отзыв refresh-токена", ANY, status=204)
E("GET", "/auth/sso/{provider}/start", "Auth", "Начать вход через SSO вуза (OIDC)", PUBLIC, status=302, redirect=True)
E("GET", "/auth/sso/{provider}/callback", "Auth", "Колбэк SSO, выдаёт сессию", PUBLIC, status=302, redirect=True,
  query=[("code", STR, "Код авторизации"), ("state", STR, "Параметр state")])
E("POST", "/auth/password/forgot", "Auth", "Запросить письмо для сброса пароля", PUBLIC, body="PasswordForgotIn", status=202,
  desc="Всегда отвечает 202, даже если email неизвестен (защита от перебора).")
E("POST", "/auth/password/reset", "Auth", "Установить новый пароль по токену из письма", PUBLIC, body="PasswordResetIn", status=204)
# Profile
E("GET", "/me", "Profile", "Текущий пользователь", ANY, resp="User")
E("PATCH", "/me", "Profile", "Изменить профиль", ANY, body="UserPatch", resp="User")
E("GET", "/me/consents", "Profile", "Мои согласия", ANY, resp=arr(ref("Consent")))
E("PUT", "/me/consents/{kind}", "Profile", "Дать или отозвать согласие", ANY, body="ConsentIn", resp="Consent",
  desc="Без согласия transcript_processing сессии создавать нельзя (409 consent_required).")
E("GET", "/me/progress", "Profile", "Мой прогресс по навыкам", STU, resp="Progress")
E("GET", "/me/subscription", "Profile", "Моя подписка", ANY, resp="Subscription")
E("POST", "/me/data-export", "Profile", "Выгрузить мои данные", ANY, resp="Job", status=202, async_job=True)
E("DELETE", "/me", "Profile", "Удалить аккаунт и данные", ANY, resp="Job", status=202, async_job=True,
  desc="Запускает удаление персональных данных и транскриптов. Обезличенная статистика сохраняется.")
# Organizations
E("GET", "/organizations/{orgId}", "Organizations", "Организация", [T, OA], resp="Organization")
E("PATCH", "/organizations/{orgId}", "Organizations", "Изменить организацию", [OA], body="OrganizationPatch", resp="Organization")
E("GET", "/organizations/{orgId}/members", "Organizations", "Участники организации", [T, OA], resp=page("Member"),
  query=Q_PAGE + [("role", enum("student", "teacher", "org_admin"), "Фильтр по роли")])
E("POST", "/organizations/{orgId}/invitations", "Organizations", "Пригласить пользователя", [OA], body="InvitationIn",
  resp="Invitation", status=201, errors=[409])
E("PATCH", "/organizations/{orgId}/members/{userId}", "Organizations", "Изменить роль участника", [OA], body="MemberPatch", resp="Member")
E("DELETE", "/organizations/{orgId}/members/{userId}", "Organizations", "Исключить участника", [OA], status=204)
E("GET", "/organizations/{orgId}/subscription", "Organizations", "Лицензия организации", [OA], resp="Subscription")
E("GET", "/groups", "Organizations", "Группы (мои или организации)", [T, OA, STU], resp=page("Group"),
  query=Q_PAGE + [("org_id", UUID, "Фильтр по организации")])
E("POST", "/groups", "Organizations", "Создать группу", [T, OA], body="GroupIn", resp="Group", status=201)
E("GET", "/groups/{groupId}", "Organizations", "Группа", [T, OA, STU], resp="Group")
E("PATCH", "/groups/{groupId}", "Organizations", "Изменить группу", [T, OA], body="GroupPatch", resp="Group")
E("DELETE", "/groups/{groupId}", "Organizations", "Удалить группу", [OA], status=204, errors=[409])
E("GET", "/groups/{groupId}/members", "Organizations", "Состав группы", [T, OA], resp=page("Member"), query=Q_PAGE)
E("PUT", "/groups/{groupId}/members/{userId}", "Organizations", "Добавить в группу (идемпотентно)", [T, OA], resp="Member")
E("DELETE", "/groups/{groupId}/members/{userId}", "Organizations", "Убрать из группы", [T, OA], status=204)
# Reference
E("GET", "/disorders", "Reference", "Справочник расстройств МКБ-11 и DSM-5", ANY, resp=page("Disorder"),
  query=Q_PAGE + [("classifier", enum("ICD-11", "DSM-5"), "Классификатор"), ("q", STR, "Поиск по коду и названию")])
# Cases
E("GET", "/cases", "Cases", "Каталог кейсов", [T, OA, STU], resp=page("CaseSummary"),
  query=Q_PAGE + [("q", STR, "Поиск по названию"), ("visibility", enum("private", "org", "public"), ""),
                  ("disorder_code", STR, "Код расстройства"), ("mine", BOOL, "Только мои")],
  desc="Студент видит только опубликованные кейсы своей организации и публичные.")
E("POST", "/cases", "Cases", "Создать кейс (первый черновик)", [T, OA], body="CaseIn", resp="CaseDetail", status=201, idem=True)
E("GET", "/cases/{caseId}", "Cases", "Кейс со списком версий", [T, OA, STU], resp="CaseDetail")
E("PATCH", "/cases/{caseId}", "Cases", "Изменить название, доступ, архив", [T, OA], body="CasePatch", resp="CaseSummary")
E("DELETE", "/cases/{caseId}", "Cases", "Удалить кейс (только если нет сессий)", [T, OA], status=204, errors=[409])
E("POST", "/cases/{caseId}/clone", "Cases", "Скопировать кейс себе", [T, OA], resp="CaseDetail", status=201)
E("GET", "/cases/{caseId}/versions", "Cases", "Версии кейса", [T, OA], resp=arr(ref("VersionInfo")))
E("POST", "/cases/{caseId}/versions", "Cases", "Создать новый черновик версии", [T, OA], body="VersionCreateIn",
  resp="CaseVersion", status=201, errors=[409])
E("GET", "/cases/{caseId}/versions/{versionNo}", "Cases", "Версия с полной моделью случая", [T, OA, STU],
  resp="CaseVersion", etag=True, desc="Студенту доступна только опубликованная версия, и в ответе нет скрытой тайны и триггеров.")
E("PUT", "/cases/{caseId}/versions/{versionNo}", "Cases", "Заменить черновик версии", [T, OA], body="CaseModel",
  resp="CaseVersion", etag=True, errors=[409, 412], desc="Опубликованные версии неизменяемы: 409 version_immutable.")
E("POST", "/cases/{caseId}/versions/{versionNo}/validate", "Cases", "Проверить черновик без публикации", [T, OA],
  resp="ValidationReport")
E("POST", "/cases/{caseId}/versions/{versionNo}/publish", "Cases", "Опубликовать версию", [T, OA],
  resp="CaseVersion", errors=[409, 422], desc="Версия становится неизменяемой. 422 validation_failed, если есть errors.")
E("POST", "/case-assist/suggestions", "Cases", "Подсказки LLM для раздела кейса", [T, OA], body="SuggestionIn",
  resp="SuggestionOut", desc="Помощник конструктора. Результат не сохраняется, автор вставляет его в черновик сам.")
# Sessions
E("POST", "/sessions", "Sessions", "Начать сессию", [STU, T, OA], body="SessionIn", resp="Session", status=201,
  impl=True, idem=True, errors=[409])
E("GET", "/sessions", "Sessions", "Список сессий", [STU, T, OA], resp=page("Session"),
  query=Q_PAGE + [("group_id", UUID, ""), ("case_id", UUID, ""), ("user_id", UUID, "Только для преподавателя"),
                  ("status", enum("active", "ended"), "")],
  desc="Студент видит только свои сессии, преподаватель сессии своих групп.")
E("GET", "/sessions/{sessionId}", "Sessions", "Сессия", [STU, T, OA], resp="Session")
E("POST", "/sessions/{sessionId}/end", "Sessions", "Завершить сессию", [STU, T, OA], resp="Session")
E("POST", "/sessions/{sessionId}/messages", "Sessions", "Реплика студента, ответ целиком", [STU, T, OA], body="MessageIn",
  resp={"oneOf": [ref("MessageOut"), ref("SimpleEvent")]}, impl=True, errors=[409, 502])
E("POST", "/sessions/{sessionId}/messages/stream", "Sessions", "Реплика студента, ответ потоком SSE", [STU, T, OA],
  body="MessageIn", impl=True, sse=True, errors=[409])
E("GET", "/sessions/{sessionId}/turns", "Sessions", "Транскрипт", [STU, T, OA], resp=page("Turn"), query=Q_PAGE)
E("GET", "/sessions/{sessionId}/state", "Sessions", "Текущее состояние пациента", [STU, T, OA], resp="StateResponse",
  impl=True, errors=[403], desc="Студенту доступно только в режиме training и если преподаватель разрешил подсказки; в exam всегда 403.")
E("GET", "/sessions/{sessionId}/state/history", "Sessions", "История состояния по репликам", [T, OA], resp=arr(ref("StateSnapshot")))
E("PUT", "/sessions/{sessionId}/formulation", "Sessions", "Отправить свою модель случая", [STU], body="FormulationIn",
  resp="Formulation", desc="Можно перезаписывать, пока сессия активна. После завершения 409.")
E("GET", "/sessions/{sessionId}/formulation", "Sessions", "Модель случая студента и совпадение с эталоном", [STU, T], resp="Formulation")
E("GET", "/sessions/{sessionId}/safety-events", "Sessions", "События безопасности сессии", [T, OA], resp=arr(ref("SafetyEvent")))
# Assessment
E("GET", "/rubrics", "Assessment", "Рубрики", [T, OA, STU], resp=page("Rubric"), query=Q_PAGE)
E("POST", "/rubrics", "Assessment", "Создать рубрику", [T, OA], body="RubricIn", resp="Rubric", status=201)
E("GET", "/rubrics/{rubricId}", "Assessment", "Рубрика", [T, OA, STU], resp="Rubric")
E("POST", "/rubrics/{rubricId}/versions", "Assessment", "Новая версия рубрики", [T, OA], body="RubricIn", resp="Rubric", status=201,
  desc="Рубрики неизменяемы: изменения создают версию, старые оценки ссылаются на старую.")
E("POST", "/sessions/{sessionId}/assessments", "Assessment", "Запустить оценку сессии", [T, STU], body="AssessmentIn",
  resp="Job", status=202, async_job=True, idem=True, errors=[409],
  desc="Оценка выполняется воркером. Студент может запросить оценку только в режиме training.")
E("GET", "/sessions/{sessionId}/assessments", "Assessment", "Оценки сессии", [STU, T], resp=arr(ref("Assessment")))
E("GET", "/assessments/{assessmentId}", "Assessment", "Оценка с баллами и привязкой к репликам", [STU, T], resp="Assessment")
E("PATCH", "/assessments/{assessmentId}", "Assessment", "Поправить баллы и комментарий", [T], body="AssessmentPatch", resp="Assessment")
E("POST", "/assessments/{assessmentId}/confirm", "Assessment", "Подтвердить оценку преподавателем", [T], resp="Assessment")
E("GET", "/jobs/{jobId}", "Assessment", "Статус фоновой задачи", ANY, resp="Job",
  desc="Общий для оценки, выгрузки данных, удаления аккаунта и отчётов. Опрашивать с паузой по заголовку Retry-After.")
E("GET", "/jobs/{jobId}/download", "Assessment", "Скачать файл результата задачи (отчёт, выгрузка данных)", ANY, file=True,
  desc="Только владелец задачи. Файлы хранятся 7 дней, затем удаляются вместе с задачей.")
# Analytics
E("GET", "/analytics/groups/{groupId}/summary", "Analytics", "Сводка по группе", [T, OA], resp="GroupSummary",
  query=[("from", S(format="date"), "Начало периода"), ("to", S(format="date"), "Конец периода")])
E("GET", "/analytics/students/{userId}/progress", "Analytics", "Прогресс студента", [T, OA], resp="Progress")
E("GET", "/analytics/cases/{caseId}/usage", "Analytics", "Использование кейса", [T, OA], resp="CaseUsage")
E("POST", "/reports", "Analytics", "Заказать отчёт (PDF, XLSX, CSV)", [T, OA], body="ReportIn", resp="Job", status=202, async_job=True)
# Safety
E("GET", "/safety-events", "Safety", "События безопасности", [T, OA], resp=page("SafetyEvent"),
  query=Q_PAGE + [("status", enum("open", "acknowledged", "resolved"), ""),
                  ("severity", enum("low", "medium", "high"), ""), ("group_id", UUID, "")])
E("PATCH", "/safety-events/{eventId}", "Safety", "Принять или закрыть событие", [T, OA], body="SafetyEventPatch", resp="SafetyEvent")
# Billing
E("POST", "/billing/checkout", "Billing", "Создать платёжную сессию", [ANY], body="CheckoutIn", resp="CheckoutOut", idem=True)
E("POST", "/webhooks/payments/{provider}", "Billing", "Вебхук платёжного провайдера", PUBLIC, status=204,
  desc="Подпись проверяется по секрету провайдера (HMAC), повторы безопасны (идемпотентность по event id).")
# Admin
E("GET", "/admin/prompt-templates", "Admin", "Шаблоны промптов", [PA], resp=page("PromptTemplate"),
  query=Q_PAGE + [("kind", enum("patient", "classifier", "judge"), "")])
E("POST", "/admin/prompt-templates", "Admin", "Создать версию шаблона", [PA], body="PromptTemplateIn", resp="PromptTemplate", status=201)
E("POST", "/admin/prompt-templates/{templateId}/activate", "Admin", "Сделать версию активной", [PA], resp="PromptTemplate")
E("GET", "/admin/llm-usage", "Admin", "Расход LLM: вызовы, токены, задержка, стоимость", [PA], resp="LlmUsage",
  query=[("from", DT, ""), ("to", DT, "")])
# System
E("GET", "/health", "System", "Liveness", PUBLIC, resp="Health")
E("GET", "/ready", "System", "Readiness: БД, Redis, LLM", PUBLIC, resp="Health", errors=[503])

# ---------- генерация OpenAPI ----------
ERR_TITLES = {
    400: "Некорректный запрос", 401: "Нет или просрочен токен", 403: "Недостаточно прав",
    404: "Не найдено", 409: "Конфликт состояния", 412: "Не совпал If-Match", 422: "Ошибка валидации",
    429: "Слишком много запросов", 502: "LLM недоступна", 503: "Сервис не готов",
}
PATH_PARAM_TYPES = {"versionNo": INT}


def build_openapi() -> dict:
    paths: dict = OrderedDict()
    for e in ENDPOINTS:
        op: dict = {"tags": [e["tag"]], "summary": e["summary"], "operationId": None}
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", e["method"].lower() + "_" + e["path"]).strip("_")
        op["operationId"] = slug
        d = e["desc"] or ""
        roles = e["roles"] if isinstance(e["roles"], list) else [e["roles"]]
        op["description"] = (d + "\n\n" if d else "") + "Роли: " + ", ".join(map(str, roles)) + "."
        op["x-roles"] = roles
        op["x-status"] = "implemented-in-module" if e["impl"] else "planned"
        params = []
        for p in re.findall(r"{(\w+)}", e["path"]):
            sch = PATH_PARAM_TYPES.get(p, STR if p in ("provider", "kind") else UUID)
            params.append({"name": p, "in": "path", "required": True, "schema": sch})
        for name, sch, pd in e["query"]:
            q = {"name": name, "in": "query", "schema": sch}
            if pd:
                q["description"] = pd
            params.append(q)
        if e["idem"]:
            params.append({"name": "Idempotency-Key", "in": "header", "required": False, "schema": STR,
                           "description": "Повтор запроса с тем же ключом в течение 24 ч возвращает первый результат."})
        if e["etag"] and e["method"] == "PUT":
            params.append({"name": "If-Match", "in": "header", "required": True, "schema": STR,
                           "description": "ETag, полученный при GET версии. Несовпадение даёт 412."})
        if params:
            op["parameters"] = params
        if e["body"]:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref(e["body"])}}}
        responses: dict = OrderedDict()
        st = str(e["status"])
        if e["sse"]:
            responses[st] = {"description": "Поток событий SSE: token, done, ended, out_of_role, error",
                             "content": {"text/event-stream": {"schema": {"type": "string",
                                         "description": "Строки вида `data: {JSON}` + пустая строка"}}}}
        elif e["file"]:
            responses[st] = {"description": "Файл результата", "content": {"application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}}}}
        elif e["redirect"]:
            responses[st] = {"description": "Перенаправление", "headers": {"Location": {"schema": STR}}}
        elif e["resp"] is None:
            responses[st] = {"description": "Без содержимого" if e["status"] == 204 else "Принято"}
        else:
            r = e["resp"]
            if isinstance(r, tuple):
                sch = obj({"items": arr(ref(r[1])), "next_cursor": nullable(STR)}, ["items"])
            elif isinstance(r, str):
                sch = ref(r)
            else:
                sch = r
            rsp = {"description": "Успех", "content": {"application/json": {"schema": sch}}}
            if e["async_job"]:
                rsp["headers"] = {"Location": {"schema": STR, "description": "URL задачи /jobs/{id}"},
                                  "Retry-After": {"schema": INT}}
            if e["etag"]:
                rsp.setdefault("headers", {})["ETag"] = {"schema": STR}
            responses[st] = rsp
        codes = set(e["errors"]) | {429}
        if e["roles"] != PUBLIC:
            codes |= {401}
            if e["roles"] != ANY or "{" in e["path"]:
                codes |= {403}
        if "{" in e["path"] and e["roles"] != PUBLIC:
            codes |= {404}
        if e["body"]:
            codes |= {422}
        for c in sorted(codes):
            responses[str(c)] = {"$ref": f"#/components/responses/E{c}"}
        op["responses"] = responses
        if e["roles"] == PUBLIC:
            op["security"] = []
        paths.setdefault(e["path"], OrderedDict())[e["method"].lower()] = op

    comp_resp = {
        f"E{c}": {"description": t, "content": {"application/problem+json": {"schema": ref("Problem")}}}
        for c, t in ERR_TITLES.items()
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PsyTrainer API", "version": "1.0.0",
            "description": ("Платформа тренировки навыков консультирования с ИИ-пациентами. "
                            "Эндпоинты с x-status=implemented-in-module уже реализованы в модуле case_model, "
                            "остальные спроектированы."),
        },
        "servers": [{"url": "https://{host}/api/v1", "variables": {"host": {"default": "psytrainer.example"}}}],
        "tags": [{"name": k, "description": v} for k, v in TAGS.items()],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
            "schemas": SCHEMAS,
            "responses": comp_resp,
        },
    }


def md_tables() -> str:
    out = []
    for tag, title in TAGS.items():
        rows = [e for e in ENDPOINTS if e["tag"] == tag]
        out.append(f"### {title}\n")
        out.append("| Метод и путь | Роли | Назначение | Статус |\n|---|---|---|---|")
        for e in rows:
            roles = e["roles"] if isinstance(e["roles"], list) else [e["roles"]]
            r = ", ".join(roles)
            st = "реализован" if e["impl"] else "проект"
            out.append(f"| `{e['method']} {e['path']}` | {r} | {e['summary']} | {st} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    spec = build_openapi()
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        yaml.safe_dump(json.loads(json.dumps(spec)), f, allow_unicode=True, sort_keys=False, width=110)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w", encoding="utf-8").write(md_tables())
    print("endpoints:", len(ENDPOINTS), "schemas:", len(SCHEMAS))
