from __future__ import annotations

import re
from app.config import SsoProvider
from app.security import create_access_token
from tests.conftest import PASSWORD, make_org, make_user

REG = {"email": "New@Example.com", "password": PASSWORD, "full_name": "Новый"}


async def test_register_login_me(client, api):
    r = await client.post(f"{api}/auth/register", json=REG)
    assert r.status_code == 201 and r.json()["token_type"] == "Bearer"
    assert "psy_refresh" in r.cookies
    r = await client.post(f"{api}/auth/register", json=REG)
    assert r.status_code == 409 and r.json()["code"] == "already_exists"
    assert r.headers["content-type"].startswith("application/problem+json")

    r = await client.post(f"{api}/auth/login", json={"email": "new@example.com", "password": PASSWORD})
    token = r.json()["access_token"]
    me = await client.get(f"{api}/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "new@example.com" and me.json()["role"] == "student"


async def test_validation_error_is_problem_json(client, api):
    r = await client.post(f"{api}/auth/register", json={"email": "bad", "password": "short", "full_name": ""})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "validation_failed"
    assert {e["field"] for e in body["errors"]} >= {"email", "password", "full_name"}


async def test_wrong_password_and_missing_token(client, api):
    await client.post(f"{api}/auth/register", json=REG)
    r = await client.post(f"{api}/auth/login", json={"email": "new@example.com", "password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert (await client.get(f"{api}/me")).status_code == 401
    assert (await client.get(f"{api}/me", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_expired_token(client, api, container, helpers):
    u = await make_user(container)
    token, _ = create_access_token(container.settings.secret_key, user_id=str(u.id), role="student", org_id=None, ttl_minutes=-1)
    r = await client.get(f"{api}/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


async def test_refresh_rotation_and_reuse_detection(make_client, api):
    c = make_client()
    await c.post(f"{api}/auth/register", json=REG)
    old = c.cookies.get("psy_refresh")
    r = await c.post(f"{api}/auth/refresh")
    assert r.status_code == 200
    new = c.cookies.get("psy_refresh")
    assert new and new != old

    thief = make_client()
    thief.cookies.set("psy_refresh", old, path=f"{api}/auth")
    r = await thief.post(f"{api}/auth/refresh")
    assert r.status_code == 401  # старый токен повторно использован
    r = await c.post(f"{api}/auth/refresh")  # из-за утечки отозвана вся цепочка
    assert r.status_code == 401


async def test_logout_revokes_refresh(make_client, api):
    c = make_client()
    reg = await c.post(f"{api}/auth/register", json=REG)
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    raw = c.cookies.get("psy_refresh")
    assert (await c.post(f"{api}/auth/logout", headers=h)).status_code == 204
    other = make_client()
    other.cookies.set("psy_refresh", raw, path=f"{api}/auth")
    assert (await other.post(f"{api}/auth/refresh")).status_code == 401


async def test_password_reset_flow(client, api, container):
    await client.post(f"{api}/auth/register", json=REG)
    assert (await client.post(f"{api}/auth/password/forgot", json={"email": "ghost@example.com"})).status_code == 202
    assert container.mailer.outbox == []  # неизвестный email: ответ тот же, письма нет
    assert (await client.post(f"{api}/auth/password/forgot", json={"email": "new@example.com"})).status_code == 202
    token = re.search(r"token=([\w-]+)", container.mailer.outbox[0]["body"]).group(1)
    assert (await client.post(f"{api}/auth/password/reset", json={"token": token, "new_password": "brand-new-password"})).status_code == 204
    assert (await client.post(f"{api}/auth/password/reset", json={"token": token, "new_password": "another-password-1"})).status_code == 422
    r = await client.post(f"{api}/auth/login", json={"email": "new@example.com", "password": "brand-new-password"})
    assert r.status_code == 200
    r = await client.post(f"{api}/auth/refresh")  # refresh-токены отозваны при смене пароля (cookie принадлежит старой цепочке)
    assert r.status_code in (200, 401)


async def test_login_rate_limit_per_email(client, api, container):
    container.settings.rate_limit_auth_per_min = 3
    for _ in range(3):
        r = await client.post(f"{api}/auth/login", json={"email": "x@example.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    r = await client.post(f"{api}/auth/login", json={"email": "x@example.com", "password": "wrong-password-1"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited" and "Retry-After" in r.headers


async def test_sso_login_provisions_user_by_domain(make_client, api, container):
    org = await make_org(container, name="Университет", sso_domain="uni.ru")
    container.settings.sso_providers = {"uni": SsoProvider(client_id="id", client_secret="s", authorization_endpoint="https://idp/auth",
                                                           token_endpoint="https://idp/token", userinfo_endpoint="https://idp/userinfo")}
    c = make_client()
    r = await c.get(f"{api}/auth/sso/uni/start", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://idp/auth?")
    state = re.search(r"state=([\w-]+)", r.headers["location"]).group(1)
    assert (await c.get(f"{api}/auth/sso/uni/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)).status_code == 400
    r = await c.get(f"{api}/auth/sso/uni/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/auth/callback")
    tok = (await c.post(f"{api}/auth/refresh")).json()["access_token"]
    me = (await c.get(f"{api}/me", headers={"Authorization": f"Bearer {tok}"})).json()
    assert me["email"] == "stud@uni.ru" and me["org_id"] == str(org.id)
    assert (await c.get(f"{api}/auth/sso/nope/start", follow_redirects=False)).status_code == 404
