"""Проверка модели случая перед публикацией: errors блокируют, warnings нет."""
from __future__ import annotations

import math
from typing import Any

from pydantic import ValidationError

from ..engine.schemas import CaseModel
from ..engine.state_engine import MAX_UP
from ..schemas import ValidationIssue, ValidationReport


def lint_case(raw: dict[str, Any], known_disorders: set[tuple[str, str]]) -> ValidationReport:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    try:
        case = CaseModel.model_validate(raw)
    except ValidationError as exc:
        for e in exc.errors():
            errors.append(ValidationIssue(path=".".join(str(p) for p in e["loc"]), message=e["msg"], code="schema"))
        return ValidationReport(valid=False, errors=errors, warnings=warnings)

    def err(path: str, msg: str, code: str) -> None:
        errors.append(ValidationIssue(path=path, message=msg, code=code))

    def warn(path: str, msg: str, code: str) -> None:
        warnings.append(ValidationIssue(path=path, message=msg, code=code))

    if not case.presenting_problem.strip():
        err("presenting_problem", "Опишите, с чем пришёл пациент", "empty_presenting_problem")
    if not case.background:
        err("background", "Нужна хотя бы одна запись истории", "empty_background")
    if not case.core_beliefs:
        err("core_beliefs", "Нужно хотя бы одно глубинное убеждение", "empty_core_beliefs")
    if not case.disorders:
        err("disorders", "Укажите хотя бы одно расстройство из МКБ-11 или DSM-5", "empty_disorders")

    seen: set[str] = set()
    for i, t in enumerate(case.triggers):
        if t.id in seen:
            err(f"triggers.{i}.id", f"Повторяющийся id триггера «{t.id}»", "duplicate_trigger_id")
        seen.add(t.id)
        kws = [k.strip() for k in t.keywords if k.strip()]
        if not kws:
            err(f"triggers.{i}.keywords", "У триггера нет ключевых слов, он никогда не сработает", "empty_keywords")
        for k in kws:
            if len(k) < 3:
                warn(f"triggers.{i}.keywords", f"Корень «{k}» слишком короткий и даст ложные срабатывания",
                     "keyword_too_short")
    if not case.triggers:
        warn("triggers", "Нет триггеров: пациент не будет реагировать на болезненные темы", "no_triggers")

    d = case.hidden_disclosure
    if d is not None:
        if not d.text.strip():
            err("hidden_disclosure.text", "Текст тайны пуст", "empty_disclosure")
        need = math.ceil(max(0.0, d.trust_threshold - case.baseline.trust) / MAX_UP)
        if d.trust_threshold > 0.95 or need > 25:
            warn("hidden_disclosure", f"Порог доверия потребует не менее {need} идеальных реплик подряд, тайна "
                                      "практически недостижима", "disclosure_unreachable")
        if case.baseline.trust >= d.trust_threshold and case.baseline.openness >= d.openness_threshold:
            warn("hidden_disclosure", "Тайна раскроется сразу: базовые шкалы уже выше порогов", "disclosure_too_easy")

    for i, dis in enumerate(case.disorders):
        if (dis.classifier, dis.code) not in known_disorders:
            warn(f"disorders.{i}", f"Кода {dis.code} ({dis.classifier}) нет в справочнике", "unknown_disorder_code")

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)
