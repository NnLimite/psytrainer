"""Сборка промптов: роль пациента и разметчик реплик студента."""
from __future__ import annotations

import json

from .schemas import CaseModel, PatientState, StudentFeatures, Trigger
from .state_engine import DisclosureMode

STOP_WORD = "ВЫХОД"
OUT_OF_ROLE_MARK = "[[OUT_OF_ROLE]]"

_VERBOSITY = {
    "terse": "1-2 коротких предложения",
    "moderate": "2-4 предложения",
    "talkative": "3-6 предложений",
}


DEFAULT_RULES = """1. Ты живой человек в кабинете, а не ассистент. Не помогай терапевту, не давай ему советов и не объясняй психологические приёмы.
2. Не называй диагнозы и профессиональные термины, если пациент не мог их знать. Не говори «у меня убеждение...» или «моя защита...».
3. Не «выздоравливай» за одну сессию: инсайты редкие, маленькие и неуверенные. Не соглашайся со всем подряд.
4. Отвечай только речью пациента от первого лица. Допустимы короткие паузы вроде (...) и не более.
5. Если терапевт напишет «{stop_word}», ответь ровно: {out_of_role}
6. Язык ответов: {language}."""


def render_rules(override: str | None, language: str) -> str:
    """Подставляет плейсхолдеры {stop_word}, {out_of_role}, {language} в правила (активный шаблон или встроенные)."""
    return (override or DEFAULT_RULES).replace("{stop_word}", STOP_WORD) \
        .replace("{out_of_role}", OUT_OF_ROLE_MARK).replace("{language}", language)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (нет данных)"


def describe_state(state: PatientState) -> list[str]:
    """Превращает числа в понятные модели указания о поведении."""
    lines: list[str] = []

    if state.trust < 0.25:
        lines.append("Ты почти не доверяешь терапевту: отвечаешь коротко и осторожно, проверяешь его реакции, избегаешь личных подробностей.")
    elif state.trust < 0.55:
        lines.append("Ты присматриваешься к терапевту: делишься поверхностным, о личном говоришь с оговорками.")
    elif state.trust < 0.8:
        lines.append("Ты начинаешь доверять: можешь рассказать о неприятном, но тебе неловко или стыдно.")
    else:
        lines.append("Ты доверяешь терапевту: способен говорить о сокровенном, хотя это даётся нелегко.")

    if state.anxiety > 0.7:
        lines.append("Тревога сильная: речь сбивчивая, ты повторяешься, перескакиваешь с мысли на мысль, возможны паузы (...).")
    elif state.anxiety > 0.4:
        lines.append("Тревога заметная: ты напряжён, торопишься с ответами, иногда сам себя поправляешь.")
    else:
        lines.append("Тревога невысокая: говоришь спокойнее и ровнее.")

    if state.resistance > 0.65:
        lines.append("Ты сопротивляешься: возражаешь, обесцениваешь, шутишь или уводишь разговор в сторону.")
    elif state.resistance > 0.35:
        lines.append("Ты сомневаешься, что разговор поможет, и можешь возразить.")
    else:
        lines.append("Ты готов работать вместе с терапевтом.")

    if state.openness < 0.3:
        lines.append("Отвечаешь на вопросы минимально и не развиваешь тему.")
    elif state.openness < 0.6:
        lines.append("Отвечаешь по существу, но сам инициативу не проявляешь.")
    else:
        lines.append("Можешь сам развивать тему и приводить примеры из жизни.")

    return lines


