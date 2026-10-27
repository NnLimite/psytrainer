from __future__ import annotations

from tests.conftest import login, make_user
from tests.test_orgs import setup
from tests.test_sessions import ready, start

RUBRIC = {"name": "Базовые навыки", "criteria": [
    {"code": "empathy", "title": "Эмпатия", "description": "Признание чувств", "weight": 2},
    {"code": "questions", "title": "Открытые вопросы", "description": "", "weight": 1}]}


async def finished_session(client, api, h, vid, texts=("Расскажите, что вас беспокоит?",), **extra):
    s = (await start(client, api, h["student"], vid, **extra)).json()
    for t in texts:
        r = await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": t}, headers=h["student"])
        assert r.status_code == 200, r.text
    await client.post(f"{api}/sessions/{s['id']}/end", headers=h["student"])
    return s


async def run_assessment(client, api, container, headers, session_id, rubric_id):
    r = await client.post(f"{api}/sessions/{session_id}/assessments", json={"rubric_id": rubric_id}, headers=headers)
    assert r.status_code == 202, r.text
    assert r.headers["location"].endswith(f"/jobs/{r.json()['id']}")
    await container.jobs.drain()
    job = (await client.get(f"{api}/jobs/{r.json()['id']}", headers=headers)).json()
    return job


async def test_rubric_versions_and_visibility(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    r = await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["teacher"])
    assert r.status_code == 201 and r.json()["version"] == 1 and [c["code"] for c in r.json()["criteria"]] == ["empathy", "questions"]
    dup = await client.post(f"{api}/rubrics", json={"name": "x", "criteria": [{"code": "a", "title": "A"}, {"code": "a", "title": "B"}]}, headers=h["teacher"])
    assert dup.status_code == 422
    v2 = await client.post(f"{api}/rubrics/{r.json()['id']}/versions", json={**RUBRIC, "name": "Базовые навыки v2"}, headers=h["teacher"])
    assert v2.status_code == 201 and v2.json()["version"] == 2
    listed = (await client.get(f"{api}/rubrics", headers=h["student"])).json()["items"]
    assert [x["version"] for x in listed] == [2]  # в списке только свежие версии
    assert (await client.get(f"{api}/rubrics/{r.json()['id']}", headers=h["student"])).json()["version"] == 1  # старая доступна по id
    assert (await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["student"])).status_code == 403
    outsider = await login(client, await make_user(container, "teacher"))
    assert (await client.post(f"{api}/rubrics", json=RUBRIC, headers=outsider)).status_code == 422  # нет организации
    other_t = await login(client, await make_user(container, "teacher", org))
    assert (await client.post(f"{api}/rubrics/{r.json()['id']}/versions", json=RUBRIC, headers=other_t)).status_code == 403


async def test_assessment_job_end_to_end(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    rub = (await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["teacher"])).json()
    s = (await start(client, api, h["student"], vid)).json()
    await client.post(f"{api}/sessions/{s['id']}/messages", json={"text": "Расскажите, что вас беспокоит?"}, headers=h["student"])
    r = await client.post(f"{api}/sessions/{s['id']}/assessments", json={"rubric_id": rub["id"]}, headers=h["student"])
    assert r.status_code == 409 and r.json()["code"] == "session_not_ended"
    await client.post(f"{api}/sessions/{s['id']}/end", headers=h["student"])

    job = await run_assessment(client, api, container, h["student"], s["id"], rub["id"])
    assert job["status"] == "succeeded" and job["result_url"].startswith("/api/v1/assessments/")
    a = (await client.get(job["result_url"].replace("/api/v1", api), headers=h["student"])).json()
    assert a["assessor_type"] == "llm" and a["total_score"] == 75.0  # балл 3 из 4 по всем критериям
    assert {x["criterion_code"] for x in a["scores"]} == {"empathy", "questions"}
    turns = (await client.get(f"{api}/sessions/{s['id']}/turns", headers=h["student"])).json()["items"]
    assert a["scores"][0]["evidence_turn_id"] == turns[0]["id"]  # привязка к реплике студента
    # преподаватель вне группы студента доступа к сессии не имеет
    assert (await client.get(f"{api}/sessions/{s['id']}/assessments", headers=h["teacher"])).status_code == 404


async def test_teacher_edit_confirm_and_exam_visibility(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    rub = (await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["teacher"])).json()
    g = (await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Г"}, headers=h["teacher"])).json()
    await client.put(f"{api}/groups/{g['id']}/members/{student.id}", headers=h["teacher"])
    s = await finished_session(client, api, h, vid, mode="exam", group_id=g["id"])

    denied = await client.post(f"{api}/sessions/{s['id']}/assessments", json={"rubric_id": rub["id"]}, headers=h["student"])
    assert denied.status_code == 403  # в exam оценку запускает преподаватель
    job = await run_assessment(client, api, container, h["teacher"], s["id"], rub["id"])
    aid = job["result_url"].rsplit("/", 1)[1]
    assert (await client.get(f"{api}/assessments/{aid}", headers=h["student"])).status_code == 404  # до подтверждения скрыта
    assert (await client.get(f"{api}/sessions/{s['id']}/assessments", headers=h["student"])).json() == []

    bad = await client.patch(f"{api}/assessments/{aid}", json={"scores": [{"criterion_code": "nope", "score": 1}]}, headers=h["teacher"])
    assert bad.status_code == 422
    r = await client.patch(f"{api}/assessments/{aid}", json={"summary": "Правка", "scores": [{"criterion_code": "empathy", "score": 4, "comment": "Отлично"}]}, headers=h["teacher"])
    a = r.json()
    assert a["assessor_type"] == "teacher" and a["total_score"] == round((4 * 2 + 3 * 1) / 3 / 4 * 100, 1) and a["teacher_confirmed"] is False
    assert (await client.patch(f"{api}/assessments/{aid}", json={"summary": "x"}, headers=h["student"])).status_code == 403
    c = await client.post(f"{api}/assessments/{aid}/confirm", headers=h["teacher"])
    assert c.json()["teacher_confirmed"] is True
    seen = (await client.get(f"{api}/assessments/{aid}", headers=h["student"])).json()
    assert seen["summary"] == "Правка"


async def test_bad_judge_output_fails_job_without_assessment(client, api, container, llm):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    rub = (await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["teacher"])).json()
    s = await finished_session(client, api, h, vid)
    llm.bad_judge = True
    job = await run_assessment(client, api, container, h["student"], s["id"], rub["id"])
    assert job["status"] == "failed" and "судья" in job["error"] and job["result_url"] is None
    assert (await client.get(f"{api}/sessions/{s['id']}/assessments", headers=h["student"])).json() == []
    llm.bad_judge = False
    job = await run_assessment(client, api, container, h["student"], s["id"], rub["id"])  # повтор успешен
    assert job["status"] == "succeeded"


async def test_jobs_are_private(client, api, container):
    org, teacher, student, h, cid, vid = await ready(client, api, container)
    rub = (await client.post(f"{api}/rubrics", json=RUBRIC, headers=h["teacher"])).json()
    s = await finished_session(client, api, h, vid)
    job = await run_assessment(client, api, container, h["student"], s["id"], rub["id"])
    assert (await client.get(f"{api}/jobs/{job['id']}", headers=h["teacher"])).status_code == 404
    assert (await client.get(f"{api}/jobs/{job['id']}/download", headers=h["student"])).status_code == 404  # у оценки нет файла
