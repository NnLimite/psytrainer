from __future__ import annotations

import json

from tests.conftest import case_body, login, make_org, make_user
from tests.test_orgs import setup


async def create(client, api, h, **over):
    r = await client.post(f"{api}/cases", json=case_body(**over), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def test_case_lifecycle_etag_and_publish(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    case = await create(client, api, h["teacher"])
    cid = case["id"]
    assert case["latest_version_no"] == 1 and case["published_version_no"] is None
    assert {d["code"] for d in case["disorders"]} == {"6B00", "F41.1"}

    v = await client.get(f"{api}/cases/{cid}/versions/1", headers=h["teacher"])
    etag = v.headers["etag"]
    model = v.json()["case_model"]
    model["presenting_problem"] = "Новое описание"
    assert (await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers=h["teacher"])).status_code == 412
    r = await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers={**h["teacher"], "If-Match": etag})
    assert r.status_code == 200 and r.headers["etag"] != etag
    stale = await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers={**h["teacher"], "If-Match": etag})
    assert stale.status_code == 412 and stale.json()["code"] == "precondition_failed"

    # публикация с ошибками блокируется
    bad = json.loads(json.dumps(model)); bad["disorders"] = []
    await client.put(f"{api}/cases/{cid}/versions/1", json=bad, headers={**h["teacher"], "If-Match": r.headers["etag"]})
    rep = (await client.post(f"{api}/cases/{cid}/versions/1/validate", headers=h["teacher"])).json()
    assert rep["valid"] is False and rep["errors"][0]["code"] == "empty_disorders"
    pub = await client.post(f"{api}/cases/{cid}/versions/1/publish", headers=h["teacher"])
    assert pub.status_code == 422 and pub.json()["errors"][0]["field"] == "disorders"

    cur = await client.get(f"{api}/cases/{cid}/versions/1", headers=h["teacher"])
    await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers={**h["teacher"], "If-Match": cur.headers["etag"]})
    pub = await client.post(f"{api}/cases/{cid}/versions/1/publish", headers=h["teacher"])
    assert pub.status_code == 200 and pub.json()["status"] == "published"
    # опубликованная версия неизменяема
    cur = await client.get(f"{api}/cases/{cid}/versions/1", headers=h["teacher"])
    r = await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers={**h["teacher"], "If-Match": cur.headers["etag"]})
    assert r.status_code == 409 and r.json()["code"] == "version_immutable"
    assert (await client.post(f"{api}/cases/{cid}/versions/1/publish", headers=h["teacher"])).status_code == 409

    # новый черновик: только один одновременно
    d2 = await client.post(f"{api}/cases/{cid}/versions", json={}, headers=h["teacher"])
    assert d2.status_code == 201 and d2.json()["version_no"] == 2 and d2.json()["status"] == "draft"
    assert (await client.post(f"{api}/cases/{cid}/versions", json={}, headers=h["teacher"])).status_code == 409
    detail = (await client.get(f"{api}/cases/{cid}", headers=h["teacher"])).json()
    assert detail["published_version_no"] == 1 and [v["status"] for v in detail["versions"]] == ["published", "draft"]


async def test_student_sees_only_published_and_sanitized(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    case = await create(client, api, h["teacher"])
    cid = case["id"]
    assert (await client.get(f"{api}/cases", headers=h["student"])).json()["items"] == []  # нет опубликованных версий
    assert (await client.get(f"{api}/cases/{cid}", headers=h["student"])).status_code == 404
    await client.post(f"{api}/cases/{cid}/versions/1/publish", headers=h["teacher"])

    items = (await client.get(f"{api}/cases", headers=h["student"])).json()["items"]
    assert [i["id"] for i in items] == [cid] and items[0]["disorders"] == []
    v = (await client.get(f"{api}/cases/{cid}/versions/1", headers=h["student"])).json()
    assert v["case_model"]["presenting_problem"] and "triggers" not in str(v["case_model"].keys()) and "hidden_disclosure" not in v["case_model"]
    assert set(v["case_model"]) == {"title", "name", "age", "gender", "occupation", "language", "presenting_problem"}
    assert (await client.patch(f"{api}/cases/{cid}", json={"title": "x"}, headers=h["student"])).status_code == 403
    assert (await client.post(f"{api}/cases", json=case_body(), headers=h["student"])).status_code == 403

    # чужая организация кейс не видит
    other_org = await make_org(container, "Чужой")
    outsider = await make_user(container, "student", other_org)
    assert (await client.get(f"{api}/cases/{cid}", headers=await login(client, outsider))).status_code == 404


async def test_lint_warnings(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    case = await create(client, api, h["teacher"])
    cid = case["id"]
    cur = await client.get(f"{api}/cases/{cid}/versions/1", headers=h["teacher"])
    model = cur.json()["case_model"]
    model["triggers"][0]["keywords"] = ["ab"]
    model["hidden_disclosure"]["trust_threshold"] = 0.99
    model["disorders"].append({"classifier": "DSM-5", "code": "X99", "name": "Unknown", "role": "comorbid"})
    await client.put(f"{api}/cases/{cid}/versions/1", json=model, headers={**h["teacher"], "If-Match": cur.headers["etag"]})
    rep = (await client.post(f"{api}/cases/{cid}/versions/1/validate", headers=h["teacher"])).json()
    codes = {w["code"] for w in rep["warnings"]}
    assert rep["valid"] is True and {"keyword_too_short", "disclosure_unreachable", "unknown_disorder_code"} <= codes


async def test_clone_delete_patch_and_idempotency(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    hdr = {**h["teacher"], "Idempotency-Key": "k-1"}
    a = await client.post(f"{api}/cases", json=case_body(), headers=hdr)
    b = await client.post(f"{api}/cases", json=case_body(), headers=hdr)
    assert a.json()["id"] == b.json()["id"] and b.headers["idempotent-replayed"] == "true"
    cid = a.json()["id"]
    assert len((await client.get(f"{api}/cases", params={"mine": "true"}, headers=h["teacher"])).json()["items"]) == 1

    clone = await client.post(f"{api}/cases/{cid}/clone", headers=h["teacher"])
    assert clone.status_code == 201 and clone.json()["title"].endswith("(копия)") and clone.json()["visibility"] == "private"
    found = (await client.get(f"{api}/cases", params={"q": "копия", "disorder_code": "6B00"}, headers=h["teacher"])).json()["items"]
    assert len(found) == 1

    r = await client.patch(f"{api}/cases/{cid}", json={"archived": True}, headers=h["teacher"])
    assert r.json()["archived"] is True
    assert (await client.get(f"{api}/cases/{cid}", headers=h["student"])).status_code == 404  # архивный скрыт
    assert (await client.delete(f"{api}/cases/{cid}", headers=h["teacher"])).status_code == 204
    assert (await client.get(f"{api}/cases/{cid}", headers=h["teacher"])).status_code == 404


async def test_case_assist(client, api, container, llm):
    org, admin, teacher, student, h = await setup(container, client)
    r = await client.post(f"{api}/case-assist/suggestions", json={"section": "defenses", "context": {"name": "Дима"}}, headers=h["teacher"])
    assert r.status_code == 200 and r.json()["suggestions"][0]["value"] == ["идея 1"]
    llm.fail = True
    r = await client.post(f"{api}/case-assist/suggestions", json={"section": "rules", "context": {}}, headers=h["teacher"])
    assert r.status_code == 502 and r.json()["code"] == "llm_unavailable"
