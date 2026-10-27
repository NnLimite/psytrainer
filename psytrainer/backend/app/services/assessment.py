"""Оценка сессии LLM-судьёй по рубрике: разбор транскрипта, проверка ответа, расчёт итогового балла."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from ..models import RubricCriterion, Turn

MAX_SCORE = 4

DEFAULT_JUDGE = """Ты опытный супервизор по консультативной психологии. Оцени работу студента-психолога в транскрипте
тренировочной сессии с виртуальным пациентом.

Шкала по каждому критерию, целое число от 0 до 4:
0 - приём отсутствует или вредит, 1 - слабо, 2 - удовлетворительно, 3 - хорошо, 4 - отлично.
Оценивай только реплики студента. Для каждого критерия укажи номер реплики студента, которая лучше всего
подтверждает оценку (evidence_turn_no), и короткий комментарий на русском.

Критерии:
{rubric}

Верни ТОЛЬКО JSON без пояснений:
{"scores": [{"criterion_code": "...", "score": 0, "evidence_turn_no": 1, "comment": "..."}], "summary": "2-3 предложения"}"""


@dataclass
class ParsedScore:
    code: str
    score: int
    evidence_turn_id: uuid.UUID | None
    comment: str | None


def render_rubric(criteria: list[RubricCriterion]) -> str:
    return "\n".join(f"- {c.code}: {c.title}. {c.description}".strip() for c in criteria)


def render_transcript(turns: list[Turn]) -> str:
    return "\n".join(f"[{t.turn_no}] {'Студент' if t.speaker == 'student' else 'Пациент'}: {t.content}" for t in turns)


def parse_assessment(raw: str, codes: list[str], student_turns: dict[int, uuid.UUID]) -> tuple[list[ParsedScore], str]:
    """Бросает ValueError, если ответ судьи непригоден (нет критерия, балл вне шкалы, не JSON)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("судья вернул не JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("scores"), list):
        raise ValueError("в ответе судьи нет списка scores")
    by_code: dict[str, ParsedScore] = {}
    for item in data["scores"]:
        if not isinstance(item, dict) or item.get("criterion_code") not in codes:
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= MAX_SCORE:
            raise ValueError(f"балл вне шкалы 0-{MAX_SCORE} для {item.get('criterion_code')}")
        ev = item.get("evidence_turn_no")
        comment = item.get("comment")
        by_code[item["criterion_code"]] = ParsedScore(
            item["criterion_code"], int(round(score)), student_turns.get(ev) if isinstance(ev, int) else None,
            comment if isinstance(comment, str) else None)
    missing = [c for c in codes if c not in by_code]
    if missing:
        raise ValueError(f"нет оценки для критериев: {', '.join(missing)}")
    summary = data.get("summary")
    return [by_code[c] for c in codes], summary if isinstance(summary, str) else ""


def total_score(scores: list[tuple[int, float]]) -> float:
    """Взвешенное среднее баллов, приведённое к шкале 0-100. scores: [(балл, вес)]."""
    weights = sum(w for _, w in scores)
    return round(sum(s * w for s, w in scores) / weights / MAX_SCORE * 100, 1) if weights else 0.0
