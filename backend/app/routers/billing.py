from __future__ import annotations

import json
import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..deps import C, DB, CurrentUser, Idem, current_user, default_rate_limit
from ..errors import ApiError
from ..models import PaymentEvent, Subscription
from ..payments import verify_signature
from ..schemas import CheckoutIn, CheckoutOut

router = APIRouter(tags=["Billing"])
log = logging.getLogger("psytrainer.billing")


@router.post("/billing/checkout", response_model=CheckoutOut, dependencies=[Depends(current_user), default_rate_limit])
async def checkout(body: CheckoutIn, c: C, user: CurrentUser, idem: Idem):
    if (replay := await idem.replay()) is not None:
        return replay
    if body.plan not in c.settings.plans:
        raise ApiError(422, "validation_failed", f"Неизвестный план. Доступны: {', '.join(c.settings.plans)}")
    org_plan = body.plan.startswith("org_")
    if org_plan and (user.role != "org_admin" or user.org_id is None):
        raise ApiError(403, "forbidden", "Подписку организации оформляет её администратор")
    ref = uuid.uuid4().hex
    owner = {"owner_type": "organization", "org_id": str(user.org_id)} if org_plan else {
        "owner_type": "user", "user_id": str(user.id)}
    await c.kv.set(f"psy:pay:{ref}", json.dumps({**owner, "plan": body.plan}), ex=7 * 86400)
    url = await c.payments.create_checkout(ref=ref, plan=body.plan, success_url=body.success_url, cancel_url=body.cancel_url)
    out = CheckoutOut(checkout_url=url)
    await idem.store(200, jsonable_encoder(out))
    return out


@router.post("/webhooks/payments/{provider}", status_code=204)
async def payment_webhook(provider: str, request: Request, c: C, db: DB) -> Response:
    """Подпись HMAC-SHA256 в заголовке X-Signature. Повторы безопасны: события дедуплицируются по id."""
    if provider != c.payments.name:
        raise ApiError(404, "not_found", "Неизвестный платёжный провайдер")
    raw = await request.body()
    if not verify_signature(c.settings.payment_webhook_secret, raw, request.headers.get("X-Signature")):
        raise ApiError(401, "unauthorized", "Неверная подпись вебхука")
    try:
        event = json.loads(raw)
        event_id, etype, ref = str(event["id"]), event["type"], event.get("data", {}).get("ref")
    except (ValueError, KeyError, TypeError):
        raise ApiError(400, "bad_request", "Некорректное тело вебхука")
    db.add(PaymentEvent(provider=provider, event_id=event_id, payload=event))
    try:
        await db.commit()
    except IntegrityError:  # событие уже обработано
        await db.rollback()
        return Response(status_code=204)
    info = json.loads(await c.kv.get(f"psy:pay:{ref}") or "null") if ref else None
    if info is None:
        log.warning("payment event %s for unknown ref %s", event_id, ref)
        return Response(status_code=204)
    stmt = select(Subscription).where(Subscription.owner_type == info["owner_type"]).order_by(Subscription.created_at.desc())
    stmt = stmt.where(Subscription.org_id == uuid.UUID(info["org_id"])) if "org_id" in info else stmt.where(
        Subscription.user_id == uuid.UUID(info["user_id"]))
    sub = (await db.execute(stmt)).scalars().first()
    if etype == "payment.succeeded":
        days = c.settings.plans[info["plan"]]
        today = date.today()
        base = sub.valid_until if sub is not None and sub.valid_until and sub.valid_until > today else today
        if sub is None:
            sub = Subscription(owner_type=info["owner_type"], plan=info["plan"],
                               user_id=uuid.UUID(info["user_id"]) if "user_id" in info else None,
                               org_id=uuid.UUID(info["org_id"]) if "org_id" in info else None)
            db.add(sub)
        sub.plan, sub.status, sub.valid_until = info["plan"], "active", base + timedelta(days=days)
    elif sub is not None and etype == "payment.failed":
        sub.status = "past_due"
    elif sub is not None and etype == "subscription.canceled":
        sub.status = "canceled"
    await db.commit()
    return Response(status_code=204)
