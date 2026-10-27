"""Схемы модуля «модель случая».

CaseModel      - «карточка персонажа»: кто пациент и почему он так реагирует.
PatientState   - «шкалы настроения»: меняются после каждой реплики студента.
StudentFeatures - разметка реплики студента (эмпатия, суждение, совет...).
RuntimeState   - то, что хранится в Redis между репликами.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DIMS = ("trust", "anxiety", "resistance", "openness")


class PatientState(BaseModel):
    trust: float = Field(0.3, ge=0.0, le=1.0)
    anxiety: float = Field(0.5, ge=0.0, le=1.0)
    resistance: float = Field(0.5, ge=0.0, le=1.0)
    openness: float = Field(0.3, ge=0.0, le=1.0)


class Disorder(BaseModel):
    classifier: Literal["ICD-11", "DSM-5"]
    code: str
    name: str
    role: Literal["primary", "comorbid"] = "primary"


class Trigger(BaseModel):
    """Тема, которая «задевает» пациента: ситуация -> мысль -> эмоция -> поведение."""

    id: str
    keywords: list[str]  # корни слов для быстрого поиска в реплике студента
    situation: str
    automatic_thought: str
    emotion: str
    behavior: str
    intensity: float = Field(0.5, ge=0.0, le=1.0)


class CommunicationStyle(BaseModel):
    verbosity: Literal["terse", "moderate", "talkative"] = "moderate"
    tone: str = ""
    quirks: list[str] = Field(default_factory=list)


class HiddenDisclosure(BaseModel):
    """То, что пациент расскажет, только когда достаточно доверится."""

    text: str
    trust_threshold: float = Field(0.7, ge=0.0, le=1.0)
    openness_threshold: float = Field(0.5, ge=0.0, le=1.0)


class CaseModel(BaseModel):
    title: str
    name: str
    age: int
    gender: str
    occupation: str = ""
    language: str = "ru"
    presenting_problem: str
    background: list[str]
    core_beliefs: list[str]
    rules: list[str]  # промежуточные убеждения: «если..., то...»
    triggers: list[Trigger]
    defenses: list[str]
    communication_style: CommunicationStyle
    goals: list[str]
    strengths: list[str]
    disorders: list[Disorder]
    baseline: PatientState  # состояние в начале первой сессии
    hidden_disclosure: HiddenDisclosure | None = None


class StudentFeatures(BaseModel):
    """Насколько реплика студента содержит каждый приём (0..1)."""

    empathy: float = Field(0.0, ge=0.0, le=1.0)
    reflection: float = Field(0.0, ge=0.0, le=1.0)
    validation: float = Field(0.0, ge=0.0, le=1.0)
    open_question: float = Field(0.0, ge=0.0, le=1.0)
    closed_question: float = Field(0.0, ge=0.0, le=1.0)
    judgment: float = Field(0.0, ge=0.0, le=1.0)
    advice: float = Field(0.0, ge=0.0, le=1.0)
    premature_reassurance: float = Field(0.0, ge=0.0, le=1.0)
    early_interpretation: float = Field(0.0, ge=0.0, le=1.0)
    pressure: float = Field(0.0, ge=0.0, le=1.0)


class RuntimeState(BaseModel):
    state: PatientState
    disclosed: bool = False
    turn_no: int = 0
