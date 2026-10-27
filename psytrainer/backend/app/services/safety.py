"""Простые правила безопасности. Это первая линия защиты, а не клиническая оценка риска: все срабатывания проверяет человек."""
from __future__ import annotations

import re
from dataclasses import dataclass

I = re.IGNORECASE

# Только высказывания от первого лица: вопрос студента пациенту («Вы думаете о самоубийстве?») не срабатывает,
# потому что оценка риска у пациента входит в тренируемые навыки.
_STUDENT_RISK = [re.compile(p, I) for p in (
    r"\bя\b[^.?!]{0,40}(хочу|собираюсь|думаю)[^.?!]{0,25}(умереть|покончить с собой|убить себя|свести счёты)",
    r"\b(не хочу|больше не хочу|не могу)\b[^.?!]{0,12}\bжить\b",
    r"\bмне\b[^.?!]{0,40}(не хочется жить|незачем жить|лучше умереть)",
    r"\bя\b[^.?!]{0,30}(навредить|причинить вред) себе",
)]
_ROLE_BREAK = re.compile(r"(как (языковая модель|ии|искусственный интеллект)|я (всего лишь )?(языковая модель|ии\b|искусственный интеллект))", I)

STUDENT_RISK_NOTICE = (
    "Похоже, вам сейчас тяжело. Эта симуляция не заменяет помощь. Остановите сессию и обратитесь к человеку, "
    "которому доверяете, к преподавателю или в экстренную службу вашего региона."
)


@dataclass
class Verdict:
    event_type: str
    severity: str


def check_student_message(text: str) -> Verdict | None:
    if any(p.search(text) for p in _STUDENT_RISK):
        return Verdict("student_risk", "high")
    return None


def check_patient_reply(text: str) -> Verdict | None:
    if _ROLE_BREAK.search(text):
        return Verdict("role_break", "low")
    return None
