"""Платежи: абстракция провайдера и проверка подписи вебхука (HMAC-SHA256). Реальные провайдеры подключаются реализацией протокола."""
from __future__ import annotations

import hashlib
import hmac
from typing import Protocol


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, *, ref: str, plan: str, success_url: str, cancel_url: str) -> str: ...


class StubPayments:
    """Заглушка для dev и тестов: возвращает ссылку на несуществующую страницу оплаты."""

    name = "stub"

    def __init__(self, public_url: str) -> None:
        self.public_url = public_url

    async def create_checkout(self, *, ref: str, plan: str, success_url: str, cancel_url: str) -> str:
        return f"{self.public_url}/pay/stub/{ref}?plan={plan}"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    return bool(signature) and hmac.compare_digest(sign(secret, body), signature)
