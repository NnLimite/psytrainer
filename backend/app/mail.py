from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("psytrainer.mail")


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Dev-реализация: пишет письмо в лог и хранит в outbox (для тестов)."""

    def __init__(self) -> None:
        self.outbox: list[dict[str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append({"to": to, "subject": subject, "body": body})
        log.info("MAIL to=%s subject=%s\n%s", to, subject, body)


class SmtpMailer:
    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str) -> None:
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as s:
            s.starttls()
            if self.user:
                s.login(self.user, self.password or "")
            s.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> None:
        try:
            await asyncio.to_thread(self._send_sync, to, subject, body)
        except Exception:  # письмо не должно ронять запрос
            log.exception("mail send failed to=%s", to)
