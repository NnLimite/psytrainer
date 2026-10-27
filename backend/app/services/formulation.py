"""Совпадение модели случая студента с эталоном. Лексическая оценка (F1 по основам слов), а не смысловая:
годится как грубый ориентир для разбора, но не как оценка."""
from __future__ import annotations

import re
from typing import Any

from ..engine.schemas import CaseModel

_TOKEN = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _bag(value: Any) -> set[str]:
    words = (w.lower().replace("ё", "е") for s in _strings(value) for w in _TOKEN.findall(s))
    return {w[:5] for w in words if len(w) >= 4}


def formulation_match(student: dict[str, Any], case: CaseModel) -> float:
    reference = {
        "core_beliefs": case.core_beliefs,
        "rules": case.rules,
        "defenses": case.defenses,
        "background": case.background,
        "triggers": [f"{t.situation} {t.automatic_thought}" for t in case.triggers],
    }
    scores: list[float] = []
    for key, ref_items in reference.items():
        ref, got = _bag(ref_items), _bag(student.get(key))
        if not ref:
            continue
        inter = len(ref & got)
        if inter == 0:
            scores.append(0.0)
            continue
        p, r = inter / len(got), inter / len(ref)
        scores.append(2 * p * r / (p + r))
    return round(sum(scores) / len(scores), 3) if scores else 0.0
