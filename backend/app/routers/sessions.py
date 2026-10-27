from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_, select

from ..deps import C, DB, CurrentUser, Idem, current_user, default_rate_limit, rate_limit, require_roles
from ..errors import ApiError
from ..models import CaseVersion, PatientSession, SafetyEvent, StateSnapshot, StudentFormulation, StudyGroup, Turn, User
from ..pagination import paginate
from ..schemas import (
    FormulationIn, FormulationOut, MessageIn, MessageOut, Page, SafetyEventOut, SessionIn, SessionOut, SnapshotOut,
    StateOut, TurnOut,
)
from ..services import access
from ..services.sessions import SessionService

router = APIRouter(prefix="/sessions", tags=["Sessions"], dependencies=[Depends(current_user), default_rate_limit])
PLAYERS = Depends(require_roles("student", "teacher", "org_admin"))
OBSERVERS = Depends(require_roles("teacher", "org_admin"))
messages_limit = Depends(rate_limit("messages", "rate_limit_messages_per_min"))
SSE_PING_SECONDS = 15.0  # комментарий «: ping», чтобы прокси не закрывали простаивающее соединение


async def to_out(svc: SessionService, user: User, s: PatientSession, *, turn_count: int | None = None,
                 with_state: bool = True) -> SessionOut:
    state = None
    if with_state and access.state_visible(user, s):
        state = (await svc.runtime_for(s)).state
    if turn_count is None:
        turn_count = (await svc.turn_counts([s.id])).get(s.id, 0)
    return SessionOut(id=s.id, case_version_id=s.case_version_id, user_id=s.user_id, group_id=s.group_id,
                      prev_session_id=s.prev_session_id, session_no=s.session_no, mode=s.mode, status=s.status,
                      started_at=s.started_at, ended_at=s.ended_at, turn_count=turn_count, state=state)


@router.post("", response_model=SessionOut, status_code=201, dependencies=[PLAYERS])
async def start_session(body: SessionIn, c: C, db: DB, user: CurrentUser, idem: Idem):
    if (replay := await idem.replay()) is not None:
        return replay
    svc = SessionService(c, db)
    session, _ = await svc.start(user, body)
    out = await to_out(svc, user, session)
    await idem.store(201, jsonable_encoder(out))
    return out


@router.get("", response_model=Page, dependencies=[PLAYERS])
async def list_sessions(c: C, db: DB, user: CurrentUser, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                        cursor: str | None = None, group_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None,
                        user_id: uuid.UUID | None = None, status: str | None = None):
    stmt = select(PatientSession).order_by(PatientSession.started_at.desc(), PatientSession.id)
    if user.role == "student":
        stmt = stmt.where(PatientSession.user_id == user.id)
    elif user.role == "teacher":
        stmt = stmt.where(or_(PatientSession.user_id == user.id, PatientSession.group_id.in_(
            select(StudyGroup.id).where(StudyGroup.teacher_id == user.id))))
    else:
        stmt = stmt.where(PatientSession.user_id.in_(select(User.id).where(User.org_id == user.org_id)))
    if group_id:
        stmt = stmt.where(PatientSession.group_id == group_id)
    if case_id:
        stmt = stmt.where(PatientSession.case_version_id.in_(select(CaseVersion.id).where(CaseVersion.case_id == case_id)))
    if user_id:
        stmt = stmt.where(PatientSession.user_id == user_id)
    if status:
        stmt = stmt.where(PatientSession.status == status)
    page = await paginate(db, stmt, limit, cursor)
    svc = SessionService(c, db)
    counts = await svc.turn_counts([s.id for s in page["items"]])
    page["items"] = [await to_out(svc, user, s, turn_count=counts.get(s.id, 0), with_state=False) for s in page["items"]]
    return page


