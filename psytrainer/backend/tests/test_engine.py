import json
from pathlib import Path

import pytest


from app.engine.prompt_builder import build_system_prompt, parse_features
from app.engine.schemas import CaseModel, PatientState, StudentFeatures, Trigger
from app.engine.state_engine import (
    carry_over_state,
    detect_triggers,
    disclosure_mode,
    heuristic_features,
    update_state,
)

CASE = CaseModel.model_validate(
    json.loads((Path(__file__).resolve().parents[1] / "examples" / "dima.json").read_text(encoding="utf-8"))
)
BASE = CASE.baseline


def test_judgment_drops_trust_and_raises_resistance():
    new, delta = update_state(BASE, StudentFeatures(judgment=1.0), [], BASE)
    assert new.trust < BASE.trust
    assert new.resistance > BASE.resistance
    assert delta["trust"] < 0


def test_empathy_is_capped_per_turn():
    f = StudentFeatures(empathy=1, validation=1, reflection=1)
    new, _ = update_state(BASE, f, [], BASE)
    assert new.trust - BASE.trust <= 0.10 + 1e-9


def test_no_instant_cure():
    state = BASE
    f = StudentFeatures(empathy=1, validation=1, reflection=1, open_question=1)
    for _ in range(3):
        state, _ = update_state(state, f, [], BASE)
    assert state.trust < 0.7
    assert CASE.hidden_disclosure and disclosure_mode(CASE, state, False) != "allowed"


def test_state_stays_in_bounds():
    extreme_hi = PatientState(trust=1, anxiety=1, resistance=1, openness=1)
    extreme_lo = PatientState(trust=0, anxiety=0, resistance=0, openness=0)
    for st in (extreme_hi, extreme_lo):
        for f in (StudentFeatures(judgment=1, pressure=1), StudentFeatures(empathy=1, validation=1)):
            new, _ = update_state(st, f, CASE.triggers, BASE)
            for v in new.model_dump().values():
                assert 0.0 <= v <= 1.0


def test_trigger_hits_softer_with_high_trust():
    trig = Trigger(id="t", keywords=["x"], situation="s", automatic_thought="a", emotion="e", behavior="b", intensity=0.3)
    lo = PatientState(trust=0.1, anxiety=0.5, resistance=0.5, openness=0.3)
    hi = PatientState(trust=0.9, anxiety=0.5, resistance=0.5, openness=0.3)
    _, d_lo = update_state(lo, StudentFeatures(), [trig], lo)
    _, d_hi = update_state(hi, StudentFeatures(), [trig], hi)
    assert d_lo["anxiety"] > d_hi["anxiety"] > 0


def test_drift_returns_toward_baseline():
    far = PatientState(trust=0.9, anxiety=0.5, resistance=0.5, openness=0.5)
    new, _ = update_state(far, StudentFeatures(), [], BASE)
    assert new.trust < far.trust


def test_detect_triggers():
    hit = detect_triggers("Расскажите про вашего отца", CASE.triggers)
    assert [t.id for t in hit] == ["father"]
    assert detect_triggers("Какая сегодня погода?", CASE.triggers) == []


def test_heuristics():
    assert heuristic_features("Вам надо просто меньше переживать").advice > 0
    assert heuristic_features("Как вы себя чувствуете?").open_question > 0
    assert heuristic_features("У вас бывает бессонница?").closed_question > 0
    assert heuristic_features("Понимаю, звучит тяжело").empathy > 0


def test_carry_over_keeps_trust_more_than_anxiety():
    prev = PatientState(trust=0.8, anxiety=0.9, resistance=0.2, openness=0.7)
    new = carry_over_state(prev, BASE)
    assert abs(new.trust - prev.trust) < abs(new.anxiety - prev.anxiety)


def test_prompt_reflects_state_and_hides_secret():
    low = PatientState(trust=0.1, anxiety=0.8, resistance=0.8, openness=0.1)
    p = build_system_prompt(CASE, low, detect_triggers("про отца", CASE.triggers), "hidden")
    assert "почти не доверяешь" in p
    assert "Опять не дотянул" in p
    assert "Пока не рассказывай" in p
    assert "Сейчас ты готов поделиться" not in p


def test_prompt_allows_secret_when_trusting():
    high = PatientState(trust=0.8, anxiety=0.3, resistance=0.2, openness=0.7)
    assert disclosure_mode(CASE, high, False) == "allowed"
    assert disclosure_mode(CASE, high, True) == "none"
    p = build_system_prompt(CASE, high, [], "allowed")
    assert "Сейчас ты готов поделиться" in p


def test_parse_features():
    ok = parse_features(json.dumps({"empathy": 0.9, "judgment": 2}))
    assert ok.empathy == 0.9 and ok.judgment == 1.0
    with pytest.raises(ValueError):
        parse_features("not json")
    with pytest.raises(ValueError):
        parse_features(json.dumps({"empathy": "high"}))


# ---------- оценка, формулировка, безопасность ----------
import uuid

from app.services.assessment import parse_assessment, total_score
from app.services.formulation import formulation_match
from app.services.safety import check_patient_reply, check_student_message


def test_parse_assessment_ok_and_errors():
    t1 = uuid.uuid4()
    raw = json.dumps({"scores": [{"criterion_code": "a", "score": 4, "evidence_turn_no": 1, "comment": "ок"},
                                  {"criterion_code": "b", "score": 2.4, "evidence_turn_no": 99}], "summary": "S"})
    scores, summary = parse_assessment(raw, ["a", "b"], {1: t1})
    assert [s.score for s in scores] == [4, 2] and scores[0].evidence_turn_id == t1 and scores[1].evidence_turn_id is None
    assert summary == "S"
    for bad in ("nope", json.dumps({"scores": [{"criterion_code": "a", "score": 9}]}),
                json.dumps({"scores": [{"criterion_code": "a", "score": 3}]}), json.dumps([1])):
        with pytest.raises(ValueError):
            parse_assessment(bad, ["a", "b"], {})


def test_total_score_is_weighted_and_normalized():
    assert total_score([(4, 1), (4, 3)]) == 100.0
    assert total_score([(0, 1), (4, 1)]) == 50.0
    assert total_score([(4, 3), (0, 1)]) == 75.0
    assert total_score([]) == 0.0


def test_formulation_match_rewards_overlap():
    full = {"core_beliefs": CASE.core_beliefs, "rules": CASE.rules, "defenses": CASE.defenses, "background": CASE.background,
            "triggers": [t.situation + " " + t.automatic_thought for t in CASE.triggers]}
    assert formulation_match(full, CASE) > 0.95
    assert formulation_match({}, CASE) == 0.0
    partial = formulation_match({"core_beliefs": ["Я недостаточно хорош"]}, CASE)
    assert 0 < partial < 0.5
    assert formulation_match({"core_beliefs": ["Погода солнечная сегодня"]}, CASE) == 0.0


def test_student_risk_is_first_person_only():
    assert check_student_message("Я не хочу больше жить") is not None
    assert check_student_message("мне иногда кажется, что лучше умереть") is not None
    assert check_student_message("Я хочу умереть") is not None
    assert check_student_message("Вы думаете о самоубийстве?") is None
    assert check_student_message("Вы не хотите жить?") is None
    assert check_student_message("Расскажите, как вы справляетесь с тревогой") is None


def test_role_break_detection():
    assert check_patient_reply("Как языковая модель, я не могу этого сказать").event_type == "role_break"
    assert check_patient_reply("Ну... не знаю, типа, нормально всё") is None
