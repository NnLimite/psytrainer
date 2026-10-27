"""Обработчики фоновых задач: оценка сессии, отчёт, выгрузка данных, удаление данных."""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..container import Container
from ..jobs import JobResult
from ..models import (
    Assessment, Consent, CriterionScore, GroupMember, Invitation, Job, LlmCall, OneTimeToken,
    PatientSession, PromptTemplate, RefreshToken, Rubric, RubricCriterion, SafetyEvent, StateSnapshot,
    StudentFormulation, Turn, User,
)
from . import analytics
from .assessment import DEFAULT_JUDGE, parse_assessment, render_rubric, render_transcript, total_score
from .sessions import runtime_key

API = "/api/v1"


# ---------- оценка ----------
async def run_assessment(c: Container, db: AsyncSession, job: Job) -> JobResult:
    p = job.params
    session = await db.get(PatientSession, uuid.UUID(p["session_id"]))
    rubric = await db.get(Rubric, uuid.UUID(p["rubric_id"]))
    criteria = (await db.execute(select(RubricCriterion).where(RubricCriterion.rubric_id == rubric.id).order_by(
        RubricCriterion.position))).scalars().all()
    turns = (await db.execute(select(Turn).where(Turn.session_id == session.id).order_by(Turn.turn_no))).scalars().all()
    if not any(t.speaker == "student" for t in turns):
        raise ValueError("в сессии нет реплик студента")
    tpl = (await db.execute(select(PromptTemplate).where(PromptTemplate.kind == "judge",
                                                          PromptTemplate.is_active.is_(True)))).scalar_one_or_none()
    system = (tpl.body if tpl else DEFAULT_JUDGE).replace("{rubric}", render_rubric(list(criteria)))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": render_transcript(list(turns))}]
    codes = [cr.code for cr in criteria]
    student_turns = {t.turn_no: t.id for t in turns if t.speaker == "student"}

    parsed = summary = None
    last_error: Exception | None = None
    for attempt in range(2):  # одна повторная попытка с напоминанием про формат
        raw, usage = await c.judge_llm.chat(messages, temperature=0.0, json_mode=True)
        db.add(LlmCall(session_id=session.id, prompt_id=tpl.id if tpl else None, purpose="judge", model=usage.model,
                       tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, latency_ms=usage.latency_ms))
        try:
            parsed, summary = parse_assessment(raw, codes, student_turns)
            break
        except ValueError as exc:
            last_error = exc
            messages = messages + [{"role": "assistant", "content": raw},
                                   {"role": "user", "content": f"Ответ непригоден ({exc}). Верни только корректный JSON."}]
    if parsed is None:
        raise ValueError(f"судья не вернул корректную оценку: {last_error}")

    weights = {cr.code: cr.weight for cr in criteria}
    a = Assessment(session_id=session.id, rubric_id=rubric.id, assessor_type="llm", summary=summary or "",
                   total_score=total_score([(s.score, weights[s.code]) for s in parsed]))
    db.add(a)
    await db.flush()
    for s in parsed:
        db.add(CriterionScore(assessment_id=a.id, criterion_code=s.code, score=s.score,
                              evidence_turn_id=s.evidence_turn_id, comment=s.comment))
    await db.commit()
    return JobResult(ref=f"{API}/assessments/{a.id}")


# ---------- отчёты ----------
def _d(v: Any) -> date | None:
    return date.fromisoformat(v) if isinstance(v, str) and v else None


def report_rows(rtype: str, data: dict[str, Any]) -> list[list[Any]]:
    skills = [["Код", "Навык", "Средний балл"] + (["Динамика за 30 дней"] if rtype == "student_progress" else [])]
    for s in data.get("skills", []):
        skills.append([s["code"], s["title"], s["avg_score"]] + ([s.get("delta_30d")] if rtype == "student_progress" else []))
    if rtype == "group_summary":
        head = [["Группа", str(data["group_id"])], ["Сессий", data["sessions_count"]],
                ["Активных студентов", data["active_students"]], ["Средний балл (0-100)", data["avg_total_score"]],
                ["Открытых событий безопасности", data["open_safety_events"]]]
    elif rtype == "student_progress":
        head = [["Завершено сессий", data["sessions_completed"]], ["Средний балл (0-100)", data["avg_total_score"]]]
    else:
        head = [["Кейс", str(data["case_id"])], ["Сессий", data["sessions_count"]],
                ["Средняя длительность, мин", data["avg_duration_min"]], ["Среднее итоговое доверие", data["avg_final_trust"]],
                ["Доля раскрытых тайн", data["disclosure_rate"]]]
    return head + [[]] + skills


async def build_report_data(db: AsyncSession, rtype: str, params: dict[str, Any]) -> dict[str, Any]:
    if rtype == "group_summary":
        return await analytics.group_summary(db, uuid.UUID(params["group_id"]), _d(params.get("from")), _d(params.get("to")))
    if rtype == "student_progress":
        return await analytics.student_progress(db, uuid.UUID(params["user_id"]))
    return await analytics.case_usage(db, uuid.UUID(params["case_id"]))