@router.get("/{session_id}", response_model=SessionOut, dependencies=[PLAYERS])
async def get_session(session_id: uuid.UUID, c: C, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    return await to_out(SessionService(c, db), user, s)


@router.post("/{session_id}/end", response_model=SessionOut, dependencies=[PLAYERS])
async def end_session(session_id: uuid.UUID, c: C, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id, owner_only=True)
    svc = SessionService(c, db)
    await svc.end(s)
    return await to_out(svc, user, s)


# ---------- диалог ----------
@router.post("/{session_id}/messages", dependencies=[PLAYERS, messages_limit], response_model=MessageOut)
async def send_message(session_id: uuid.UUID, body: MessageIn, c: C, db: DB, user: CurrentUser):
    """Ответ целиком. Для слова «ВЫХОД» и выхода пациента из роли возвращает {"type": "ended" | "out_of_role"}."""
    s = await access.load_session(db, user, session_id, owner_only=True)
    final: dict[str, Any] | None = None
    async for event in SessionService(c, db).respond(s, user, body.text, body.client_message_id):
        if event["type"] != "token":
            final = event
    if final is None or final["type"] == "error":
        raise ApiError(502, "llm_unavailable", (final or {}).get("message", "Нет ответа от LLM"))
    if final["type"] != "done":
        return JSONResponse({"type": final["type"]})
    return final


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/{session_id}/messages/stream", dependencies=[PLAYERS, messages_limit])
async def send_message_stream(session_id: uuid.UUID, body: MessageIn, c: C, user: CurrentUser):
    """SSE: события token / done / ended / out_of_role / error. Ошибки проверок (404/409/422) приходят обычным
    HTTP-ответом: проверки выполняются до начала стрима, а ожидание модели идёт уже внутри него с пингами."""
    db = c.session_factory()  # своя сессия БД: она должна жить, пока идёт стрим
    try:
        s = await access.load_session(db, user, session_id, owner_only=True)
        gen, release = await SessionService(c, db).begin(s, user, body.text, body.client_message_id)
    except BaseException:
        await db.close()
        raise

    async def events() -> AsyncIterator[str]:
        pending: asyncio.Future | None = None
        try:
            while True:
                pending = asyncio.ensure_future(gen.__anext__())
                while True:  # пока модель думает, раз в SSE_PING_SECONDS отправляем пинг; сам запрос при этом не отменяется
                    done, _ = await asyncio.wait({pending}, timeout=SSE_PING_SECONDS)
                    if done:
                        break
                    yield ": ping\n\n"
                try:
                    ev = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                yield _sse(ev)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
            await gen.aclose()
            await release()
            await db.close()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{session_id}/turns", response_model=Page, dependencies=[PLAYERS])
async def list_turns(session_id: uuid.UUID, db: DB, user: CurrentUser,
                     limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None):
    s = await access.load_session(db, user, session_id)
    stmt = select(Turn).where(Turn.session_id == s.id).order_by(Turn.turn_no)
    return await paginate(db, stmt, limit, cursor, lambda t: TurnOut(
        id=t.id, turn_no=t.turn_no, speaker=t.speaker, content=t.content, flagged=t.flagged, created_at=t.created_at))


# ---------- состояние ----------
@router.get("/{session_id}/state", response_model=StateOut, dependencies=[PLAYERS])
async def get_state(session_id: uuid.UUID, c: C, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    if not access.state_visible(user, s):
        raise ApiError(403, "state_hidden", "В экзаменационном режиме состояние пациента скрыто")
    rt = await SessionService(c, db).runtime_for(s)
    return StateOut(state=rt.state, disclosed=rt.disclosed, turn_no=rt.turn_no)


@router.get("/{session_id}/state/history", response_model=list[SnapshotOut], dependencies=[OBSERVERS])
async def state_history(session_id: uuid.UUID, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    rows = (await db.execute(select(StateSnapshot, Turn.turn_no).join(Turn, Turn.id == StateSnapshot.turn_id).where(
        Turn.session_id == s.id).order_by(Turn.turn_no))).all()
    return [SnapshotOut(turn_no=n, state={"trust": sn.trust, "anxiety": sn.anxiety, "resistance": sn.resistance,
                                          "openness": sn.openness}, delta=sn.extra.get("delta", {}),
                        triggers=sn.extra.get("triggers", []), features=sn.extra.get("features", {}),
                        features_source=sn.extra.get("features_source"), disclosed=bool(sn.extra.get("disclosed")))
            for sn, n in rows]


# ---------- модель случая студента ----------
@router.put("/{session_id}/formulation", response_model=FormulationOut, dependencies=[Depends(require_roles("student"))])
async def put_formulation(session_id: uuid.UUID, body: FormulationIn, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id, owner_only=True)
    if s.status != "active":
        raise ApiError(409, "session_not_active", "После завершения сессии модель случая менять нельзя")
    if len(json.dumps(body.content, ensure_ascii=False)) > 50_000:
        raise ApiError(422, "validation_failed", "Слишком большая модель случая (лимит 50 КБ)")
    form = (await db.execute(select(StudentFormulation).where(StudentFormulation.session_id == s.id))).scalar_one_or_none()
    if form is None:
        form = StudentFormulation(session_id=s.id, content=body.content)
        db.add(form)
    else:
        form.content = body.content
    await db.commit()
    await db.refresh(form)
    return FormulationOut(content=form.content, match_score=None, submitted_at=form.submitted_at)


@router.get("/{session_id}/formulation", response_model=FormulationOut, dependencies=[Depends(require_roles("student", "teacher"))])
async def get_formulation(session_id: uuid.UUID, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    form = (await db.execute(select(StudentFormulation).where(StudentFormulation.session_id == s.id))).scalar_one_or_none()
    if form is None:
        raise access.not_found("Модель случая")
    return FormulationOut(content=form.content, match_score=form.match_score if s.status == "ended" else None,
                          submitted_at=form.submitted_at)


@router.get("/{session_id}/safety-events", response_model=list[SafetyEventOut], dependencies=[OBSERVERS])
async def session_safety_events(session_id: uuid.UUID, db: DB, user: CurrentUser):
    s = await access.load_session(db, user, session_id)
    rows = (await db.execute(select(SafetyEvent).where(SafetyEvent.session_id == s.id).order_by(SafetyEvent.created_at))).scalars().all()
    return rows
