from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, delete, exists, func, or_, select

from ..deps import C, DB, CurrentUser, Idem, current_user, default_rate_limit, require_roles
from ..errors import ApiError
from ..models import CaseDisorder, CaseTemplate, CaseVersion, DisorderRef, LlmCall, PatientSession, User
from ..pagination import paginate
from ..schemas import (
    CaseDetailOut, CaseIn, CasePatch, CaseSummaryOut, CaseVersionOut, DisorderOut, Page, SuggestionIn, SuggestionItem,
    SuggestionOut, ValidationReport, VersionCreateIn, VersionInfo,
)
from ..engine.schemas import CaseModel
from ..services import access
from ..services.case_lint import lint_case

router = APIRouter(tags=["Cases"], dependencies=[Depends(current_user), default_rate_limit])
STAFF = Depends(require_roles("teacher", "org_admin", "platform_admin"))
PUBLIC_FIELDS = ("title", "name", "age", "gender", "occupation", "language", "presenting_problem")


def etag(v: CaseVersion) -> str:
    return f'"{v.revision}"'


def public_card(model: dict[str, Any]) -> dict[str, Any]:
    """Что видит студент: только то, что видит человек на приёме. Структура случая скрыта: её студент должен вывести сам."""
    return {k: model.get(k) for k in PUBLIC_FIELDS}


def version_out(v: CaseVersion, user: User) -> CaseVersionOut:
    model = public_card(v.case_model) if user.role == "student" else v.case_model
    return CaseVersionOut(id=v.id, case_id=v.case_id, version_no=v.version_no, status=v.status, case_model=model,
                          language=v.language, created_at=v.created_at, published_at=v.published_at)


async def known_disorders(db) -> set[tuple[str, str]]:
    return {(d.classifier, d.code) for d in (await db.execute(select(DisorderRef))).scalars().all()}


async def sync_disorders(db, version: CaseVersion) -> None:
    await db.execute(delete(CaseDisorder).where(CaseDisorder.case_version_id == version.id))
    refs = {(d.classifier, d.code): d for d in (await db.execute(select(DisorderRef))).scalars().all()}
    for item in version.case_model.get("disorders", []):
        ref = refs.get((item.get("classifier"), item.get("code")))
        if ref is not None:
            db.add(CaseDisorder(case_version_id=version.id, disorder_id=ref.id, role=item.get("role", "primary")))


async def summaries(db, user: User, cases: list[CaseTemplate]) -> list[CaseSummaryOut]:
    if not cases:
        return []
    ids = [c.id for c in cases]
    versions = (await db.execute(select(CaseVersion).where(CaseVersion.case_id.in_(ids)))).scalars().all()
    latest: dict[uuid.UUID, CaseVersion] = {}
    published: dict[uuid.UUID, int] = {}
    for v in versions:
        if v.case_id not in latest or v.version_no > latest[v.case_id].version_no:
            latest[v.case_id] = v
        if v.status == "published":
            published[v.case_id] = max(published.get(v.case_id, 0), v.version_no)
    shown = {cid: v.id for cid, v in latest.items()}
    dis_rows = (await db.execute(select(CaseDisorder.case_version_id, DisorderRef).join(
        DisorderRef, DisorderRef.id == CaseDisorder.disorder_id).where(CaseDisorder.case_version_id.in_(list(shown.values()))))).all()
    dis: dict[uuid.UUID, list[DisorderOut]] = {}
    for vid, d in dis_rows:
        dis.setdefault(vid, []).append(DisorderOut.model_validate(d))
    out = []
    for c in cases:
        pub = published.get(c.id)
        out.append(CaseSummaryOut(
            id=c.id, title=c.title, visibility=c.visibility, author_id=c.author_id, org_id=c.org_id,
            latest_version_no=(pub if user.role == "student" else latest[c.id].version_no) or 0,
            published_version_no=pub, archived=c.archived,
            disorders=dis.get(shown.get(c.id), []) if user.role != "student" else [], updated_at=c.updated_at))
    return out


async def versions_of(db, case: CaseTemplate, *, published_only: bool) -> list[CaseVersion]:
    stmt = select(CaseVersion).where(CaseVersion.case_id == case.id).order_by(CaseVersion.version_no)
    if published_only:
        stmt = stmt.where(CaseVersion.status == "published")
    return list((await db.execute(stmt)).scalars().all())


