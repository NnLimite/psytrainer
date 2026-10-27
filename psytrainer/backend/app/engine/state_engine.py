"""Движок состояния пациента.

Не использует LLM: правила детерминированные, поэтому их легко тестировать
и объяснять студентам и преподавателям. LLM нужен только чтобы разметить
реплику студента (см. prompt_builder.build_classifier_messages), а при сбое
работает эвристика heuristic_features.
"""
from __future__ import annotations

import re
from typing import Literal

from .schemas import DIMS, CaseModel, PatientState, StudentFeatures, Trigger

# Вклад каждого приёма студента в шкалы (при значении признака = 1.0).
EFFECTS: dict[str, dict[str, float]] = {
    "empathy": {"trust": 0.08, "resistance": -0.04, "anxiety": -0.03},
    "reflection": {"trust": 0.06, "openness": 0.06},
    "validation": {"trust": 0.07, "anxiety": -0.04, "resistance": -0.03},
    "open_question": {"openness": 0.05},
    "closed_question": {"openness": -0.02},
    "judgment": {"trust": -0.15, "resistance": 0.15, "openness": -0.10, "anxiety": 0.05},
    "advice": {"trust": -0.03, "resistance": 0.08},
    "premature_reassurance": {"trust": -0.04, "resistance": 0.04},
    "early_interpretation": {"trust": -0.03, "resistance": 0.10, "anxiety": 0.05},
    "pressure": {"trust": -0.08, "resistance": 0.12, "anxiety": 0.12},
}
# Эти приёмы сильнее раздражают, пока доверия мало.
TRUST_SENSITIVE = {"advice", "premature_reassurance", "early_interpretation"}

MAX_UP = 0.10  # за одну реплику шкала растёт не больше (нет «мгновенного излечения»)
MAX_DOWN = 0.20  # а падает быстрее: доверие легко потерять
NEGATIVE_TRUST_AMPLIFIER = 1.3
DRIFT = 0.05  # возврат к базовому состоянию, доля за ход

# Сколько состояния сохраняется между сессиями.
CARRY_KEEP = {"trust": 0.7, "anxiety": 0.4, "resistance": 0.4, "openness": 0.4}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def update_state(
    state: PatientState,
    features: StudentFeatures,
    triggers: list[Trigger],
    baseline: PatientState,
) -> tuple[PatientState, dict[str, float]]:
    """Возвращает (новое состояние, фактическое изменение по шкалам)."""
    delta = dict.fromkeys(DIMS, 0.0)

    for name, value in features.model_dump().items():
        if value <= 0:
            continue
        scale = (1.5 - state.trust) if name in TRUST_SENSITIVE else 1.0
        for dim, weight in EFFECTS.get(name, {}).items():
            delta[dim] += weight * value * scale

    for trig in triggers:
        # Чем больше доверия, тем мягче задевает болезненная тема.
        hit = trig.intensity * (1.0 - 0.5 * state.trust)
        delta["anxiety"] += 0.25 * hit
        delta["resistance"] += 0.12 * hit
        delta["openness"] -= 0.10 * hit

    if delta["trust"] < 0:
        delta["trust"] *= NEGATIVE_TRUST_AMPLIFIER

    for dim in DIMS:
        delta[dim] = max(-MAX_DOWN, min(MAX_UP, delta[dim]))

    new = {
        dim: _clamp(getattr(state, dim) + delta[dim] + DRIFT * (getattr(baseline, dim) - getattr(state, dim)))
        for dim in DIMS
    }

    # Открытость подтягивается к тому, что «позволяют» остальные шкалы.
    target_open = 0.6 * new["trust"] + 0.4 * (1 - new["resistance"]) - 0.2 * new["anxiety"]
    new["openness"] = _clamp(new["openness"] + 0.25 * (target_open - new["openness"]))

    new_state = PatientState(**new)
    applied = {dim: round(getattr(new_state, dim) - getattr(state, dim), 4) for dim in DIMS}
    return new_state, applied


def carry_over_state(prev: PatientState, baseline: PatientState) -> PatientState:
    """Состояние в начале следующей сессии: доверие сохраняется лучше остальных шкал."""
    values = {
        dim: _clamp(getattr(baseline, dim) + CARRY_KEEP[dim] * (getattr(prev, dim) - getattr(baseline, dim)))
        for dim in DIMS
    }
    return PatientState(**values)


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def detect_triggers(text: str, triggers: list[Trigger]) -> list[Trigger]:
    """Ищет в реплике корни слов. Пишите keywords как корни/основы: «отц», а не только «отец»,
    потому что в русском корень меняется («отец» -> «отца»)."""
    low = _norm(text)
    return [t for t in triggers if any(_norm(k) in low for k in t.keywords)]


DisclosureMode = Literal["none", "hidden", "allowed"]


def disclosure_mode(case: CaseModel, state: PatientState, already_disclosed: bool) -> DisclosureMode:
    """none - раскрывать нечего или уже раскрыто; hidden - прячет; allowed - готов рассказать."""
    d = case.hidden_disclosure
    if d is None or already_disclosed:
        return "none"
    if state.trust >= d.trust_threshold and state.openness >= d.openness_threshold:
        return "allowed"
    return "hidden"


# --- Запасной вариант, если LLM-разметчик недоступен или вернул мусор -------------------

_I = re.IGNORECASE
_PATTERNS: dict[str, re.Pattern[str]] = {
    "empathy": re.compile(r"(понимаю|звучит .{0,12}(тяжело|непросто|больно)|должно быть .{0,10}(тяжело|непросто)|мне жаль)", _I),
    "validation": re.compile(r"(это нормально|имеете право|ваши чувства|неудивительно)", _I),
    "reflection": re.compile(r"(то есть вы|вы говорите|если я правильно понимаю|вы чувствуете)", _I),
    "judgment": re.compile(r"(глупо|нелепо|неправильно|сами виноваты|как можно так|вечно вы)", _I),
    "advice": re.compile(r"(вам (надо|нужно|стоит|следует)|попробуйте|советую|вы должны)", _I),
    "premature_reassurance": re.compile(r"(всё будет хорошо|не переживайте|не волнуйтесь|ничего страшного|это ерунда)", _I),
    "early_interpretation": re.compile(r"(на самом деле вы|это потому,? что вы|ваша проблема в том|вы просто)", _I),
    "pressure": re.compile(r"(отвечайте|вы обязаны|почему вы молчите|скажите прямо|ну же)", _I),
}
_OPEN_START = re.compile(r"^\s*(как|что|расскажите|поделитесь|каково|о чём|чем|когда именно)", _I)
_CLOSED_START = re.compile(r"^\s*(вы|у вас|был ли|бывает ли|есть ли|правда ли|это так)\b", _I)


def heuristic_features(text: str) -> StudentFeatures:
    values: dict[str, float] = {name: 0.7 for name, pat in _PATTERNS.items() if pat.search(text)}
    if "?" in text or "расскажите" in text.lower():
        if _OPEN_START.search(text) or "расскажите" in text.lower():
            values["open_question"] = 0.8
        elif _CLOSED_START.search(text):
            values["closed_question"] = 0.7
    return StudentFeatures(**values)
