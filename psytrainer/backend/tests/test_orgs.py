from __future__ import annotations

import re

from tests.conftest import PASSWORD, login, make_org, make_user


async def setup(container, client):
    org = await make_org(container)
    admin = await make_user(container, "org_admin", org)
    teacher = await make_user(container, "teacher", org)
    student = await make_user(container, "student", org)
    return org, admin, teacher, student, {
        "admin": await login(client, admin), "teacher": await login(client, teacher), "student": await login(client, student)}


async def test_org_read_and_patch_permissions(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    assert (await client.get(f"{api}/organizations/{org.id}", headers=h["teacher"])).status_code == 200
    assert (await client.get(f"{api}/organizations/{org.id}", headers=h["student"])).status_code == 403
    assert (await client.patch(f"{api}/organizations/{org.id}", json={"name": "Новое"}, headers=h["teacher"])).status_code == 403
    r = await client.patch(f"{api}/organizations/{org.id}", json={"name": "Новое"}, headers=h["admin"])
    assert r.json()["name"] == "Новое"
    other = await make_org(container, "Чужой")
    assert (await client.get(f"{api}/organizations/{other.id}", headers=h["admin"])).status_code == 404


async def test_invitation_registration_flow(client, api, container, make_client):
    org, admin, teacher, student, h = await setup(container, client)
    g = (await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Группа 1"}, headers=h["teacher"])).json()
    r = await client.post(f"{api}/organizations/{org.id}/invitations",
                          json={"email": "Newbie@Example.com", "role": "student", "group_id": g["id"]}, headers=h["admin"])
    assert r.status_code == 201 and r.json()["status"] == "pending"
    dup = await client.post(f"{api}/organizations/{org.id}/invitations", json={"email": "newbie@example.com", "role": "student"}, headers=h["admin"])
    assert dup.status_code == 409
    token = re.search(r"invitation=([\w-]+)", container.mailer.outbox[-1]["body"]).group(1)

    c2 = make_client()
    bad = await c2.post(f"{api}/auth/register", json={"email": "other@example.com", "password": PASSWORD, "full_name": "X", "invitation_token": token})
    assert bad.status_code == 422  # приглашение выдано на другой email
    ok = await c2.post(f"{api}/auth/register", json={"email": "newbie@example.com", "password": PASSWORD, "full_name": "Новичок", "invitation_token": token})
    assert ok.status_code == 201
    me = (await c2.get(f"{api}/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})).json()
    assert me["org_id"] == str(org.id) and me["role"] == "student"
    members = (await client.get(f"{api}/groups/{g['id']}/members", headers=h["teacher"])).json()["items"]
    assert [m["email"] for m in members] == ["newbie@example.com"]
    assert (await client.post(f"{api}/organizations/{org.id}/invitations", json={"email": "x@example.com", "role": "student"}, headers=h["teacher"])).status_code == 403


async def test_groups_access_and_membership(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    g = (await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Группа А"}, headers=h["teacher"])).json()
    assert g["teacher_id"] == str(teacher.id) and g["members_count"] == 0

    assert (await client.get(f"{api}/groups/{g['id']}", headers=h["student"])).status_code == 404  # ещё не в группе
    r = await client.put(f"{api}/groups/{g['id']}/members/{student.id}", headers=h["teacher"])
    assert r.status_code == 200
    assert (await client.put(f"{api}/groups/{g['id']}/members/{student.id}", headers=h["teacher"])).status_code == 200  # идемпотентно
    assert (await client.get(f"{api}/groups/{g['id']}", headers=h["student"])).json()["members_count"] == 1
    mine = (await client.get(f"{api}/groups", headers=h["student"])).json()["items"]
    assert [x["id"] for x in mine] == [g["id"]]

    other_teacher = await make_user(container, "teacher", org)
    ho = await login(client, other_teacher)
    assert (await client.get(f"{api}/groups/{g['id']}", headers=ho)).status_code == 404
    assert (await client.get(f"{api}/groups", headers=ho)).json()["items"] == []
    # преподаватель не может передать группу, администратор может
    assert (await client.patch(f"{api}/groups/{g['id']}", json={"teacher_id": str(other_teacher.id)}, headers=h["teacher"])).status_code == 403
    r = await client.patch(f"{api}/groups/{g['id']}", json={"teacher_id": str(other_teacher.id)}, headers=h["admin"])
    assert r.json()["teacher_id"] == str(other_teacher.id)

    assert (await client.delete(f"{api}/groups/{g['id']}/members/{student.id}", headers=ho)).status_code == 204
    assert (await client.delete(f"{api}/groups/{g['id']}", headers=h["teacher"])).status_code == 403
    assert (await client.delete(f"{api}/groups/{g['id']}", headers=h["admin"])).status_code == 204


async def test_members_roles_and_last_admin_protection(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    lst = (await client.get(f"{api}/organizations/{org.id}/members", params={"role": "student"}, headers=h["teacher"])).json()
    assert [m["user_id"] for m in lst["items"]] == [str(student.id)]
    r = await client.patch(f"{api}/organizations/{org.id}/members/{admin.id}", json={"role": "teacher"}, headers=h["admin"])
    assert r.status_code == 409 and r.json()["code"] == "last_admin"
    assert (await client.delete(f"{api}/organizations/{org.id}/members/{admin.id}", headers=h["admin"])).status_code == 409

    await client.post(f"{api}/groups", json={"org_id": str(org.id), "name": "Г"}, headers=h["teacher"])
    r = await client.delete(f"{api}/organizations/{org.id}/members/{teacher.id}", headers=h["admin"])
    assert r.status_code == 409 and r.json()["code"] == "teacher_has_groups"
    assert (await client.delete(f"{api}/organizations/{org.id}/members/{student.id}", headers=h["admin"])).status_code == 204
    me = (await client.get(f"{api}/me", headers=await login(client, student))).json()
    assert me["org_id"] is None


async def test_disorders_reference(client, api, container):
    org, admin, teacher, student, h = await setup(container, client)
    r = await client.get(f"{api}/disorders", params={"classifier": "ICD-11", "q": "anxiety"}, headers=h["student"])
    codes = {d["code"] for d in r.json()["items"]}
    assert {"6B00", "6B04"} <= codes
    p1 = (await client.get(f"{api}/disorders", params={"limit": 3}, headers=h["student"])).json()
    p2 = (await client.get(f"{api}/disorders", params={"limit": 3, "cursor": p1["next_cursor"]}, headers=h["student"])).json()
    assert len(p1["items"]) == 3 and {d["id"] for d in p1["items"]}.isdisjoint({d["id"] for d in p2["items"]})
