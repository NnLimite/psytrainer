from __future__ import annotations

import json

from tests.conftest import case_body, login, make_user
from tests.test_orgs import setup


async def published_case(client, api, h, **over):
    r = await client.post(f"{api}/cases", json=case_body(**over), headers=h["teacher"])
    cid = r.json()["id"]
    pub = await client.post(f"{api}/cases/{cid}/versions/1/publish", headers=h["teacher"])
    assert pub.status_code == 200, pub.text
    return cid, pub.json()["id"]


async def consent(client, api, headers, kind="transcript_processing", granted=True):
    r = await client.put(f"{api}/me/consents/{kind}", json={"granted": granted}, headers=headers)
    assert r.status_code == 200
    return r.json()


async def start(client, api, headers, version_id, **extra):
    r = await client.post(f"{api}/sessions", json={"case_version_id": version_id, **extra}, headers=headers)
    return r


async def ready(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    cid, vid = await published_case(client, api, h)
    await consent(client, api, h["student"])
    return org, teacher, student, h, cid, vid


async def test_consent_required_then_start(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    cid, vid = await published_case(client, api, h)
    r = await start(client, api, h["student"], vid)
    assert r.status_code == 409 and r.json()["code"] == "consent_required"
    c = await consent(client, api, h["student"])
    assert c["granted"] is True and c["granted_at"]
    r = await start(client, api, h["student"], vid)
    body = r.json()
    assert r.status_code == 201 and body["status"] == "active" and body["session_no"] == 1
    assert body["state"] == {"trust": 0.25, "anxiety": 0.55, "resistance": 0.55, "openness": 0.25}
    r = await client.post(f"{api}/sessions", json={"case_id": cid}, headers=h["student"])  # по case_id берётся опубликованная версия
    assert r.status_code == 201 and r.json()["case_version_id"] == vid
    assert (await client.post(f"{api}/sessions", json={}, headers=h["student"])).status_code == 422


async def test_message_updates_state_and_persists(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    llm.features = {"judgment": 1.0}
    s = (await start(client, api, h["student"], vid)).json()
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Это глупо. Расскажите про отца."}, headers=h["student"])
    body = r.json()
    assert r.status_code == 200 and body["reply"] == "Ну ладно..." and body["triggers"] == ["father"]
    assert body["features_source"] == "llm" and body["state"]["trust"] < 0.25

    turns = (await client.get(f"{api}/sessions/{s['id']}/turns", headers=h["student"])).json()["items"]
    assert [(t["turn_no"], t["speaker"]) for t in turns] == [(1, "student"), (2, "patient")]
    cur = (await client.get(f"{api}/sessions/{s['id']}/state", headers=h["student"])).json()
    assert cur["turn_no"] == 2 and cur["state"] == body["state"]
    got = (await client.get(f"{api}/sessions/{s['id']}", headers=h["student"])).json()
    assert got["turn_count"] == 2
    prompt = llm.patient_prompts[-1]
    assert "Дмитрий" in prompt and "Опять не дотянул" in prompt  # сработавший триггер попал в промпт


async def test_classifier_failure_falls_back_to_heuristics(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    llm.fail_classifier = True
    s = (await start(client, api, h["student"], vid)).json()
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Понимаю, звучит тяжело"}, headers=h["student"])
    assert r.json()["features_source"] == "heuristic" and r.json()["delta"]["trust"] > 0


async def test_streaming_sse_and_preflight_errors(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    async with client.stream("POST", f"{api}/sessions/{s['id']}/messages/stream", json={"text": "Как вы?"}, headers=h["student"]) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in [l async for l in r.aiter_lines()] if line.startswith("data: ")]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Ну ладно..."
    assert events[-1]["type"] == "done" and events[-1]["state"]["openness"] is not None

    # ошибки проверок приходят обычным HTTP-ответом, а не внутри стрима
    r = await client.post(f"{api}/sessions/{s['id']}/messages/stream", json={"text": ""}, headers=h["student"])
    assert r.status_code == 422
    other = await login(client, await make_user(container, "student", org))
    assert (await client.post(f"{api}/sessions/{s['id']}/messages/stream", json={"text": "привет"}, headers=other)).status_code == 404
    await client.post(f"{api}/sessions/{s['id']}/end", headers=h["student"])
    r = await client.post(f"{api}/sessions/{s['id']}/messages/stream", json={"text": "ещё"}, headers=h["student"])
    assert r.status_code == 409 and r.json()["code"] == "session_not_active"


async def test_llm_failure_is_502_and_nothing_persisted(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    llm.fail = True
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Здравствуйте"}, headers=h["student"])
    assert r.status_code == 502 and r.json()["code"] == "llm_unavailable"
    assert (await client.get(f"{api}/sessions/{s['id']}", headers=h["student"])).json()["turn_count"] == 0
    llm.fail = False
    assert (await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Здравствуйте"}, headers=h["student"])).status_code == 200


async def test_duplicate_client_message_and_stop_word(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    body = {"text": "Расскажите подробнее", "client_message_id": "7c1d5a52-8f4e-4a56-9d0a-1f2b3c4d5e6f"}
    assert (await client.post(f"{api}/sessions/{s['id']}/messages", json=body, headers=h["student"])).status_code == 200
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json=body, headers=h["student"])
    assert r.status_code == 409 and r.json()["code"] == "duplicate_message"
    assert (await client.get(f"{api}/sessions/{s['id']}", headers=h["student"])).json()["turn_count"] == 2
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "выход"}, headers=h["student"])
    assert r.json() == {"type": "ended"}
    assert (await client.get(f"{api}/sessions/{s['id']}", headers=h["student"])).json()["status"] == "ended"


async def test_exam_mode_hides_state_from_student_only(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    g = (await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Экзамен"}, headers=h["teacher"])).json()
    await client.put(f"{api}/groups/{g['id']}/members/{student.id}", headers=h["teacher"])
    assert (await start(client, api, h["student"], vid, mode="exam")).status_code == 422  # нужен group_id
    s = (await start(client, api, h["student"], vid, mode="exam", group_id=g["id"])).json()
    assert s["state"] is None
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Здравствуйте"}, headers=h["student"])
    assert r.json()["state"] is None and r.json()["delta"] is None and r.json()["triggers"] is None
    r = await client.get(f"{api}/sessions/{s['id']}/state", headers=h["student"])
    assert r.status_code == 403 and r.json()["code"] == "state_hidden"
    # преподаватель группы видит состояние и историю
    assert (await client.get(f"{api}/sessions/{s['id']}/state", headers=h["teacher"])).status_code == 200
    hist = (await client.get(f"{api}/sessions/{s['id']}/state/history", headers=h["teacher"])).json()
    assert len(hist) == 1 and hist[0]["features_source"] == "llm"
    assert (await client.get(f"{api}/sessions/{s['id']}/state/history", headers=h["student"])).status_code == 403
    lst = (await client.get(f"{api}/sessions", params={"group_id": g["id"]}, headers=h["teacher"])).json()["items"]
    assert [x["id"] for x in lst] == [s["id"]]
    # чужой преподаватель доступа не имеет
    stranger = await login(client, await make_user(container, "teacher", org))
    assert (await client.get(f"{api}/sessions/{s['id']}", headers=stranger)).status_code == 404


async def test_student_risk_creates_event_and_notice(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    g = (await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Г"}, headers=h["teacher"])).json()
    await client.put(f"{api}/groups/{g['id']}/members/{student.id}", headers=h["teacher"])
    s = (await start(client, api, h["student"], vid, group_id=g["id"])).json()

    ask = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Вы думаете о самоубийстве?"}, headers=h["student"])
    assert ask.json().get("notice") is None  # вопрос пациенту про риск: тренируемый навык, не срабатывание
    r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Если честно, я не хочу больше жить"}, headers=h["student"])
    assert r.json()["notice"]["code"] == "student_risk"
    events = (await client.get(f"{api}/safety-events", params={"status": "open"}, headers=h["teacher"])).json()["items"]
    assert len(events) == 1 and events[0]["event_type"] == "student_risk" and events[0]["severity"] == "high"
    assert (await client.get(f"{api}/safety-events", headers=h["student"])).status_code == 403
    r = await client.patch(f"{api}/safety-events/{events[0]['id']}", json={"status": "resolved", "note": "Поговорил"}, headers=h["teacher"])
    assert r.json()["status"] == "resolved" and r.json()["handled_by"] == str(teacher.id)
    per_session = (await client.get(f"{api}/sessions/{s['id']}/safety-events", headers=h["teacher"])).json()
    assert len(per_session) == 1
    turns = (await client.get(f"{api}/sessions/{s['id']}/turns", headers=h["student"])).json()["items"]
    assert [t["flagged"] for t in turns] == [False, False, True, False]


async def test_continue_session_carries_state(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    llm.features = {"empathy": 1.0, "validation": 1.0, "reflection": 1.0}
    s1 = (await start(client, api, h["student"], vid)).json()
    for _ in range(3):
        last = (await client.post(f"{api}/sessions/{s1['id']}/messages", json={"text": "Понимаю, это нормально"}, headers=h["student"])).json()
    r = await start(client, api, h["student"], vid, prev_session_id=s1["id"])
    assert r.status_code == 409 and r.json()["code"] == "previous_session_mismatch"  # прошлая сессия ещё идёт
    await client.post(f"{api}/sessions/{s1['id']}/end", headers=h["student"])
    s2 = (await start(client, api, h["student"], vid, prev_session_id=s1["id"])).json()
    assert s2["session_no"] == 2 and 0.25 < s2["state"]["trust"] < last["state"]["trust"]


async def test_formulation_match_after_end(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    content = {"core_beliefs": ["Я недостаточно хорош", "Я обязан быть безупречным, иначе меня отвергнут"],
               "defenses": ["юмор и ирония", "интеллектуализация"]}
    r = await client.put(f"{api}/sessions/{s['id']}/formulation", json={"content": content}, headers=h["student"])
    assert r.status_code == 200 and r.json()["match_score"] is None
    assert (await client.get(f"{api}/sessions/{s['id']}/formulation", headers=h["student"])).json()["match_score"] is None
    await client.post(f"{api}/sessions/{s['id']}/end", headers=h["student"])
    got = (await client.get(f"{api}/sessions/{s['id']}/formulation", headers=h["student"])).json()
    assert got["match_score"] is not None and got["match_score"] > 0.15
    r = await client.put(f"{api}/sessions/{s['id']}/formulation", json={"content": content}, headers=h["student"])
    assert r.status_code == 409


async def test_preview_and_draft_rules(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    r = await client.post(f"{api}/cases", json=case_body(), headers=h["teacher"])
    cid = r.json()["id"]
    draft = (await client.get(f"{api}/cases/{cid}/versions/1", headers=h["teacher"])).json()["id"]
    await consent(client, api, h["teacher"])
    await consent(client, api, h["student"])
    r = await start(client, api, h["teacher"], draft)
    assert r.status_code == 409 and r.json()["code"] == "version_not_published"
    r = await start(client, api, h["teacher"], draft, mode="preview")
    assert r.status_code == 201 and r.json()["mode"] == "preview"
    assert (await start(client, api, h["student"], draft, mode="preview")).status_code in (403, 404)
    other = await login(client, await make_user(container, "teacher", org))
    await consent(client, api, other)
    assert (await start(client, api, other, draft, mode="preview")).status_code == 404  # не автор


async def test_idempotent_session_start_and_message_rate_limit(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    hdr = {**h["student"], "Idempotency-Key": "abc"}
    a = await client.post(f"{api}/sessions", json={"case_version_id": vid}, headers=hdr)
    b = await client.post(f"{api}/sessions", json={"case_version_id": vid}, headers=hdr)
    assert a.json()["id"] == b.json()["id"]
    container.settings.rate_limit_messages_per_min = 2
    for _ in range(2):
        assert (await client.post(f"{api}/sessions/{a.json()['id']}/messages", json={"text": "привет"}, headers=h["student"])).status_code == 200
    r = await client.post(f"{api}/sessions/{a.json()['id']}/messages", json={"text": "привет"}, headers=h["student"])
    assert r.status_code == 429


async def test_patient_rules_template_override(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    admin = await login(client, await make_user(container, "platform_admin"))
    t = await client.post(f"{api}/admin/prompt-templates", json={"kind": "patient", "body": "Отвечай ровно одним словом. Язык: {language}."}, headers=admin)
    assert t.status_code == 201 and t.json()["version"] == 1
    await client.post(f"{api}/admin/prompt-templates/{t.json()['id']}/activate", headers=admin)
    s = (await start(client, api, h["student"], vid)).json()
    await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Привет"}, headers=h["student"])
    assert "Отвечай ровно одним словом. Язык: ru." in llm.patient_prompts[-1]
    assert "живой человек в кабинете" not in llm.patient_prompts[-1]


async def test_concurrent_messages_are_serialized(client, api, container, llm):
    import asyncio
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    llm.delay = 0.2
    url = f"{api}/sessions/{s['id']}/messages"
    a, b = await asyncio.gather(client.post(url, json={"text": "Первая"}, headers=h["student"]),
                                client.post(url, json={"text": "Вторая"}, headers=h["student"]))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    loser = a if a.status_code == 409 else b
    assert loser.json()["code"] == "session_busy"
    llm.delay = 0
    turns = (await client.get(f"{api}/sessions/{s['id']}/turns", headers=h["student"])).json()["items"]
    assert [t["turn_no"] for t in turns] == [1, 2]
    assert (await client.post(url, json={"text": "Третья"}, headers=h["student"])).status_code == 200  # блокировка снята


async def test_sse_heartbeat_while_model_is_thinking(client, api, container, llm, monkeypatch):
    from app.routers import sessions as sessions_router
    monkeypatch.setattr(sessions_router, "SSE_PING_SECONDS", 0.05)
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    s = (await start(client, api, h["student"], vid)).json()
    llm.delay = 0.3
    async with client.stream("POST", f"{api}/sessions/{s['id']}/messages/stream", json={"text": "Здравствуйте"}, headers=h["student"]) as r:
        lines = [l async for l in r.aiter_lines() if l]
    assert any(l == ": ping" for l in lines)
    assert json.loads(lines[-1][6:])["type"] == "done"  # пинги не мешают итоговому событию