async def version_or_404(db, case_id: uuid.UUID, version_no: int) -> CaseVersion:
    v = (await db.execute(select(CaseVersion).where(CaseVersion.case_id == case_id,
                                                    CaseVersion.version_no == version_no))).scalar_one_or_none()
    if v is None:
        raise access.not_found("Версия")
    return v


def _check_visibility(user: User, visibility: str) -> None:
    if visibility == "org" and user.org_id is None and user.role != "platform_admin":
        raise ApiError(422, "validation_failed", "Доступ «org» возможен только для пользователя организации")


# ---------- каталог ----------
@router.get("/cases", response_model=Page)
async def list_cases(db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     cursor: str | None = None, q: str | None = None, visibility: str | None = None,
                     disorder_code: str | None = None, mine: bool = False):
    stmt = select(CaseTemplate).order_by(CaseTemplate.updated_at.desc(), CaseTemplate.id)
    if user.role != "platform_admin":
        cond = [CaseTemplate.author_id == user.id, CaseTemplate.visibility == "public"]
        if user.org_id is not None:
            cond.append(and_(CaseTemplate.visibility == "org", CaseTemplate.org_id == user.org_id))
            if user.role == "org_admin":
                cond.append(CaseTemplate.org_id == user.org_id)
        stmt = stmt.where(or_(*cond), or_(CaseTemplate.archived.is_(False), CaseTemplate.author_id == user.id))
    if user.role == "student":
        stmt = stmt.where(exists().where(CaseVersion.case_id == CaseTemplate.id, CaseVersion.status == "published"))
    if mine:
        stmt = stmt.where(CaseTemplate.author_id == user.id)
    if q:
        stmt = stmt.where(func.lower(CaseTemplate.title).like(f"%{q.lower()}%"))
    if visibility:
        stmt = stmt.where(CaseTemplate.visibility == visibility)
    if disorder_code:
        stmt = stmt.where(exists().where(
            CaseVersion.case_id == CaseTemplate.id, CaseDisorder.case_version_id == CaseVersion.id,
            CaseDisorder.disorder_id == DisorderRef.id, DisorderRef.code == disorder_code))
    page = await paginate(db, stmt, limit, cursor)
    page["items"] = await summaries(db, user, page["items"])
    return page


@router.post("/cases", response_model=CaseDetailOut, status_code=201, dependencies=[Depends(require_roles("teacher", "org_admin"))])
async def create_case(body: CaseIn, request: Request, db: DB, user: CurrentUser, idem: Idem):
    if (replay := await idem.replay()) is not None:
        return replay
    _check_visibility(user, body.visibility)
    case = CaseTemplate(id=uuid.uuid4(), author_id=user.id, org_id=user.org_id, title=body.title,
                        visibility=body.visibility)
    version = CaseVersion(id=uuid.uuid4(), case_id=case.id, version_no=1, status="draft",
                          case_model=body.case_model.model_dump(mode="json"), language=body.case_model.language)
    db.add_all([case, version])
    await db.flush()
    await sync_disorders(db, version)
    await db.commit()
    out = (await _detail(db, user, case))
    await idem.store(201, jsonable_encoder(out))
    return out


async def _detail(db, user: User, case: CaseTemplate) -> CaseDetailOut:
    [summary] = await summaries(db, user, [case])
    vs = await versions_of(db, case, published_only=not access.can_edit_case(user, case))
    return CaseDetailOut(**summary.model_dump(), versions=[
        VersionInfo(version_no=v.version_no, status=v.status, created_at=v.created_at, published_at=v.published_at) for v in vs])


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
async def get_case(case_id: uuid.UUID, db: DB, user: CurrentUser):
    return await _detail(db, user, await access.case_for_view(db, user, case_id))


@router.patch("/cases/{case_id}", response_model=CaseSummaryOut, dependencies=[STAFF])
async def patch_case(case_id: uuid.UUID, body: CasePatch, db: DB, user: CurrentUser):
    case = await access.case_for_edit(db, user, case_id)
    data = body.model_dump(exclude_unset=True)
    if "visibility" in data and data["visibility"] is not None:
        _check_visibility(user, data["visibility"])
    for k, v in data.items():
        if v is not None:
            setattr(case, k, v)
    await db.commit()
    return (await summaries(db, user, [case]))[0]


