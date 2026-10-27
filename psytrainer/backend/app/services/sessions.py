"""Сервис сессий (процессы 2 и 3 на DFD): запуск, диалог со стримингом, завершение, состояние пациента."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..engine.prompt_builder import (
    OUT_OF_ROLE_MARK, STOP_WORD, build_classifier_messages, build_messages, build_system_prompt, parse_features,
)
from ..engine.schemas import CaseModel, PatientState, RuntimeState
from ..engine.state_engine import (
    carry_over_state, detect_triggers, disclosure_mode, heuristic_features, update_state,
)
from ..errors import ApiError
from ..llm import LLMUsage
from ..models import (
    CaseVersion, Consent, LlmCall, PatientSession, PromptTemplate, SafetyEvent, StateSnapshot, StudentFormulation,
    Turn, User,
)
from ..schemas import SessionIn
from . import access, safety
from .formulation import formulation_match

RUNTIME_TTL = 24 * 3600
HISTORY_TURNS = 16
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def clean_reply(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def runtime_key(session_id: uuid.UUID) -> str:
    return f"psy:session:{session_id}:runtime"


def _state_of(s: StateSnapshot) -> PatientState:
    return PatientState(trust=s.trust, anxiety=s.anxiety, resistance=s.resistance, openness=s.openness)


class SessionService:
    def __init__(self, c: Container, db: AsyncSession) -> None:
        self.c, self.db = c, db

    # ---------- запуск ----------
    async def start(self, user: User, body: SessionIn) -> tuple[PatientSession, RuntimeState]:
        db = self.db
        consent = (await db.execute(select(Consent).where(
            Consent.user_id == user.id, Consent.kind == "transcript_processing"))).scalar_one_or_none()
        if consent is None or not consent.granted:
            raise ApiError(409, "consent_required", "Перед первой сессией дайте согласие transcript_processing")

        if body.case_version_id is None and body.case_id is None:
            raise ApiError(422, "validation_failed", "Нужно указать case_version_id или case_id")
        if body.case_version_id is not None:
            cv = await db.get(CaseVersion, body.case_version_id)
            if cv is None:
                raise access.not_found("Версия кейса")
            case = await access.load_case(db, cv.case_id)
        else:
            case = await access.load_case(db, body.case_id)
            no = await access.published_version_no(db, case.id)
            if no is None:
                raise access.not_found("Опубликованная версия кейса")
            cv = (await db.execute(select(CaseVersion).where(
                CaseVersion.case_id == case.id, CaseVersion.version_no == no))).scalar_one()

        if body.mode == "preview":
            if user.role == "student":
                raise ApiError(403, "forbidden", "Предпросмотр доступен только преподавателям")
            if not access.can_edit_case(user, case):
                raise access.not_found("Кейс")
        else:
            if not await access.can_view_case(db, user, case):
                raise access.not_found("Кейс")
            if cv.status != "published":
                raise ApiError(409, "version_not_published", "Сессию можно начать только на опубликованной версии")

        if body.mode == "exam" and body.group_id is None:
            raise ApiError(422, "validation_failed", "Для режима exam нужен group_id")
        if body.group_id is not None:
            await access.group_for(db, user, body.group_id)

        prev: PatientSession | None = None
        if body.prev_session_id is not None:
            prev = await db.get(PatientSession, body.prev_session_id)
            if prev is None or prev.user_id != user.id:
                raise access.not_found("Предыдущая сессия")
            if prev.case_version_id != cv.id or prev.status != "ended":
                raise ApiError(409, "previous_session_mismatch",
                               "Предыдущая сессия должна быть завершена и относиться к той же версии кейса")

        session = PatientSession(id=uuid.uuid4(), case_version_id=cv.id, user_id=user.id, group_id=body.group_id,
                                 prev_session_id=prev.id if prev else None,
                                 session_no=(prev.session_no + 1) if prev else 1, mode=body.mode)
        db.add(session)
        await db.flush()
        runtime = await self._initial_runtime(session, CaseModel.model_validate(cv.case_model))
        await db.commit()
        await self._save_runtime(session.id, runtime)
        return session, runtime

    # ---------- диалог ----------
    async def begin(self, session: PatientSession, user: User, text: str, client_message_id: uuid.UUID | None = None):
        """Проверки и блокировка сессии. Все ошибки (404/409/422) бросаются здесь, ДО начала стрима.
        Возвращает (генератор событий, release): release() нужно вызвать всегда, он снимает блокировку.
        Пока идёт обработка реплики, сессия заблокирована: параллельная реплика получит 409 session_busy
        (иначе две реплики получили бы одинаковый turn_no и перемешали состояние пациента)."""
        if session.user_id != user.id:
            raise access.not_found("Сессия")
        if session.status != "active":
            raise ApiError(409, "session_not_active", "Сессия завершена")
        text = text.strip()
        if not text:
            raise ApiError(422, "validation_failed", "Пустое сообщение")
        lock = f"psy:lock:{session.id}"
        if not await self.c.kv.set_nx(lock, "1", ex=180):
            raise ApiError(409, "session_busy", "Предыдущая реплика ещё обрабатывается")

        async def release() -> None:
            await self.c.kv.delete(lock)

        if client_message_id is not None and not await self.c.kv.set_nx(
                f"psy:msg:{session.id}:{client_message_id}", "1", ex=86400):
            await release()
            raise ApiError(409, "duplicate_message", "Это сообщение уже обработано")
        return self._events(session, user, text), release

    async def respond(self, session: PatientSession, user: User, text: str,
                      client_message_id: uuid.UUID | None = None) -> AsyncIterator[dict[str, Any]]:
        """События: token* -> done | ended | out_of_role | error. ApiError бросается до первого события."""
        events, release = await self.begin(session, user, text, client_message_id)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()
            await release()

    async def _events(self, session: PatientSession, user: User, text: str) -> AsyncIterator[dict[str, Any]]:
        db, c = self.db, self.c
        if text.upper() == STOP_WORD:
            await self._finish(session)
            yield {"type": "ended"}
            return

        cv = await db.get(CaseVersion, session.case_version_id)
        case = CaseModel.model_validate(cv.case_model)
        runtime = await self._load_runtime(session, case)
        history = await self._history(session.id)
        show_state = access.state_visible(user, session)

        last_patient = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), None)
        features, source, classifier_usage, classifier_tpl = await self._analyze(text, last_patient)

        triggers = detect_triggers(text, case.triggers)
        new_state, delta = update_state(runtime.state, features, triggers, case.baseline)
        mode = disclosure_mode(case, new_state, runtime.disclosed)
        patient_tpl = await self._active_template("patient")
        system_prompt = build_system_prompt(case, new_state, triggers, mode,
                                            rules_override=patient_tpl.body if patient_tpl else None)
        messages = build_messages(system_prompt, history, text)

        chunks: list[str] = []
        usage: LLMUsage | None = None
        try:
            async for item in c.llm.chat_stream(messages, temperature=0.8):
                if isinstance(item, LLMUsage):
                    usage = item
                else:
                    chunks.append(item)
                    yield {"type": "token", "text": item}
        except Exception as exc:
            yield {"type": "error", "message": f"LLM недоступна: {exc}"}
            return

        reply = clean_reply("".join(chunks))
        if reply == OUT_OF_ROLE_MARK:
            yield {"type": "out_of_role"}
            return

        student_verdict = safety.check_student_message(text)
        patient_verdict = safety.check_patient_reply(reply)
        disclosed = runtime.disclosed or mode == "allowed"
        base = runtime.turn_no
        st_turn = Turn(id=uuid.uuid4(), session_id=session.id, turn_no=base + 1, speaker="student", content=text,
                       flagged=student_verdict is not None)
        pt_turn = Turn(id=uuid.uuid4(), session_id=session.id, turn_no=base + 2, speaker="patient", content=reply,
                       flagged=patient_verdict is not None)
        db.add_all([st_turn, pt_turn])
        await db.flush()
        db.add(StateSnapshot(turn_id=pt_turn.id, trust=new_state.trust, anxiety=new_state.anxiety,
                             resistance=new_state.resistance, openness=new_state.openness,
                             extra={"features": features.model_dump(), "features_source": source, "delta": delta,
                                    "triggers": [t.id for t in triggers], "disclosed": disclosed}))
        for verdict, turn in ((student_verdict, st_turn), (patient_verdict, pt_turn)):
            if verdict:
                db.add(SafetyEvent(session_id=session.id, turn_id=turn.id, event_type=verdict.event_type,
                                   severity=verdict.severity))
        if usage:
            self._log_llm(usage, "patient", session.id, pt_turn.id, patient_tpl)
        if classifier_usage:
            self._log_llm(classifier_usage, "classifier", session.id, pt_turn.id, classifier_tpl)
        await db.commit()
        await self._save_runtime(session.id, RuntimeState(state=new_state, disclosed=disclosed, turn_no=base + 2))

        done: dict[str, Any] = {"type": "done", "reply": reply, "features_source": source}
        if show_state:
            done.update(state=new_state.model_dump(), delta=delta, triggers=[t.id for t in triggers])
        else:
            done.update(state=None, delta=None, triggers=None)
        if student_verdict:
            done["notice"] = {"code": "student_risk", "text": safety.STUDENT_RISK_NOTICE}
        yield done

    # ---------- завершение ----------
    async def end(self, session: PatientSession) -> PatientSession:
        if session.status == "active":
            await self._finish(session)
        return session

    async def _finish(self, session: PatientSession) -> None:
        session.status, session.ended_at = "ended", datetime.now(timezone.utc)
        form = (await self.db.execute(select(StudentFormulation).where(
            StudentFormulation.session_id == session.id))).scalar_one_or_none()
        if form is not None:
            cv = await self.db.get(CaseVersion, session.case_version_id)
            form.match_score = formulation_match(form.content, CaseModel.model_validate(cv.case_model))
        await self.db.commit()
        await self.c.kv.delete(runtime_key(session.id))

    # ---------- представления ----------
    async def runtime_for(self, session: PatientSession) -> RuntimeState:
        cv = await self.db.get(CaseVersion, session.case_version_id)
        return await self._load_runtime(session, CaseModel.model_validate(cv.case_model))

    async def turn_counts(self, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not session_ids:
            return {}
        rows = await self.db.execute(select(Turn.session_id, func.count(Turn.id)).where(
            Turn.session_id.in_(session_ids)).group_by(Turn.session_id))
        return {sid: n for sid, n in rows.all()}

    # ---------- внутреннее ----------
    async def _active_template(self, kind: str) -> PromptTemplate | None:
        return (await self.db.execute(select(PromptTemplate).where(
            PromptTemplate.kind == kind, PromptTemplate.is_active.is_(True)))).scalar_one_or_none()

    def _log_llm(self, usage: LLMUsage, purpose: str, session_id: uuid.UUID, turn_id: uuid.UUID | None,
                 tpl: PromptTemplate | None) -> None:
        self.db.add(LlmCall(session_id=session_id, turn_id=turn_id, prompt_id=tpl.id if tpl else None,
                            purpose=purpose, model=usage.model, tokens_in=usage.tokens_in,
                            tokens_out=usage.tokens_out, latency_ms=usage.latency_ms))

    async def _analyze(self, text: str, last_patient: str | None):
        if not self.c.settings.use_llm_classifier:
            return heuristic_features(text), "heuristic", None, None
        tpl = await self._active_template("classifier")
        try:
            raw, usage = await self.c.judge_llm.chat(
                build_classifier_messages(text, last_patient, tpl.body if tpl else None),
                temperature=0.0, json_mode=True)
            return parse_features(raw), "llm", usage, tpl
        except Exception:
            return heuristic_features(text), "heuristic", None, None

    async def _history(self, session_id: uuid.UUID) -> list[dict[str, str]]:
        rows = (await self.db.execute(select(Turn).where(Turn.session_id == session_id).order_by(
            Turn.turn_no.desc()).limit(HISTORY_TURNS))).scalars().all()
        return [{"role": "user" if t.speaker == "student" else "assistant", "content": t.content}
                for t in reversed(rows)]

    async def _last_snapshot(self, session_id: uuid.UUID) -> tuple[StateSnapshot, int] | None:
        row = (await self.db.execute(select(StateSnapshot, Turn.turn_no).join(Turn, Turn.id == StateSnapshot.turn_id)
                                     .where(Turn.session_id == session_id).order_by(Turn.turn_no.desc()).limit(1))).first()
        return (row[0], row[1]) if row else None

    async def _initial_runtime(self, session: PatientSession, case: CaseModel) -> RuntimeState:
        if session.prev_session_id is None:
            return RuntimeState(state=case.baseline)
        last = await self._last_snapshot(session.prev_session_id)
        if last is None:
            return RuntimeState(state=case.baseline)
        snap, _ = last
        return RuntimeState(state=carry_over_state(_state_of(snap), case.baseline),
                            disclosed=bool(snap.extra.get("disclosed", False)))

    async def _load_runtime(self, session: PatientSession, case: CaseModel) -> RuntimeState:
        raw = await self.c.kv.get(runtime_key(session.id))
        if raw:
            return RuntimeState.model_validate_json(raw)
        last = await self._last_snapshot(session.id)
        if last is not None:
            snap, turn_no = last
            return RuntimeState(state=_state_of(snap), disclosed=bool(snap.extra.get("disclosed", False)),
                                turn_no=turn_no)
        return await self._initial_runtime(session, case)

    async def _save_runtime(self, session_id: uuid.UUID, runtime: RuntimeState) -> None:
        await self.c.kv.set(runtime_key(session_id), runtime.model_dump_json(), ex=RUNTIME_TTL)
