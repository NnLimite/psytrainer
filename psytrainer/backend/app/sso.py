"""OIDC: вход через SSO вуза (authorization code flow). Данные пользователя берутся из userinfo-эндпоинта провайдера."""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

from .config import SsoProvider
from .errors import ApiError


class OidcClient:
    def authorization_url(self, p: SsoProvider, *, state: str, redirect_uri: str) -> str:
        q = {"response_type": "code", "client_id": p.client_id, "redirect_uri": redirect_uri,
             "scope": p.scope, "state": state}
        return f"{p.authorization_endpoint}?{urlencode(q)}"

    async def fetch_userinfo(self, p: SsoProvider, *, code: str, redirect_uri: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as http:
            try:
                tok = await http.post(p.token_endpoint, data={
                    "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                    "client_id": p.client_id, "client_secret": p.client_secret})
                tok.raise_for_status()
                access = tok.json()["access_token"]
                info = await http.get(p.userinfo_endpoint, headers={"Authorization": f"Bearer {access}"})
                info.raise_for_status()
                return info.json()
            except (httpx.HTTPError, KeyError, ValueError):
                raise ApiError(502, "sso_failed", "Провайдер SSO не вернул данные пользователя")