@router.delete("/cases/{case_id}", status_code=204, dependencies=[STAFF])
async def delete_case(case_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    case = await access.case_for_edit(db, user, case_id)
    vids = list((await db.execute(select(CaseVersion.id).where(CaseVersion.case_id == case.id))).scalars().all())
    if vids and (await db.execute(select(func.count(PatientSession.id)).where(
            PatientSession.case_version_id.in_(vids)))).scalar_one():
        raise ApiError(409, "case_has_sessions", "По кейсу есть сессии: заархивируйте его вместо удаления")
    if vids:
        await db.execute(delete(CaseDisorder).where(CaseDisorder.case_version_id.in_(vids)))
        await db.execute(delete(CaseVersion).where(CaseVersion.case_id == case.id))
    await db.delete(case)
    await db.commit()
    return Response(status_code=204)


@router.post("/cases/{case_id}/clone", response_model=CaseDetailOut, status_code=201, dependencies=[STAFF])
async def clone_case(case_id: uuid.UUID, db: DB, user: CurrentUser):
    case = await access.case_for_view(db, user, case_id)
    editor = access.can_edit_case(user, case)
    vs = await versions_of(db, case, published_only=not editor)
    if not vs:
        raise access.not_found("Версия")
    src = vs[-1]
    copy = CaseTemplate(id=uuid.uuid4(), author_id=user.id, org_id=user.org_id, title=f"{case.title} (копия)",
                        visibility="private")
    version = CaseVersion(id=uuid.uuid4(), case_id=copy.id, version_no=1, status="draft",
                          case_model=json.loads(json.dumps(src.case_model)), language=src.language)
    db.add_all([copy, version])
    await db.flush()
    await sync_disorders(db, version)
    await db.commit()
    return await _detail(db, user, copy)


# ---------- версии ----------
@router.get("/cases/{case_id}/versions", response_model=list[VersionInfo], dependencies=[STAFF])
async def list_versions(case_id: uuid.UUID, db: DB, user: CurrentUser):
    case = await access.case_for_view(db, user, case_id)
    vs = await versions_of(db, case, published_only=not access.can_edit_case(user, case))
    return [VersionInfo(version_no=v.version_no, status=v.status, created_at=v.created_at, published_at=v.published_at) for v in vs]


@router.post("/cases/{case_id}/versions", response_model=CaseVersionOut, status_code=201, dependencies=[STAFF])
async def create_version(case_id: uuid.UUID, body: VersionCreateIn, response: Response, db: DB, user: CurrentUser):
    case = await access.case_for_edit(db, user, case_id)
    vs = await versions_of(db, case, published_only=False)
    if any(v.status == "draft" for v in vs):
        raise ApiError(409, "already_exists", "У кейса уже есть черновик: правьте или опубликуйте его")
    base = vs[-1] if body.based_on_version_no is None else next(
        (v for v in vs if v.version_no == body.based_on_version_no), None)
    if base is None:
        raise access.not_found("Версия")
    v = CaseVersion(id=uuid.uuid4(), case_id=case.id, version_no=vs[-1].version_no + 1, status="draft",
                    case_model=json.loads(json.dumps(base.case_model)), language=base.language)
    db.add(v)
    await db.flush()
    await sync_disorders(db, v)
    await db.commit()
    response.headers["ETag"] = etag(v)
    return version_out(v, user)


@router.get("/cases/{case_id}/versions/{version_no}", response_model=CaseVersionOut)
async def get_version(case_id: uuid.UUID, version_no: int, response: Response, db: DB, user: CurrentUser):
    case = await access.case_for_view(db, user, case_id)
    v = await version_or_404(db, case_id, version_no)
    if v.status != "published" and not access.can_edit_case(user, case):
        raise access.not_found("Версия")
    response.headers["ETag"] = etag(v)
    return version_out(v, user)


@router.put("/cases/{case_id}/versions/{version_no}", response_model=CaseVersionOut, dependencies=[STAFF])
async def replace_version(case_id: uuid.UUID, version_no: int, body: CaseModel, response: Response, db: DB,
                          user: CurrentUser, if_match: Annotated[str | None, Header()] = None):
    await access.case_for_edit(db, user, case_id)
    v = await version_or_404(db, case_id, version_no)
    if v.status != "draft":
        raise ApiError(409, "version_immutable", "Опубликованная версия неизменяема: создайте новый черновик")
    if if_match is None or if_match != etag(v):
        raise ApiError(412, "precondition_failed", "Нужен актуальный заголовок If-Match (ETag версии)")
    v.case_model, v.language, v.revision = body.model_dump(mode="json"), body.language, v.revision + 1
    await sync_disorders(db, v)
    await db.commit()
    response.headers["ETag"] = etag(v)
    return version_out(v, user)


@router.post("/cases/{case_id}/versions/{version_no}/validate", response_model=ValidationReport, dependencies=[STAFF])
async def validate_version(case_id: uuid.UUID, version_no: int, db: DB, user: CurrentUser):
    await access.case_for_edit(db, user, case_id)
    v = await version_or_404(db, case_id, version_no)
    return lint_case(v.case_model, await known_disorders(db))


@router.post("/cases/{case_id}/versions/{version_no}/publish", response_model=CaseVersionOut, dependencies=[STAFF])
async def publish_version(case_id: uuid.UUID, version_no: int, db: DB, user: CurrentUser):
    case = await access.case_for_edit(db, user, case_id)
    v = await version_or_404(db, case_id, version_no)
    if v.status != "draft":
        raise ApiError(409, "version_immutable", "Версия уже опубликована")
    report = lint_case(v.case_model, await known_disorders(db))
    if not report.valid:
        raise ApiError(422, "validation_failed", "Модель случая не прошла проверку",
                       errors=[{"field": e.path, "message": e.message} for e in report.errors])
    v.status, v.published_at = "published", datetime.now(timezone.utc)
    case.updated_at = v.published_at
    await db.commit()
    return version_out(v, user)


# ---------- помощник конструктора ----------
_ASSIST_SHAPES = {
    "background": 'список из 3-5 коротких строк (факты истории)',
    "core_beliefs": 'список из 2-3 глубинных убеждений от первого лица',
    "rules": 'список из 2-4 правил вида «если..., то...»',
    "defenses": 'список из 2-4 защит (юмор, интеллектуализация и т. п.)',
    "triggers": 'объект {"id","keywords":[корни слов],"situation","automatic_thought","emotion","behavior","intensity":0..1}',
    "communication_style": 'объект {"verbosity":"terse|moderate|talkative","tone","quirks":[...]}',
    "hidden_disclosure": 'объект {"text","trust_threshold":0..1,"openness_threshold":0..1}',
}


@router.post("/case-assist/suggestions", response_model=SuggestionOut, dependencies=[STAFF])
async def suggest(body: SuggestionIn, c: C, db: DB, user: CurrentUser):
    shape = _ASSIST_SHAPES[body.section]
    system = ("Ты помощник преподавателя клинической психологии и составляешь учебного виртуального пациента. "
              f"Предложи 3 разных варианта для раздела «{body.section}», согласованных с уже заполненными частями. "
              f"Значение каждого варианта: {shape}. Язык: {body.language}. "
              'Верни ТОЛЬКО JSON: {"suggestions":[{"value":...,"rationale":"почему так"}]}')
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(body.context, ensure_ascii=False)[:6000]}]
    try:
        raw, usage = await c.judge_llm.chat(messages, temperature=0.8, json_mode=True)
    except Exception as exc:
        raise ApiError(502, "llm_unavailable", f"LLM недоступна: {exc}")
    db.add(LlmCall(purpose="assist", model=usage.model, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
                   latency_ms=usage.latency_ms))
    await db.commit()
    try:
        items = json.loads(raw)["suggestions"]
        out = [SuggestionItem(value=i["value"], rationale=i.get("rationale")) for i in items if "value" in i][:5]
        if not out:
            raise ValueError("пусто")
    except (ValueError, KeyError, TypeError):
        raise ApiError(502, "llm_unavailable", "LLM вернула ответ в непригодном формате, повторите запрос")
    return SuggestionOut(section=body.section, suggestions=out)
