from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update

from ..container import Container
from ..deps import C, DB, CurrentUser, auth_rate_limit, check_rate, client_ip
from ..errors import ApiError
from ..models import GroupMember, Invitation, OneTimeToken, Organization, RefreshToken, User
from ..schemas import LoginIn, PasswordForgotIn, PasswordResetIn, RegisterIn, TokenPair
from ..security import create_access_token, hash_password, new_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])
REFRESH_COOKIE = "psy_refresh"
API_PREFIX = "/api/v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite отдаёт даты без таймзоны


def set_refresh_cookie(c: Container, response: Response, raw: str) -> None:
    response.set_cookie(REFRESH_COOKIE, raw, max_age=c.settings.refresh_ttl_days * 86400, httponly=True,
                        secure=c.settings.cookie_secure, samesite="strict", path=f"{API_PREFIX}/auth")


async def start_refresh_chain(c: Container, db, user: User, response: Response,
                              family_id: uuid.UUID | None = None) -> None:
    raw = new_token()
    db.add(RefreshToken(user_id=user.id, family_id=family_id or uuid.uuid4(), token_hash=token_hash(raw),
                        expires_at=_now() + timedelta(days=c.settings.refresh_ttl_days)))
    await db.commit()
    set_refresh_cookie(c, response, raw)


def access_pair(c: Container, user: User) -> TokenPair:
    token, ttl = create_access_token(c.settings.secret_key, user_id=str(user.id), role=user.role,
                                     org_id=str(user.org_id) if user.org_id else None,
                                     ttl_minutes=c.settings.access_ttl_minutes)
    return TokenPair(access_token=token, expires_in=ttl)


async def _apply_invitation(db, inv: Invitation, user: User) -> None:
    user.org_id, user.role = inv.org_id, inv.role
    inv.status = "accepted"
    if inv.group_id is not None:
        db.add(GroupMember(group_id=inv.group_id, user_id=user.id))


async def _pending_invitation(db, email: str, raw_token: str | None = None) -> Invitation | None:
    stmt = select(Invitation).where(Invitation.status == "pending", Invitation.expires_at > _now())
    stmt = stmt.where(Invitation.token_hash == token_hash(raw_token)) if raw_token else stmt.where(Invitation.email == email)
    return (await db.execute(stmt.order_by(Invitation.created_at.desc()))).scalars().first()


@router.post("/register", response_model=TokenPair, status_code=201, dependencies=[auth_rate_limit])
async def register(body: RegisterIn, response: Response, c: C, db: DB) -> TokenPair:
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise ApiError(409, "already_exists", "Пользователь с таким email уже зарегистрирован")
    inv = None
    if body.invitation_token:
        inv = await _pending_invitation(db, email, body.invitation_token)
        if inv is None or inv.email.lower() != email:
            raise ApiError(422, "validation_failed", "Приглашение недействительно или выдано на другой email")
    user = User(id=uuid.uuid4(), email=email, full_name=body.full_name, locale=body.locale,
                password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    if inv is not None:
        await _apply_invitation(db, inv, user)
    await start_refresh_chain(c, db, user, response)
    return access_pair(c, user)


@router.post("/login", response_model=TokenPair, dependencies=[auth_rate_limit])
async def login(body: LoginIn, request: Request, response: Response, c: C, db: DB) -> TokenPair:
    email = body.email.lower()
    await check_rate(c, "login", f"{client_ip(request)}:{email}", c.settings.rate_limit_auth_per_min)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else None, body.password)
    if not ok or user is None or not user.is_active or user.deleted_at is not None:
        raise ApiError(401, "unauthorized", "Неверный email или пароль")
    await start_refresh_chain(c, db, user, response)
    return access_pair(c, user)


@router.post("/refresh", response_model=TokenPair, dependencies=[auth_rate_limit])
async def refresh(request: Request, response: Response, c: C, db: DB) -> TokenPair:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise ApiError(401, "unauthorized", "Нет refresh-токена")
    tok = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw)))).scalar_one_or_none()
    if tok is None:
        raise ApiError(401, "unauthorized", "Недействительный refresh-токен")
    if tok.revoked_at is not None:
        # повторное использование уже ротированного токена: считаем утечкой и отзываем всю цепочку
        await db.execute(update(RefreshToken).where(RefreshToken.family_id == tok.family_id,
                                                    RefreshToken.revoked_at.is_(None)).values(revoked_at=_now()))
        await db.commit()
        raise ApiError(401, "unauthorized", "Refresh-токен уже использован, войдите заново")
    if _aware(tok.expires_at) < _now():
        raise ApiError(401, "token_expired", "Refresh-токен истёк, войдите заново")
    user = await db.get(User, tok.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise ApiError(401, "unauthorized", "Пользователь недоступен")
    tok.revoked_at = _now()
    await start_refresh_chain(c, db, user, response, family_id=tok.family_id)
    return access_pair(c, user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, c: C, db: DB, user: CurrentUser) -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        tok = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw)))).scalar_one_or_none()
        if tok is not None and tok.user_id == user.id:
            await db.execute(update(RefreshToken).where(RefreshToken.family_id == tok.family_id,
                                                        RefreshToken.revoked_at.is_(None)).values(revoked_at=_now()))
            await db.commit()
    out = Response(status_code=204)
    out.delete_cookie(REFRESH_COOKIE, path=f"{API_PREFIX}/auth")
    return out