def build_system_prompt(
    case: CaseModel,
    state: PatientState,
    active_triggers: list[Trigger],
    disclosure: DisclosureMode = "none",
    rules_override: str | None = None,
) -> str:
    style = case.communication_style
    parts: list[str] = [
        "Ты играешь роль пациента на тренировочной сессии психотерапии. "
        "Собеседник - студент-психолог, который учится вести консультацию.",
        "",
        "## Кто ты",
        f"{case.name}, {case.age}, {case.gender}. {case.occupation}".strip(),
        f"С чем пришёл: {case.presenting_problem}",
        "",
        "## Твоя история (ты это знаешь, но не пересказываешь целиком)",
        _bullets(case.background),
        "",
        "## Что ты думаешь о себе и мире (проявляется в поведении, вслух так не говоришь)",
        "Глубинные убеждения:",
        _bullets(case.core_beliefs),
        "Правила, по которым живёшь:",
        _bullets(case.rules),
        "",
        "## Как ты защищаешься",
        _bullets(case.defenses),
        "",
        "## Как ты говоришь",
        f"Тон: {style.tone or 'обычный'}.",
        f"Длина реплики: {_VERBOSITY[style.verbosity]}.",
        "Особенности речи:",
        _bullets(style.quirks),
        "",
        "## Чего ты хочешь и что тебе помогает",
        "Цели:",
        _bullets(case.goals),
        "Сильные стороны:",
        _bullets(case.strengths),
        "",
        "## Твоё состояние прямо сейчас (не озвучивай, просто веди себя так)",
        _bullets(describe_state(state)),
    ]

    if active_triggers:
        parts += ["", "## Терапевт только что задел болезненную тему"]
        for t in active_triggers:
            parts += [
                f"- Тема: {t.situation}",
                f"  Автоматическая мысль: «{t.automatic_thought}»",
                f"  Эмоция: {t.emotion}",
                f"  Как ты реагируешь: {t.behavior}",
            ]

    if case.hidden_disclosure is not None:
        if disclosure == "hidden":
            parts += [
                "",
                "## Тайна",
                f"Ты скрываешь: {case.hidden_disclosure.text} "
                "Пока не рассказывай об этом и не намекай прямо. Если спросят в лоб - уходи от ответа.",
            ]
        elif disclosure == "allowed":
            parts += [
                "",
                "## Ты решился рассказать",
                f"Сейчас ты готов поделиться: {case.hidden_disclosure.text} "
                "Скажи это своими словами, с колебанием, в этой реплике или в следующей.",
            ]

    parts += ["", "## Жёсткие правила", render_rules(rules_override, case.language)]
    return "\n".join(parts)


def build_messages(
    system_prompt: str,
    history: list[dict[str, str]],
    student_text: str,
) -> list[dict[str, str]]:
    """history: [{'role': 'user'|'assistant', 'content': ...}], user = студент, assistant = пациент."""
    return [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": student_text},
    ]


# --- Разметчик реплик студента ---------------------------------------------------------

_FEATURE_HELP = {
    "empathy": "сочувствие, признание тяжести переживаний",
    "reflection": "пересказ слов пациента своими словами",
    "validation": "нормализация, «ваши чувства понятны»",
    "open_question": "открытый вопрос, приглашающий рассказать",
    "closed_question": "закрытый вопрос (да/нет, короткий ответ)",
    "judgment": "осуждение, критика, обесценивание",
    "advice": "прямой совет «вам надо...»",
    "premature_reassurance": "преждевременное успокоение «всё будет хорошо»",
    "early_interpretation": "интерпретация «на самом деле вы...» без основы",
    "pressure": "давление, требование отвечать",
}


DEFAULT_CLASSIFIER = (
    "Ты разметчик реплик студента-психолога на тренировочной консультации. "
    "Оцени, насколько реплика содержит каждый приём, числом от 0 до 1. "
    "Верни ТОЛЬКО JSON-объект со всеми ключами, без пояснений.\nКлючи:\n{keys}"
)


def build_classifier_messages(student_text: str, last_patient_text: str | None,
                              template: str | None = None) -> list[dict[str, str]]:
    keys = "\n".join(f'- "{k}": {v}' for k, v in _FEATURE_HELP.items())
    system = (template or DEFAULT_CLASSIFIER).replace("{keys}", keys)
    user = f"Последняя реплика пациента: {last_patient_text or '(начало сессии)'}\nРеплика студента: {student_text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_features(raw: str) -> StudentFeatures:
    """Разбирает ответ разметчика. Бросает ValueError, если ответ непригоден."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("classifier returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("classifier JSON must be an object")
    known = StudentFeatures.model_fields.keys()
    cleaned: dict[str, float] = {}
    for key in known:
        value = data.get(key, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"bad value for {key}")
        cleaned[key] = max(0.0, min(1.0, float(value)))
    return StudentFeatures(**cleaned)