async def run_report(c: Container, db: AsyncSession, job: Job) -> JobResult:
    rtype, fmt = job.params["type"], job.params["format"]
    rows = report_rows(rtype, await build_report_data(db, rtype, job.params["params"]))
    out_dir = c.storage / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{job.id}.{fmt}"
    if fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        path.write_text(buf.getvalue(), encoding="utf-8-sig")  # BOM, чтобы Excel открыл кириллицу
    elif fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        for r in rows:
            ws.append([str(x) if isinstance(x, uuid.UUID) else x for x in r])
        wb.save(path)
    else:
        raise ValueError(f"формат {fmt} не поддерживается")
    return JobResult(ref=f"{API}/jobs/{job.id}/download", file=str(path))


# ---------- выгрузка данных пользователя ----------
async def run_export(c: Container, db: AsyncSession, job: Job) -> JobResult:
    user = await db.get(User, job.user_id)

    def row(o: Any, *cols: str) -> dict[str, Any]:
        return {k: getattr(o, k) for k in cols}

    sessions = (await db.execute(select(PatientSession).where(PatientSession.user_id == user.id))).scalars().all()
    payload: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc),
        "user": row(user, "id", "email", "full_name", "role", "locale", "created_at"),
        "consents": [row(x, "kind", "granted", "granted_at", "revoked_at") for x in (await db.execute(
            select(Consent).where(Consent.user_id == user.id))).scalars().all()],
        "sessions": [],
    }
    for s in sessions:
        turns = (await db.execute(select(Turn).where(Turn.session_id == s.id).order_by(Turn.turn_no))).scalars().all()
        form = (await db.execute(select(StudentFormulation).where(StudentFormulation.session_id == s.id))).scalar_one_or_none()
        assess = (await db.execute(select(Assessment).where(Assessment.session_id == s.id))).scalars().all()
        scores = {a.id: (await db.execute(select(CriterionScore).where(CriterionScore.assessment_id == a.id))).scalars().all()
                  for a in assess}
        payload["sessions"].append({
            **row(s, "id", "case_version_id", "mode", "status", "session_no", "started_at", "ended_at"),
            "turns": [row(t, "turn_no", "speaker", "content", "created_at") for t in turns],
            "formulation": row(form, "content", "match_score", "submitted_at") if form else None,
            "assessments": [{**row(a, "id", "total_score", "summary", "teacher_confirmed", "created_at"),
                             "scores": [row(x, "criterion_code", "score", "comment") for x in scores[a.id]]} for a in assess],
        })
    out_dir = c.storage / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{job.id}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return JobResult(ref=f"{API}/jobs/{job.id}/download", file=str(path))


# ---------- удаление данных ----------
async def run_erasure(c: Container, db: AsyncSession, job: Job) -> JobResult:
    """Удаляет транскрипты, состояние и формулировки; оценки обезличиваются; учётная запись становится «надгробием»."""
    uid = job.user_id
    session_ids = list((await db.execute(select(PatientSession.id).where(PatientSession.user_id == uid))).scalars().all())
    for sid in session_ids:
        turn_ids = list((await db.execute(select(Turn.id).where(Turn.session_id == sid))).scalars().all())
        if turn_ids:
            await db.execute(delete(StateSnapshot).where(StateSnapshot.turn_id.in_(turn_ids)))
            await db.execute(update(SafetyEvent).where(SafetyEvent.turn_id.in_(turn_ids)).values(turn_id=None, note=None))
            await db.execute(update(CriterionScore).where(CriterionScore.evidence_turn_id.in_(turn_ids)).values(
                evidence_turn_id=None))
            await db.execute(update(LlmCall).where(LlmCall.turn_id.in_(turn_ids)).values(turn_id=None))
            await db.execute(delete(Turn).where(Turn.id.in_(turn_ids)))
        await db.execute(delete(StudentFormulation).where(StudentFormulation.session_id == sid))
        a_ids = list((await db.execute(select(Assessment.id).where(Assessment.session_id == sid))).scalars().all())
        if a_ids:
            await db.execute(update(Assessment).where(Assessment.id.in_(a_ids)).values(summary=""))
            await db.execute(update(CriterionScore).where(CriterionScore.assessment_id.in_(a_ids)).values(comment=None))
        await c.kv.delete(runtime_key(sid))
    user = await db.get(User, uid)
    await db.execute(delete(Consent).where(Consent.user_id == uid))
    await db.execute(delete(GroupMember).where(GroupMember.user_id == uid))
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == uid))
    await db.execute(delete(OneTimeToken).where(OneTimeToken.user_id == uid))
    await db.execute(delete(Invitation).where(Invitation.email == user.email))
    user.email, user.full_name, user.password_hash = f"deleted-{uid}@invalid", "Удалённый пользователь", None
    user.is_active, user.deleted_at = False, datetime.now(timezone.utc)
    await db.commit()
    return JobResult()


HANDLERS = {"assessment": run_assessment, "report": run_report, "data_export": run_export, "erasure": run_erasure}