# ---------- SSO ----------
@router.get("/sso/{provider}/start", status_code=302, dependencies=[auth_rate_limit])
async def sso_start(provider: str, c: C) -> RedirectResponse:
    cfg = c.settings.sso_providers.get(provider)
    if cfg is None:
        raise ApiError(404, "not_found", "SSO-провайдер не настроен")
    state = new_token()
    await c.kv.set(f"psy:sso:{state}", provider, ex=600)
    redirect_uri = f"{c.settings.public_url}{API_PREFIX}/auth/sso/{provider}/callback"
    return RedirectResponse(c.sso.authorization_url(cfg, state=state, redirect_uri=redirect_uri), status_code=302)


@router.get("/sso/{provider}/callback", status_code=302, dependencies=[auth_rate_limit])
async def sso_callback(provider: str, code: str, state: str, c: C, db: DB) -> RedirectResponse:
    cfg = c.settings.sso_providers.get(provider)
    if cfg is None or await c.kv.get(f"psy:sso:{state}") != provider:
        raise ApiError(400, "bad_request", "Недействительный параметр state")
    await c.kv.delete(f"psy:sso:{state}")
    info = await c.sso.fetch_userinfo(cfg, code=code,
                                      redirect_uri=f"{c.settings.public_url}{API_PREFIX}/auth/sso/{provider}/callback")
    email = (info.get("email") or "").lower()
    if not email or info.get("email_verified") is False:
        raise ApiError(403, "forbidden", "Провайдер не подтвердил email")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(id=uuid.uuid4(), email=email, full_name=info.get("name") or email.split("@")[0])
        db.add(user)
        await db.flush()
        inv = await _pending_invitation(db, email)
        if inv is not None:
            await _apply_invitation(db, inv, user)
        else:
            domain = email.split("@")[-1]
            org = (await db.execute(select(Organization).where(Organization.sso_domain == domain))).scalars().first()
            if org is not None:
                user.org_id = org.id
    elif not user.is_active or user.deleted_at is not None:
        raise ApiError(403, "forbidden", "Учётная запись отключена")
    out = RedirectResponse(f"{c.settings.frontend_url}/auth/callback", status_code=302)
    await start_refresh_chain(c, db, user, out)  # access-токен фронтенд получит через /auth/refresh, не через URL
    return out


# ---------- сброс пароля ----------
@router.post("/password/forgot", status_code=202, dependencies=[auth_rate_limit])
async def password_forgot(body: PasswordForgotIn, c: C, db: DB) -> Response:
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if user is not None and user.is_active and user.password_hash and user.deleted_at is None:
        raw = new_token()
        db.add(OneTimeToken(user_id=user.id, purpose="password_reset", token_hash=token_hash(raw),
                            expires_at=_now() + timedelta(hours=1)))
        await db.commit()
        await c.mailer.send(user.email, "Сброс пароля PsyTrainer",
                            f"Для сброса пароля перейдите по ссылке (действует 1 час):\n"
                            f"{c.settings.frontend_url}/reset-password?token={raw}")
    return Response(status_code=202)  # ответ одинаков, существует email или нет


@router.post("/password/reset", status_code=204, dependencies=[auth_rate_limit])
async def password_reset(body: PasswordResetIn, db: DB) -> Response:
    tok = (await db.execute(select(OneTimeToken).where(
        OneTimeToken.token_hash == token_hash(body.token), OneTimeToken.purpose == "password_reset"))).scalar_one_or_none()
    if tok is None or tok.used_at is not None or _aware(tok.expires_at) < _now():
        raise ApiError(422, "validation_failed", "Ссылка недействительна или устарела")
    user = await db.get(User, tok.user_id)
    user.password_hash = hash_password(body.new_password)
    tok.used_at = _now()
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id,
                                                RefreshToken.revoked_at.is_(None)).values(revoked_at=_now()))
    await db.commit()
    return Response(status_code=204)
